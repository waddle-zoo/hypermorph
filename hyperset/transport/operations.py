"""The three agent-facing v0 operations, decoded once (hy-oih, hy-x7f),
`docs/v0-foundation.md` section 7.

HTTP and MCP are adapters over this module. They decode a transport-specific
request into a params mapping, call `run_operation`, and serialize the dict
they get back. Neither may re-implement, re-shape, or re-order anything, so
bundle parity between the transports is structural rather than a promise a
test has to keep re-checking.

`ContextBundle` and `PlanValidation` remain the contract for answers; the
catalog is a listing of what exists, not an answer, and carries no governed
meaning (see `hyperset/bundle/catalog.py`). This module names the
operations, checks their parameters, and turns a bad request into an error
that says what to change.

Retrieval is directed, not interpreted: `resolve_analytics_context` takes a
structured `directive` and no longer accepts a bare question to route by
wording (GitHub #70). A request that names nothing to retrieve is refused
here, with the catalog operation named in the recovery, because the deleted
alternative was to guess.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import traceback
from collections import Counter
from copy import deepcopy

from hyperset.bundle import (
    CATALOG_DEFAULT_LIMIT,
    CATALOG_INNER_LIMIT,
    CATALOG_MAX_LIMIT,
    CATALOG_MIN_LIMIT,
    CATALOG_MIN_OFFSET,
    CATALOG_OPERATION,
    PLAN_FIRST,
    SCHEMA_VERSION,
    AnalyticsPlan,
    CatalogBoundError,
    ContextBundle,
    ContextCatalog,
    ContextDirective,
    PlanValidation,
    list_context_catalog,
    resolve_analytics_context,
    validate_analytics_plan,
)
from hyperset.bundle.expansion import MIN_CONTEXT_BUDGET, expand_analytics_context, expand_from_root
from hyperset.bundle.schema import NO_MATCH, OBSERVED_ONLY
from hyperset.candidates.catalog import CANDIDATE_LIMIT
from hyperset.candidates.service import (
    DISCOVER_OPERATION,
    DiscoveryResult,
    discover_analytics_context,
)
from hyperset.context.schema import DRAFT_DEFINITION_FIELDS
from hyperset.db.models import ANSWER_FEEDBACK_OUTCOMES, CITATION_DECISIONS
from hyperset.knowledge import search_knowledge as _search_knowledge
from hyperset.observability.correlation import current_correlation_id, new_correlation_id
from hyperset.observability.interaction import current_trace_context, opaque_token
from hyperset.repositories.errors import NotFoundError, OptimisticConcurrencyError
from hyperset.repositories.postgres import (
    REVIEW_TASK_STATUSES,
    PostgresAdminAuditRepository,
    PostgresAnswerCitationRepository,
    PostgresAnswerFeedbackRepository,
    PostgresCitationDecisionRepository,
    PostgresContextRepository,
    PostgresGovernedContextRepository,
    PostgresInteractionTraceRepository,
    PostgresResolveMissRepository,
    PostgresReviewRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.review.meaning_diff import diff_definition, merge_definitions
from hyperset.security.authz import (
    CONFIGURE,
    READ,
    REVIEW,
    Resource,
    authorize,
    authz_enabled,
    roles_for,
)
from hyperset.security.pii import guard_text
from hyperset.security.redaction import redact_free_text_userinfo, redact_pointer
from hyperset.security.reviewer_allowlist import approves as reviewer_allowlist_approves
from hyperset.security.reviewer_allowlist import reviewer_allowlist

_log = logging.getLogger("hyperset.transport.operations")

CATALOG = CATALOG_OPERATION
RESOLVE = "resolve_analytics_context"
VALIDATE = "validate_analytics_plan"
DISCOVER = DISCOVER_OPERATION
# Governed progressive expansion (#230 slice 4, hy-fgga): bounded NAVIGATION over the
# governed `contains` graph from one resolved domain. Served on both transports but --
# like DISCOVER and the REVIEW ops -- NOT in RESOLVE_PATH_OPERATIONS, so it moves no
# `tools_hash`. It is navigation, not a governed answer: its result carries `result_kind:
# "navigation"` and no authority/instructions/evidence, and it composes nothing.
EXPAND = "expand_analytics_context"
# Read-only, non-authoritative search over the CONFIGURED/governed sources (grep-MVP
# hy-r0szz; semantic ranking hy-0unvk). Served on both transports but -- like DISCOVER,
# EXPAND, and the REVIEW
# ops -- NOT in RESOLVE_PATH_OPERATIONS, so it moves no `tools_hash`; it returns its own
# hit envelope and adds no key to the ContextBundle. Semantic hits add `signal`, which moves
# SCHEMA_VERSION under ADR 0018. It writes, proposes, approves, and resolves nothing, and it
# is ACL-fail-closed per source (a caller
# denied a source gets zero hits from it, reusing security.authz).
SEARCH_KNOWLEDGE = "search_knowledge"
# Append-only interaction feedback and its bounded read path (hy-8f2r4). Both
# are assist/audit class, served on HTTP + MCP but deliberately absent from
# RESOLVE_PATH_OPERATIONS: they can improve a later proposal, never determine
# governed meaning or authorize one.
RECORD_ANSWER_FEEDBACK = "record_answer_feedback"
LOOKUP_ANSWER_FEEDBACK = "lookup_answer_feedback"
# The flywheel Review operations (hy-jis1), served first-class on BOTH
# transports so an agent and the /review page reach one shape. They read and
# mutate the step-4 UNAPPROVED assist draft, or propose it as a Git PR and stop
# -- proposal-only and PII-guarded (ADR 0012, ADR 0025). None approves, merges,
# writes a governed row, or runs SQL.
LIST_REVIEW_TASKS = "list_review_tasks"
GET_REVIEW_TASK = "get_review_task"
EDIT_REVIEW_DRAFT = "edit_review_draft"
REFINE_REVIEW_DRAFT = "refine_review_draft"
PROPOSE_REVIEW_TO_GIT = "propose_review_to_git"
SET_REVIEW_ASSIGNEE = "set_review_assignee"
REVIEW_OPERATIONS = (
    LIST_REVIEW_TASKS,
    GET_REVIEW_TASK,
    EDIT_REVIEW_DRAFT,
    REFINE_REVIEW_DRAFT,
    PROPOSE_REVIEW_TO_GIT,
    SET_REVIEW_ASSIGNEE,
)
# In the order an agent uses them: discover where to look for an ordinary
# question, find out what exists, retrieve what it named, check what it plans
# to do with it. DISCOVER is assist-class and served, but it is NOT in the
# governed benchmark tool surface (hyperset.planner.loop.tool_specs is an
# explicit resolve-path allowlist), so it never moves tools_hash. The REVIEW
# operations are served here too but are likewise NOT in RESOLVE_PATH_OPERATIONS,
# so tools_hash is unaffected; they are proposal-only and PII-guarded (ADR 0025).
OPERATIONS = (
    CATALOG,
    DISCOVER,
    RESOLVE,
    VALIDATE,
    EXPAND,
    SEARCH_KNOWLEDGE,
    RECORD_ANSWER_FEEDBACK,
    LOOKUP_ANSWER_FEEDBACK,
    *REVIEW_OPERATIONS,
)

# Re-gather the observed evidence for a review task (hy-to8m, V1 gap Reviewer/3). A review-
# AUTHORING mutation like edit/refine -- it replaces the task's assist `gathered_sources` by
# re-running the DETERMINISTIC step-2 gather -- but served ONLY as a bespoke HTTP playground
# route, deliberately NOT a member of OPERATIONS. Adding it to OPERATIONS would auto-publish
# an MCP tool and GROW the ADR-0025 trust-surface enumeration; keeping it off OPERATIONS keeps
# it off MCP/ROUTES and moves no tools_hash. It still carries the REVIEW action below, so the
# HTTP handler gates it exactly like the review-authoring ops.
REQUEST_REVIEW_EVIDENCE = "request_review_evidence"

# Record a human's include/exclude/approve/reject decision on a citation (hy-cpkvu, epic
# slice 3). Like REQUEST_REVIEW_EVIDENCE it is a review-surface mutation served ONLY as a
# bespoke HTTP route, deliberately NOT in OPERATIONS -- so it publishes no MCP tool and moves
# no tools_hash. It writes only the internal citation_decisions AUDIT store: no governed row,
# no resolve, no SQL, and it approves/merges nothing (ADR 0012). It carries the REVIEW action
# below, so its handler and service gate it exactly like the review-authoring ops.
DECIDE_CITATION = "decide_citation"

# Open a PROPOSAL-ONLY write-back review task from selected search hits + a proposed
# context change (hy-27nl6, epic slice 4 CAPSTONE). Like DECIDE_CITATION it is a
# review-surface mutation served ONLY as a bespoke HTTP route, deliberately NOT in
# OPERATIONS -- so it publishes no MCP tool and moves no tools_hash. It CREATES a ReviewTask
# (status open) whose proposal_payload carries the proposed change + the originating hit
# citations + correlation, routed by domain to the write-back target. It does NOT approve,
# write a governed version, or merge (ADR 0012): a human still drives propose_review_to_git
# -> PR. It carries the REVIEW action below, so its handler and service gate it exactly like
# the review-authoring ops.
PROPOSE_CONTEXT_FROM_SEARCH = "propose_context_from_search"

# The authz ACTION each operation requires (hy-dq0r). Everything is a READ except the
# review-AUTHORING ops -- editing a draft, re-running the authoring agent, opening the
# proposal PR, and assigning an owner -- which require the REVIEW action, so a read-only
# `explorer` is denied them at the gate while a `reviewer` is not. Listing/getting review
# tasks stay READs: seeing the queue is not authoring. Absent from this map (any governed
# read) defaults to READ. Authoring here is still PROPOSAL-ONLY and approves/merges
# nothing (ADR-0012); this gate decides WHO may propose or assign, not that either is an
# approval. Assignment is task METADATA (who should work the gap), never a grant.
OPERATION_ACTIONS: dict[str, str] = {
    # Feedback is an audit append/read for the connected agent. It requires the
    # same governed READ grant as the trace it references; it confers no REVIEW
    # authority and cannot advance a task.
    RECORD_ANSWER_FEEDBACK: READ,
    LOOKUP_ANSWER_FEEDBACK: READ,
    EDIT_REVIEW_DRAFT: REVIEW,
    REFINE_REVIEW_DRAFT: REVIEW,
    PROPOSE_REVIEW_TO_GIT: REVIEW,
    SET_REVIEW_ASSIGNEE: REVIEW,
    # Re-gathering a task's evidence is authoring, not reading -- gated REVIEW like the rest.
    # This name is not in OPERATIONS (HTTP-only, off MCP); its handler calls the gate directly.
    REQUEST_REVIEW_EVIDENCE: REVIEW,
    # Recording a human decision on a citation is a review-surface action, not a read.
    # HTTP-only (not in OPERATIONS); its handler and service both call the REVIEW gate.
    DECIDE_CITATION: REVIEW,
    # Opening a proposal-only write-back task from search hits is a review-surface
    # authoring action, not a read. HTTP-only (not in OPERATIONS); handler + service gate.
    PROPOSE_CONTEXT_FROM_SEARCH: REVIEW,
}

# WHY A REQUEST COULD NOT BE ANSWERED, as a vocabulary rather than as strings
# written at each raise site (hy-y633). These cross the wire: both transports
# serialize `OperationError.to_dict()` to the client, so a caller branches on
# `code` exactly as it branches on a `resolution.warnings` code -- and that
# vocabulary has had a registry, an enforcement point and a documentation test
# since hy-6ae while this one had none of the three.
#
# Split by where they can be raised, because the difference is part of the
# contract -- and the split has a second edge. `HTTP_ERROR_CODES` are failures
# of the HTTP envelope, so an MCP client never sees them. `UNKNOWN_OPERATION`
# goes the other way: `run_operation` raises it, and NEITHER transport
# delivers it, because both check the operation name first -- HTTP answers 404
# `unknown_route` from `ROUTES`, MCP answers JSON-RPC -32602. It reaches an
# in-process caller only, such as the planner's executor. Section 7 says the
# same, and `tests/unit/transport/test_delivered_error_codes.py` asserts it
# against what the wires actually deliver.
UNKNOWN_OPERATION = "unknown_operation"
INVALID_PARAMS = "invalid_params"
UNKNOWN_PARAMETER = "unknown_parameter"
DIRECTIVE_REQUIRED = "directive_required"
# The authorization gate's denial (hy-ac2x, ADR-0030). A FIXED, non-disclosing
# code: an unauthorized caller gets `unauthorized` whether the resource it named
# exists or not, so the denial leaks no existence signal. Delivered by both
# transports only when `HYPERSET_AUTHZ_ENABLED` is on; off by default, so a caller
# never sees it in today's unauthenticated deployment.
UNAUTHORIZED = "unauthorized"
# The one error code that is the server's fault rather than the request's.
INTERNAL_ERROR = "internal_error"

INVALID_JSON = "invalid_json"
INVALID_REQUEST = "invalid_request"
REQUEST_TOO_LARGE = "request_too_large"
UNKNOWN_ROUTE = "unknown_route"
METHOD_NOT_ALLOWED = "method_not_allowed"

OPERATION_ERROR_CODES = (
    UNKNOWN_OPERATION,
    INVALID_PARAMS,
    UNKNOWN_PARAMETER,
    DIRECTIVE_REQUIRED,
    UNAUTHORIZED,
    INTERNAL_ERROR,
)
"""What either transport can return. An MCP client sees only these."""

HTTP_ERROR_CODES = (
    INVALID_JSON,
    INVALID_REQUEST,
    REQUEST_TOO_LARGE,
    UNKNOWN_ROUTE,
    METHOD_NOT_ALLOWED,
)
"""Failures of the HTTP envelope, before any operation is reached."""

ERROR_CODES = OPERATION_ERROR_CODES + HTTP_ERROR_CODES


class OperationError(Exception):
    """A request that cannot be answered as asked.

    Carries recovery instructions because an agent that is only told
    "invalid" has nothing to change on its next call.
    """

    def __init__(self, code: str, message: str, *, recovery: str) -> None:
        # THE RULE, which retires the choice rather than answering this
        # instance of it: the check goes wherever construction cannot be
        # bypassed. A class means its constructor; a plain dict means the
        # factory that makes it. `warning()` is a factory only because a
        # warning has no class to own the check -- a factory HERE would not be
        # a gate at all, since `OperationError(...)` stays constructible and an
        # invented code would walk straight past it. So there are two
        # spellings of one rule and no precedent to litigate.
        #
        # What changes for a caller: a code outside the vocabulary is now a
        # `ValueError` at construction, which surfaces as a 500 rather than as
        # a wrong code on the wire. Unreachable today -- all 21 construction
        # sites pass a named constant and none builds a string.
        if code not in ERROR_CODES:
            raise ValueError(f"unknown operation error code {code!r}; add it to ERROR_CODES first")
        super().__init__(message)
        self.code = code
        self.message = message
        self.recovery = recovery

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "recovery": self.recovery}}


# The retrieval parameters. `validate_analytics_plan` accepts them too: a
# plan check re-runs the same retrieval to get the bundle back.
_RESOLVE_PARAMS = ("query", "directive")
_DIRECTIVE_KEYS = ("domains", "asset_refs", "concepts", "max_hops", "context_budget")
_PLAN_PARAMS = ("bundle_id", "source_refs", "fields", "joins", "filters", "grain", "checks")
_CATALOG_PARAMS = ("limit", "offset")
_DISCOVER_PARAMS = ("query", "limit")
_EXPAND_PARAMS = (
    "query",
    "domain",
    "concepts",
    "from_root",
    "max_hops",
    "max_components",
    "context_budget",
)
_SEARCH_KNOWLEDGE_PARAMS = ("query", "sources", "filters", "mode", "limit", "intent")
_RECORD_FEEDBACK_PARAMS = ("outcome", "bundle_id", "source_ref", "review_task_id", "notes")
_LOOKUP_FEEDBACK_PARAMS = (
    "session_id",
    "correlation_id",
    "source_ref",
    "review_task_id",
    "limit",
)
_LIST_REVIEW_PARAMS = ("status",)
_GET_REVIEW_PARAMS = ("task_id",)
_EDIT_REVIEW_PARAMS = ("task_id", "definition")
_REFINE_REVIEW_PARAMS = ("task_id", "feedback")
_PROPOSE_REVIEW_PARAMS = ("task_id",)
_SET_ASSIGNEE_PARAMS = ("task_id", "assigned", "assignee")
_REQUEST_EVIDENCE_PARAMS = ("task_id",)
_DECIDE_CITATION_PARAMS = (
    "decision",
    "citation_ref",
    "source_ref",
    "review_task_id",
    "correlation_id",
    "notes",
)
_PROPOSE_FROM_SEARCH_PARAMS = (
    "domain",
    "definition",
    "hits",
    "session_id",
    "correlation_id",
    "notes",
)

# The edit_review_draft `definition` schema, GENERATED from the same constant
# that enforces the draft (hy-gh-281 item 6): a caller can construct a valid
# draft from the tool schema without reading `hyperset/context/schema.py`. The
# KEY set is derived from `DRAFT_DEFINITION_FIELDS`, so the served schema and
# `validate_definition_draft`'s accepted set cannot drift
# (`test_the_edit_draft_schema_documents_every_draft_field` binds them). Each
# value carries a real per-field type/shape mirroring the parse helpers the
# validator reuses (`_definitions`, `_approved_sources`, `_fields`, `_joins`,
# `_string_list`), plus `additionalProperties: False` on every object so an
# unknown sub-key is schema-invalid, the same failure `_reject_unknown` produces
# on the Git-manifest parse.
#
# ONE deliberate lag: `_approved_sources` also recognizes a per-source `facets`
# sub-key (`facets.grain`, hy-gh-284 slice 1), which this DRAFT schema does not
# expose. That is intentional -- surfacing facets is a later, SCHEMA_VERSION-
# moving #284 bead -- and it is safe: a draft carrying `facets` is schema-invalid
# here (`additionalProperties: False`) and fail-closes at the MCP boundary before
# it reaches the validator, so the draft surface never widens ahead of the parse.
#
# The one thing the schema CANNOT express is the validator's CROSS-FIELD rules
# (`_collect_refs`, `_check_ref_conflicts`: a field reading a non-approved source,
# a ref both approved and prohibited). Those are enforced on submit and named in
# the error, and the top-level description says so -- a schema-valid draft is
# structurally acceptable, and the remaining rejections are relational, not shape.
_BI_OVERRIDE_SCHEMA = {
    "type": "object",
    "description": "Optional Superset-dataset alias for the ref: {ref, reason}.",
    "properties": {"ref": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["ref", "reason"],
    "additionalProperties": False,
}
_DRAFT_FIELD_SCHEMAS = {
    "definitions": {
        "type": "array",
        "description": "Governed term definitions; at least one is required.",
        "minItems": 1,
        "items": {
            "type": "object",
            "properties": {"term": {"type": "string"}, "statement": {"type": "string"}},
            "required": ["term", "statement"],
            "additionalProperties": False,
        },
    },
    "approved_sources": {
        "type": "array",
        "description": "Sources the domain approves; a plan may read only these.",
        "items": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "role": {"type": "string"},
                "reason": {"type": "string"},
                "bi_override": _BI_OVERRIDE_SCHEMA,
            },
            "required": ["ref", "role"],
            "additionalProperties": False,
        },
    },
    "prohibited_sources": {
        "type": "array",
        "description": "Sources the domain forbids; each needs a stated reason.",
        "items": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "reason": {"type": "string"},
                "bi_override": _BI_OVERRIDE_SCHEMA,
            },
            "required": ["ref", "reason"],
            "additionalProperties": False,
        },
    },
    "fields": {
        "type": "array",
        "description": "Governed field definitions.",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "source_ref": {"type": "string"},
                "expression": {"type": "string"},
            },
            "required": ["name", "source_ref", "expression"],
            "additionalProperties": False,
        },
    },
    "joins": {
        "type": "array",
        "description": "Approved joins; a plan may join only through these.",
        "items": {
            "type": "object",
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
                "type": {"type": "string"},
            },
            "required": ["from", "to", "type"],
            "additionalProperties": False,
        },
    },
    "filters": {
        "type": "array",
        "description": "Required filters, as SQL-ish predicate strings a compliant plan carries.",
        "items": {"type": "string"},
    },
    "grain": {
        "type": "string",
        "description": "The governed grain the plan's grain must match.",
    },
    "checks": {
        "type": "array",
        "description": "Post-query validations the plan should carry; Hyperset never runs them.",
        "items": {"type": "string"},
    },
    "caveats": {
        "type": "array",
        "description": "Free-text caveats to surface with any answer built from this definition.",
        "items": {"type": "string"},
    },
}
_DRAFT_DEFINITION_SCHEMA = {
    "type": "object",
    "description": (
        "The manifest-shaped context definition, validated against the SAME rules a human's "
        "Git commit faces. Only the keys below are accepted; each mirrors the manifest section "
        "of the same name, and its shape is stated here. 'definitions' is required (at least "
        "one). Relational rules the shape cannot carry -- a field must read an approved source, "
        "and a ref cannot be both approved and prohibited -- are enforced on submit and named "
        "in the error."
    ),
    "properties": {field: _DRAFT_FIELD_SCHEMAS[field] for field in DRAFT_DEFINITION_FIELDS},
    "required": ["definitions"],
    "additionalProperties": False,
}

_DISCOVER_SCHEMA = {
    "query": {
        "type": "string",
        "description": (
            "The analytics question, in the agent's own words. Ranked against the "
            "configured catalog to suggest WHERE to look: candidate domains and concepts, "
            "each with the signal that ranked it. Assist-class and non-authoritative -- a "
            f"candidate is not a resolution. Send the exact names it surfaces through "
            f"{RESOLVE} to get governed meaning."
        ),
    },
    "limit": {
        "type": ["integer", "null"],
        "description": (
            f"How many candidates to return, most-relevant first, default {CANDIDATE_LIMIT}."
        ),
    },
}

_EXPAND_SCHEMA = {
    "query": {
        "type": "string",
        "description": (
            "The analytics question, in the agent's own words. Recorded with the result; "
            "expansion does not read it to choose where to go -- it follows only the "
            "governed edges the estate declares."
        ),
    },
    "domain": {
        "type": "string",
        "description": (
            "The governed domain to expand FROM: an exact domain name from "
            f"{CATALOG}, which must declare the 'concepts' below. Expansion starts only "
            "from a domain you have established is governed."
        ),
    },
    "concepts": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "The concept terms the start 'domain' must declare, exactly as the catalog "
            "lists them -- the same coverage claim resolve_analytics_context requires. "
            "Required with 'domain'; omit when 'from_root' is true."
        ),
    },
    "from_root": {
        "type": ["boolean", "null"],
        "description": (
            "Enter at the synthetic HIVE-MIND ROOT and walk DOWN without naming a start "
            "'domain'. The root links the enabled, current, and ACL-visible top-level domains "
            "of your workspace (edges marked 'evidence: system' -- catalog-derived NAVIGATION, "
            "never governed 'evidence: git'); each reached domain carries document POINTERS "
            "(ids/paths/refs, never content), and a disabled, unsynced, or ACL-denied domain is "
            "disclosed EXCLUDED-with-reason, never dropped. When true, 'domain'/'concepts' are "
            "omitted. Resolve or search_knowledge a returned pointer to read content."
        ),
    },
    "max_hops": {
        "type": ["integer", "null"],
        "description": (
            "Cap the expansion at this many `contains` hops from the start domain. Omit "
            "for the whole reachable subtree. A dropped domain is DISCLOSED, never silent."
        ),
    },
    "max_components": {
        "type": ["integer", "null"],
        "description": (
            "Cap how many related domains one expansion may pull in (breadth), so a wide "
            "forest cannot blow the packet. A dropped domain is DISCLOSED."
        ),
    },
    "context_budget": {
        "type": ["integer", "null"],
        "minimum": MIN_CONTEXT_BUDGET,
        "description": (
            "Bytes of the serialized result. The result is BOUND to this: the farthest "
            "related domains are dropped (breadth shrunk) until it fits, and the drop is "
            "disclosed with 'expansion_over_context_budget' -- an over-budget graph is never "
            "returned. If even the start domain alone does not fit, the request FAILS CLOSED "
            "with an empty result carrying that code, rather than a populated over-budget one."
        ),
    },
}

_CATALOG_SCHEMA = {
    "limit": {
        "type": ["integer", "null"],
        "description": (
            f"Domains per page, {CATALOG_MIN_LIMIT}-{CATALOG_MAX_LIMIT}, default "
            f"{CATALOG_DEFAULT_LIMIT}. Caps this page only: the lists inside each domain "
            f"are capped at {CATALOG_INNER_LIMIT} and that is not raisable, so the "
            f"listing stays a preview. Resolve the domain for its lists whole."
        ),
    },
    "offset": {
        "type": ["integer", "null"],
        "description": (
            "Start this many domains into the listing, for paging. Use "
            "'page.next_offset' from the previous response."
        ),
    },
}

_REF_EXAMPLE = "superset:dataset:finance_orders_daily"
_QUERY_EXAMPLE = "Which source and rules should an analyst use for recognized revenue by region?"

_QUERY_SCHEMA = {
    "query": {
        "type": "string",
        "description": (
            "The analytics question, in the agent's own words. Recorded with the answer "
            "and never interpreted: Hyperset retrieves what 'directive' names, and does "
            f"not infer a domain from the wording. Call {CATALOG} first."
        ),
    },
    "directive": {
        "type": "object",
        "description": (
            "What to retrieve, chosen by your planner from the catalog. Naming neither "
            "'domains' nor 'asset_refs' is refused rather than guessed."
        ),
        "properties": {
            "domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Configured domains to resolve, exactly as the catalog lists them, "
                    "e.g. ['revenue']. One bundle answers for one domain; name a second "
                    "domain in a second call."
                ),
            },
            "asset_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    f"Exact evidence refs, e.g. ['{_REF_EXAMPLE}']. With a domain, these "
                    "narrow its evidence; a ref the domain does not cover comes back as "
                    "observed-only evidence and the bundle's status becomes 'mixed'. "
                    "Without a domain, only observed-only evidence is returned."
                ),
            },
            "concepts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "The concept terms this answer needs the named domain to declare, "
                    f"exactly as {CATALOG} lists them for it, e.g. ['recognized_revenue']. "
                    "Required whenever 'domains' is named and refused without it: Hyperset "
                    "does not read the question, so this is the only place a caller can say "
                    "what the domain has to cover. A term the domain does not declare is "
                    "refused with code 'domain_does_not_declare' and nothing governed is "
                    "served -- the nearest domain is not the right domain."
                ),
            },
            "max_hops": {
                "type": ["integer", "null"],
                "description": (
                    "Bound domain_graph to this many hops from the domain node. Never "
                    "trims the instructions. Omit for the whole projection."
                ),
            },
            "context_budget": {
                "type": ["integer", "null"],
                "description": (
                    "Byte ceiling for the answer. Over budget, the observed payloads are "
                    "omitted and the omission is disclosed as a resolution.warnings entry "
                    "with code 'observed_payloads_omitted'; instructions, refs, versions, "
                    "and findings are never dropped."
                ),
            },
        },
        "additionalProperties": False,
    },
}


def _validate_query_schema() -> dict:
    """`_QUERY_SCHEMA`'s shape with validate-time meaning in its descriptions.

    Splatting `_QUERY_SCHEMA` into VALIDATE advertised the RESOLVE-time
    meaning of every field: a paragraph on how to CHOOSE `asset_refs` ("With a
    domain, these narrow its evidence") on an operation whose only correct
    value is the one already resolved. One sentence of the tool description
    said "send the same query and directive", under a schema that spent a
    paragraph inviting a different one, and the schema is what a planner reads
    per field while filling the call in.

    That is not hypothetical. Measured on the governed arm (hy-t3am): the model
    planned correctly, called validate, and sent `asset_refs` the resolve
    directive had not carried, so the request re-resolved to a different bundle
    -- planned cb-0f5046c1de99324b against resolved cb-72b9b503948a2597 -- and a
    correct plan
    came back `stale_bundle`. The refs it added were the ones its plan reads,
    which `source_refs` is the field for.

    Derived from `_QUERY_SCHEMA` rather than retyped so the two cannot drift in
    SHAPE: a field added to the directive reaches validate automatically and
    arrives carrying its resolve-time wording, which is the safe direction to
    fail. Only the descriptions are overridden, so this changes what the model
    is told and nothing about what the server accepts.
    """
    schema = deepcopy(_QUERY_SCHEMA)
    schema["query"]["description"] = (
        "Copy 'request.query' from the bundle you are validating against, verbatim. Not "
        "the question reworded, shortened, or re-asked: the bundle id covers the request "
        "as well as the answer, so any edit here re-resolves to a different bundle and "
        "your plan comes back 'stale_bundle' rather than validated."
    )
    directive = schema["directive"]
    directive["description"] = (
        "Copy 'request.directive' from the bundle you are validating against, verbatim. "
        "This is the one field agents get wrong: it is not chosen again here. Every "
        "per-field note below describes how to CHOOSE a directive at "
        f"{RESOLVE} time and none of it applies now -- the choice was already made and "
        "its result is the bundle you are checking. To name the refs your PLAN reads, "
        "use 'source_refs'; adding them here changes what this call resolves to and "
        "invalidates the very plan you are validating."
    )
    for field in ("domains", "asset_refs", "concepts"):
        note = directive["properties"][field]
        note["description"] = (
            f"At validate time: copy 'request.directive.{field}' from the bundle exactly "
            f"as it appears, or omit it if the bundle's directive omits it. "
            f"(Resolve-time meaning, which does NOT apply here: {note['description']})"
        )
    return schema


_VALIDATE_QUERY_SCHEMA = _validate_query_schema()

_PLAN_ITEMS = {"type": "array", "items": {"type": ["string", "object"]}}

# Default-deny, served with every tool because it is the precondition that
# makes an added value safe to ship without moving SCHEMA_VERSION (ADR 0018
# decision 5). One constant rather than three paragraphs: the rule is the same
# on all three tools, and three copies is three chances for one to drift.
#
# The split is per field and not by category, because "a governance-bearing
# field" named no set anyone could apply (hy-2fmp). A field CARRIES a verdict
# or QUALIFIES one, and the two halves owe different things: denial, and
# disclosure that survives to a person.
_UNKNOWN_VALUE_RULE = (
    " A value you do not recognise is never approval. A field that CARRIES a verdict -- "
    "'resolution.status', a plan's 'status', an 'observed_assets' entry's 'governance', a "
    "'violations' entry's 'code', an error 'code' -- is NOT APPROVED when its value is one you "
    "do not know: not governed, not valid, not approved, the error not recovered from. Never "
    "infer approval from the absence of a refusal you know. A field that QUALIFIES a verdict -- "
    "a 'violations' entry's 'severity', a 'page.truncated' entry's 'reason', a "
    "'resolution.warnings' entry's 'code' -- does not invalidate a verdict you did recognise: "
    "the answer it rides on stays what it was, and the unrecognised value is an undischarged "
    "caveat you MUST SURFACE with that answer, carried through to whatever you show a person "
    "and never silently discarded. Do not act on it as though you understood it. An "
    "unrecognised 'severity' is treated as no less blocking than the strictest severity you "
    "know."
)

# Checked-in examples, served with the tool docs, so parameter shape is never
# a guess (`docs/v0-foundation.md` section 7, tool-design requirements).
_EXAMPLES = {
    CATALOG: {"limit": 20},
    DISCOVER: {"query": _QUERY_EXAMPLE},
    RESOLVE: {
        "query": _QUERY_EXAMPLE,
        "directive": {"domains": ["revenue"], "concepts": ["recognized_revenue"]},
    },
    VALIDATE: {
        "query": _QUERY_EXAMPLE,
        "directive": {"domains": ["revenue"], "concepts": ["recognized_revenue"]},
        "bundle_id": "cb-0123456789abcdef",
        "source_refs": [_REF_EXAMPLE],
        "fields": ["recognized_revenue", "region"],
        "joins": ["finance_orders_daily.customer_id->customer_dim.customer_id"],
        "filters": ["finance_orders_daily.status = 'completed'"],
        "grain": "order_date by customer_dim.region",
        "checks": ["recognized_revenue is non-negative"],
    },
    EXPAND: {
        "query": _QUERY_EXAMPLE,
        "domain": "revenue",
        "concepts": ["recognized_revenue"],
        "max_hops": 2,
    },
    SEARCH_KNOWLEDGE: {
        "query": "recognized revenue",
        "mode": "grep",
        "filters": {"path_prefix": "docs/"},
        "limit": 20,
    },
    RECORD_ANSWER_FEEDBACK: {
        "outcome": "ignore",
        "source_ref": "ctxsrc-0123456789abcdef:docs/revenue.md",
    },
    LOOKUP_ANSWER_FEEDBACK: {"correlation_id": "corr-0123456789abcdef"},
    LIST_REVIEW_TASKS: {},
    GET_REVIEW_TASK: {"task_id": "rt-0123456789abcdef"},
    EDIT_REVIEW_DRAFT: {
        "task_id": "rt-0123456789abcdef",
        "definition": {
            "definitions": [{"term": "churn", "statement": "customers lost in a period"}]
        },
    },
    REFINE_REVIEW_DRAFT: {
        "task_id": "rt-0123456789abcdef",
        "feedback": "tighten the churn definition",
    },
    PROPOSE_REVIEW_TO_GIT: {"task_id": "rt-0123456789abcdef"},
    SET_REVIEW_ASSIGNEE: {
        "task_id": "rt-0123456789abcdef",
        "assigned": True,
    },
}

OPERATION_SPECS: dict[str, dict] = {
    CATALOG: {
        "title": "List context catalog",
        "description": (
            "List what governed context exists so a planner can choose where to start: "
            "the configured domains with their concept terms, document paths, approved "
            "and prohibited source refs, evidence refs, node kinds and relationship "
            "names, plus the observed source kinds and how many live assets of each "
            "exist. Read-only and cheap. Domains are paged by 'limit' and 'offset' and "
            "each domain's lists are capped, both positionally and never by relevance: "
            "each 'page.truncated' entry names a list and says whether it was 'cut' or "
            "'withheld', each domain's 'counts' gives the full size of its lists, and "
            "'page.next_offset' is where the next call starts. A truncated "
            "'evidence_refs' is withheld rather than cut, because a "
            "partial list of seeds is unfit for the one thing seeds are for: the KEY IS "
            "ABSENT from that domain, which means withheld and not 'none declared' -- "
            "'counts.evidence_refs' still gives the full size, and resolving the domain "
            "without 'asset_refs' returns them all. It carries identifiers "
            "and titles only -- no definitions, expressions, filters, or caveats -- so "
            "it answers nothing on its own: pick from it, then call "
            "resolve_analytics_context with a directive to get governed meaning with its "
            "authority, provenance, and warnings attached." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": dict(_CATALOG_SCHEMA),
            "additionalProperties": False,
        },
        "example": _EXAMPLES[CATALOG],
    },
    DISCOVER: {
        "title": "Discover analytics context",
        "description": (
            "Rank the configured context catalog for a question and return candidate "
            "domains and concepts to look at, each disclosing the signal that ranked it -- "
            "a relevance score and the embedding index that produced it. Assist-class and "
            "read-only: a candidate is derived and non-authoritative. It names where to "
            "look, never that an answer is governed; it carries no evidence ref and no "
            "resolution; and it can neither resolve an ambiguity nor invent a match where "
            "the exact resolver would find none. Unlike the catalog's positional preview, "
            "discovery ranks the full declared lists, so a relevant concept past the "
            f"catalog's cap is reachable by relevance. Pick from it, then call {RESOLVE} "
            "with the exact names to get governed meaning with its authority and "
            "provenance attached." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": dict(_DISCOVER_SCHEMA),
            "required": ["query"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[DISCOVER],
    },
    RESOLVE: {
        "title": "Resolve analytics context",
        "description": (
            "Compile one ContextBundle from the customer's authoritative Git context "
            "plus linked Superset/DataHub evidence, retrieving exactly what 'directive' "
            f"names. Read-only. Get the names from {CATALOG}. status 'governed' means "
            "the guidance comes from that Git context, not that Hyperset approved the "
            "business meaning; 'mixed' means part of the answer is observed-only; "
            "'observed_only' is raw observation with no authority behind it; 'no_match' "
            "means Git says nothing about what was named, and is a valid answer rather "
            "than an invitation to invent guidance. Hyperset neither executes nor "
            "validates SQL or results. Every 'resolution.warnings' entry is an object "
            "with a stable 'code' and a human 'message': branch on the code, never on "
            "the wording. The codes are exported as "
            "hyperset.bundle.WARNING_CODES; the ones a planner most often acts on are "
            "'ref_malformed' (fix the ref you sent), 'ref_ambiguous' (qualify it), "
            "'ref_not_observed' (a sync is needed; editing will not help), "
            "'projection_bounded' and 'max_hops_not_applicable' (what happened to the "
            "graph), and 'observed_payloads_omitted' and 'over_context_budget' (what "
            "the byte ceiling did)." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": dict(_QUERY_SCHEMA),
            "required": ["query", "directive"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[RESOLVE],
    },
    VALIDATE: {
        "title": "Validate analytics plan",
        "description": (
            "Deterministically compare a proposed fetch (sources, fields, joins, filters, "
            "grain, checks) with the governed context, and return machine-readable "
            "violations naming the instruction section each one contradicts. Never "
            "executes SQL. Send the same 'query' and 'directive' you resolved with, and "
            "the 'bundle_id' that resolution returned. "
            "Bundles are content-derived and unstored, so the question is re-resolved "
            "server-side and compared with the bundle you planned against: a moved "
            "answer, or a different question, comes back as a 'stale_bundle' violation "
            "instead of being validated against silently. The bundle actually compared "
            "is disclosed in 'checked_against'." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_VALIDATE_QUERY_SCHEMA,
                "bundle_id": {
                    "type": "string",
                    "description": (
                        "The bundle id the plan was built against, as returned by "
                        "resolve_analytics_context."
                    ),
                },
                "source_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "The governed sources the plan reads, each a ref string such as "
                        "'table:postgres:analytics.public.finance_orders_daily' or a "
                        "'pipeline:...' ref. Every ref must be one this domain approves in "
                        "'instructions.approved_sources'; a ref the context prohibits, one "
                        "carried only as a raw observation, or one it does not approve comes "
                        "back as a violation (prohibited_source, observed_only_source, "
                        "unapproved_source). Strings only."
                    ),
                },
                "fields": {
                    **_PLAN_ITEMS,
                    "description": (
                        "The fields the plan computes. Each entry is either the field NAME as "
                        "a string, or an object echoing a bundle 'instructions.fields' entry -- "
                        "{name, expression, source_ref} -- of which only the keys you include "
                        "are compared. A name the context defines no field for is "
                        "'unapproved_field'; an 'expression' that provably differs is "
                        "'field_expression_mismatch', while one differing only in table "
                        "qualifiers or casts is disclosed as 'field_expression_undecidable' "
                        "(a warning, not a rejection); a 'source_ref' that is not the one the "
                        "context names is 'field_source_mismatch'. Note a field's governed "
                        "source must ALSO be listed in 'source_refs', or it is "
                        "'undeclared_field_source' -- so echoing an 'instructions.fields' entry "
                        "validates only when 'source_refs' carries that field's source too."
                    ),
                },
                "joins": {
                    **_PLAN_ITEMS,
                    "description": (
                        "The joins the plan performs. Each entry is either a string "
                        "'left->right' (the SQL-ish 'left = right' is also accepted) or an "
                        "object echoing an 'instructions.joins' entry -- {from, to, type} -- "
                        "compared on the keys you include. Echoing an 'instructions.joins' "
                        "entry verbatim validates; a join the context does not declare is "
                        "'unapproved_join', and a differing 'type' is 'join_type_mismatch'."
                    ),
                },
                "filters": {
                    **_PLAN_ITEMS,
                    "description": (
                        "The filters the plan applies. Each entry is either the filter as a "
                        "SQL string or an object echoing 'instructions.filters' (its "
                        "'expression' is compared, else the entry's string form). A filter "
                        "'instructions.filters' requires that is absent is "
                        "'missing_required_filter'; one that differs only in table qualifiers "
                        "or casts is disclosed as 'filter_undecidable' rather than judged, "
                        "because Hyperset does not run the query. An extra filter the plan "
                        "adds that the context does not declare is 'unapproved_filter' (a "
                        "warning: it narrows the answer past the governed definition rather "
                        "than contradicting it)."
                    ),
                },
                "grain": {
                    "type": ["string", "null"],
                    "description": (
                        "The grain the plan assumes, as a string, or null when the plan states "
                        "none. It must match 'instructions.grain': a differing grain is "
                        "'grain_mismatch', and one differing only in qualifiers or casts is "
                        "disclosed as 'grain_undecidable'. Null omits the check ONLY when the "
                        "governed context declares no grain; a null grain against a required "
                        "'instructions.grain' is 'grain_mismatch', not an omission."
                    ),
                },
                "checks": {
                    **_PLAN_ITEMS,
                    "description": (
                        "The checks the plan will run on the result. Each entry is a string, "
                        "or an object whose string form is used. A check "
                        "'instructions.validations' requires that is absent is "
                        "'missing_required_check' -- a disclosure, not a contradiction, because "
                        "Hyperset never executes or verifies a check itself; the caller runs it "
                        "and reports the outcome."
                    ),
                },
            },
            # Required, not optional: an agent with a plan necessarily
            # resolved first and was given the id back, so accepting a plan
            # without one buys nothing and costs a silently skipped
            # staleness check.
            "required": ["query", "directive", "bundle_id"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[VALIDATE],
    },
    EXPAND: {
        "title": "Expand analytics context",
        "description": (
            "NAVIGATION, not a governed answer: from one governed 'domain', follow the "
            "customer's declared 'contains' hierarchy edges into the RELATED domains and "
            "return which are reachable and the governed edges among them. Bounded by "
            "'max_hops'/'max_components'/'context_budget', cycle- and duplicate-safe, and "
            "every edge keeps its 'evidence: \"git\"' provenance. The result carries "
            "'result_kind': 'navigation' and NO authority, instructions, or evidence -- it "
            f"names WHERE to look next, and each domain it lists must still be resolved with "
            f"{RESOLVE} to get governed meaning. It composes nothing. THIS SLICE follows "
            "'contains' edges only (the depends_on/joinable_on relationship edges are not "
            "emitted yet). It discloses 'expansion_bounded' when a 'max_hops'/'max_components' "
            "cap dropped part of the graph; 'expansion_over_context_budget' when the byte "
            "budget shrank the graph (the far domains are DROPPED to fit, never returned "
            "over-budget); and 'expansion_domain_unavailable' when the estate declares a "
            "neighbour that is not currently governed -- surfaced with 'available': false and "
            "its reason, never traversed and never hiding a valid sibling. It does NOT check "
            "per-domain staleness or conflicts, so the ABSENCE of a staleness/conflict "
            "warning means this operation did not check it, not that the domain is fresh or "
            "non-conflicting." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": dict(_EXPAND_SCHEMA),
            "required": ["query", "domain", "concepts"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[EXPAND],
    },
    SEARCH_KNOWLEDGE: {
        "title": "Search knowledge",
        "description": (
            "READ-ONLY, NON-AUTHORITATIVE lexical or semantic search over the sources this "
            "deployment has "
            "CONFIGURED -- the messy Git/context content an agent searches BEFORE resolving the "
            "governed answer. It searches ONLY configured/governed sources through an adapter, "
            "never an arbitrary file on the server. Each hit names its source (id + repository), "
            "the path and line, the commit and content version, the ACL decision that admitted "
            "it, the source's staleness, and the match type. It is FAIL-CLOSED per source: a "
            "caller without access to a source gets ZERO hits from it, and its denial is decided "
            "before its content is read or embedded. 'mode' defaults to 'grep'; 'semantic' "
            "ranks authorized lines using the same configured embedding provider as discovery "
            "and discloses its score and embedding space on each hit. 'sources' optionally "
            "narrows to named configured sources; 'filters' does path narrowing "
            "('path_prefix'/'path'). It writes, proposes, approves, "
            "and resolves NOTHING: a hit is a place to look, and its meaning must still be got "
            f"from {RESOLVE}." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "text to search for"},
                "intent": {
                    "type": "string",
                    "description": (
                        "optional caller-declared purpose for this search; stored in the "
                        "interaction trace to help the write-back reviewer understand why "
                        "the hit was sought"
                    ),
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "optional: configured source ids/repos; all if omitted",
                },
                "filters": {
                    "type": "object",
                    "properties": {
                        "path_prefix": {"type": "string"},
                        "path": {"type": "string"},
                    },
                    "additionalProperties": False,
                    "description": "optional lexical path narrowing",
                },
                "mode": {
                    "type": "string",
                    "enum": ["grep", "semantic"],
                    "description": "lexical grep (default) or embedding-ranked semantic search",
                },
                "limit": {"type": "integer", "minimum": 1, "description": "max hits to return"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[SEARCH_KNOWLEDGE],
    },
    RECORD_ANSWER_FEEDBACK: {
        "title": "Record answer feedback",
        "description": (
            "Append one feedback decision to a hit or governed answer from this MCP "
            "session/correlation chain. The session and correlation are derived from "
            "transport metadata and the target must match an existing trace in the "
            "caller's workspace; fabricated or cross-workspace targets fail closed. "
            "This is operational audit only: it approves, merges, resolves, and writes "
            "no governed context. Free text is redacted before persistence." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": list(ANSWER_FEEDBACK_OUTCOMES)},
                "bundle_id": {
                    "type": "string",
                    "description": "optional ContextBundle id returned by a traced resolve",
                },
                "source_ref": {
                    "type": "string",
                    "description": "optional exact hit id or source_id:path document prefix",
                },
                "review_task_id": {
                    "type": "string",
                    "description": "optional proposal/review-task id this feedback concerns",
                },
                "notes": {"type": "string", "description": "optional redacted rationale"},
            },
            "required": ["outcome"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[RECORD_ANSWER_FEEDBACK],
    },
    LOOKUP_ANSWER_FEEDBACK: {
        "title": "Lookup answer feedback",
        "description": (
            "Read append-only answer feedback in the caller's workspace, filtered by at "
            "least one exact session, correlation, source/document, or proposal/review-task "
            "id. Read-only and non-authoritative: feedback may inform a later proposal but "
            "never changes governed meaning." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "correlation_id": {"type": "string"},
                "source_ref": {"type": "string"},
                "review_task_id": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "additionalProperties": False,
        },
        "example": _EXAMPLES[LOOKUP_ANSWER_FEEDBACK],
    },
    LIST_REVIEW_TASKS: {
        "title": "List review tasks",
        "description": (
            "List the open flywheel review tasks -- each a miss's finding evidence plus the "
            "step-4 UNAPPROVED assist draft ('proposal_payload') -- for an expert to read. "
            "Read-only: it presents; it does not approve. Filter with 'status'. The draft is "
            "an unapproved candidate, never governed authority -- approval is a human Git "
            "commit (ADR 0012)." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": list(REVIEW_TASK_STATUSES),
                    "description": (
                        "Filter to tasks in this lifecycle state. One of: "
                        f"{', '.join(REVIEW_TASK_STATUSES)}. OMIT THE FIELD (or send an empty "
                        "string) for all tasks. Any other unrecognised value is refused "
                        "(invalid_params), never answered with an empty list -- a typo must "
                        "not read as 'no open tasks'."
                    ),
                }
            },
            "additionalProperties": False,
        },
        "example": _EXAMPLES[LIST_REVIEW_TASKS],
    },
    GET_REVIEW_TASK: {
        "title": "Get review task",
        "description": (
            "Fetch one review task by id: its finding evidence and the step-4 UNAPPROVED "
            "assist draft ('proposal_payload'). Read-only and non-authoritative -- the draft "
            "is an unapproved candidate, not governed authority. Approval is a human Git "
            "commit (ADR 0012)." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[GET_REVIEW_TASK],
    },
    EDIT_REVIEW_DRAFT: {
        "title": "Edit review draft",
        "description": (
            "Replace the assist draft on a review task with an expert's edited 'definition', "
            "validated against the SAME manifest rules a human's Git commit faces. It mutates "
            "ONLY the unapproved assist draft: the task stays unapproved, no governed row is "
            "written, and no SQL runs (ADR 0012). The result is the unapproved draft, not "
            "governed authority." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "definition": _DRAFT_DEFINITION_SCHEMA,
            },
            "required": ["task_id", "definition"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[EDIT_REVIEW_DRAFT],
    },
    REFINE_REVIEW_DRAFT: {
        "title": "Refine review draft",
        "description": (
            "Re-run the assist-class authoring producer with the expert's 'feedback' and "
            "replace the assist draft on the same task. It mutates ONLY the unapproved assist "
            "draft and stays attributed (the trace's model and prompt hash ride on the "
            "payload); the task stays unapproved, no governed row is written, and no SQL runs "
            "(ADR 0012). The result is the unapproved draft, not governed authority."
            + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "feedback": {"type": "string"},
            },
            "required": ["task_id"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[REFINE_REVIEW_DRAFT],
    },
    PROPOSE_REVIEW_TO_GIT: {
        "title": "Propose review to Git",
        "description": (
            "Open a pull request carrying a review task's UNAPPROVED assist draft into the "
            "customer's context repository, and STOP. Proposal-only: it pushes a new branch "
            "and never approves, merges, writes a governed version, creates an approvable "
            "object, or runs SQL (ADR 0012). A PII guard runs over the proposal content "
            "before it is committed. The task stays unapproved until a human merges the PR; "
            "the result names the proposal, not governed authority." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[PROPOSE_REVIEW_TO_GIT],
    },
    SET_REVIEW_ASSIGNEE: {
        "title": "Set review assignee",
        "description": (
            "Assign a review task (`assigned: true`) or unassign it (`assigned: false`). "
            "OMIT 'assignee' to SELF-claim: the owner is the CALLER'S OWN verified identity, "
            "computed by the server as an opaque 'subject@issuer' (PII-safe -- a typed owner "
            "is never accepted as free text). GIVE 'assignee' to assign ANOTHER user: it is "
            "accepted ONLY as a KNOWN approved identity from the reviewer allowlist, never as "
            "typed free text, and requires the allowlist to be configured. Assignment is task "
            "metadata -- who is working the gap so two people do not duplicate it -- NOT an "
            "approval or an access grant: it writes no governed row, resolves nothing, and "
            "runs no SQL (ADR 0012). The result is the updated task." + _UNKNOWN_VALUE_RULE
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "assigned": {
                    "type": "boolean",
                    "description": "true to assign this task, false to unassign it",
                },
                "assignee": {
                    "type": "string",
                    "description": (
                        "OPTIONAL, only with assigned=true: a KNOWN approved reviewer identity "
                        "(subject@issuer) from the allowlist to assign the task to; omit to "
                        "claim it yourself. Not accepted as arbitrary free text."
                    ),
                },
            },
            "required": ["task_id", "assigned"],
            "additionalProperties": False,
        },
        "example": _EXAMPLES[SET_REVIEW_ASSIGNEE],
    },
}


# A FIXED denial. It names no resource, no operation, and no decision class, so an
# unauthorized caller gets the SAME answer for a resource that exists and one that
# does not -- the denial leaks nothing (ADR-0030 Decision 4). It is raised BEFORE any
# dispatch, so no bundle, catalog, partial, or provenance is assembled for a denied
# call: deny-the-whole, never strip-and-serve.
_DENIAL_MESSAGE = "not authorized"
_DENIAL_RECOVERY = "present a valid bearer token for an authorized identity"

# The resource domain a request touches when NONE can be resolved: a cross-domain
# listing (catalog), a directive that names no domain, or a governed read with no
# domain at all (context history). It carries a NUL, which a governed domain slug --
# casefolded, collision-checked -- can never be, so a future per-domain grant
# (scope.domain="revenue") does NOT cover it and DENIES: fail closed. Only an all-None
# reader scope covers it. Never the empty string, which a `domain == ""` grant could
# match and which silently read as "any domain" in the first cut of this gate.
_UNRESOLVED_DOMAIN = "\x00unresolved"


def _authorization_resources(name: str | None, params: dict, principal=None) -> list[Resource]:
    """The governed resources a request touches, for the gate to authorize EACH of.

    Every resource carries the caller's WORKSPACE (hq-t6nx #438), so the gate scopes
    EVERY read op -- catalog, discover, validate, expand, resolve, and the context-
    history route -- to one tenant through `Scope.covers`. Additive: a grant with
    `workspace=None` (every role defined before this slice) still covers any
    workspace, so default behaviour is unchanged; a workspace-scoped grant now
    denies a cross-tenant read at the gate.

    A resolve/validate directive names its domains explicitly, so the gate binds the
    decision to the REAL domain(s) -- the earlier `params.get("domain")` was always
    `None` (resolve's params are `query`/`directive`; the domains live in
    `directive["domains"]`), so a per-domain grant silently checked `""` and never the
    request: a dead-wired, false-clearing gate. Any other operation, or a directive
    that names no domain, resolves to the unresolvable sentinel and so requires an
    all-domain (reader) grant -- a domain-scoped grant that cannot see the request's
    domain must DENY, never default to reading everything."""
    if name in (RESOLVE, VALIDATE):
        directive = params.get("directive")
        domains = directive.get("domains") if isinstance(directive, dict) else None
        # `domains` must be a LIST to be trusted as domain names. A non-list truthy
        # value (a bare string, a dict) would otherwise iterate into per-character or
        # per-key resources and authorize the wrong thing -- so anything that is not a
        # list falls through to the unresolvable sentinel and DENIES a domain-scoped
        # grant (fail closed), rather than being coerced. Brandon's named
        # enable-precondition (overseer hq-rqq6, 2026-08-15).
        if isinstance(domains, list) and domains:
            return [
                Resource(domain=str(domain), workspace=_principal_workspace(principal))
                for domain in domains
            ]
    return [Resource(domain=_UNRESOLVED_DOMAIN, workspace=_principal_workspace(principal))]


def authorization_error(name: str | None, params: dict, principal) -> OperationError | None:
    """The gate's decision AS A VALUE: the uniform `unauthorized` denial when the
    enabled gate denies this principal, else `None`. A no-op (returns `None`) unless
    `HYPERSET_AUTHZ_ENABLED` is on, so with the flag off nothing changes. `run_operation`
    raises it; a served governed read that does NOT route through `run_operation` -- the
    context-history route -- fails with it, so every governed read shares ONE fail-closed
    decision and ONE non-disclosing denial rather than growing a second, divergent gate.

    DENY-THE-WHOLE across a multi-domain request: every resolved resource must be
    allowed, and the FIRST denied one denies the request -- a reader authorized on one
    domain but not another cannot read a directive that spans both. The `authorize`
    call is pure; the principal was verified at the transport boundary (or is the
    trusted in-process identity), never here."""
    if not authz_enabled():
        return None
    # The action the operation requires: a governed READ for most, REVIEW for the
    # review-authoring ops (hy-dq0r). A name this map does not carry -- every read op,
    # and the name-less context-history route -- is a READ. So a read-only role is
    # denied a review-authoring op, and deny-the-whole still holds per resource.
    action = OPERATION_ACTIONS.get(name, READ)
    # The explicit approved-reviewer ALLOWLIST (hy-a607k), ANDed with the reviewer role
    # for the REVIEW action: a Git-owned per-principal policy that gates the review-
    # authoring ops IN ADDITION to the role. Not configured => no-op (role-only, byte-
    # identical); configured => the caller must ALSO be listed, else denied with the SAME
    # uniform, non-disclosing `UNAUTHORIZED`. Only the REVIEW action consults it, so a
    # governed READ is unaffected.
    if action == REVIEW and not reviewer_allowlist_approves(principal):
        return OperationError(UNAUTHORIZED, _DENIAL_MESSAGE, recovery=_DENIAL_RECOVERY)
    # The role registry the names resolve against: the public token-resolvable `ROLES`
    # for a verified/bearer principal, or the in-process-only registry for the trusted
    # `SYSTEM_PRINCIPAL` singleton -- chosen by OBJECT IDENTITY, so a token asserting a
    # `system` role (a different Principal object) can never reach the system grant
    # (hy-i4hc). `roles_for` is the one place that choice is made.
    registry = roles_for(principal)
    for resource in _authorization_resources(name, params, principal):
        if not authorize(principal, action, resource, registry).allowed:
            return OperationError(UNAUTHORIZED, _DENIAL_MESSAGE, recovery=_DENIAL_RECOVERY)
    return None


def admin_config_authorization_error(principal) -> OperationError | None:
    """The gate for an ADMIN deployment-settings WRITE (the write-back target, hy-2nqb),
    as a value: the uniform `unauthorized` denial when the enabled gate denies this
    principal the `configure` action, else `None`.

    A no-op (returns `None`) unless `HYPERSET_AUTHZ_ENABLED` is on -- so with the flag
    off, the admin write stays UNAUTHENTICATED, which is the LOCAL-ONLY dev shortcut
    (the operator drives it on loopback). Enabling authz is what closes it for a
    non-loopback deployment: only a principal with an `admin` (`configure`) grant may
    write, and an unauthenticated caller or an insufficient role (reader/reviewer/...)
    is denied, fail-closed and non-disclosing -- the SAME `UNAUTHORIZED` a governed read
    denial uses. The config write is global, not domain-scoped, so it authorizes over the
    unresolvable-domain sentinel, which only an all-scope `configure` grant covers.
    """
    if not authz_enabled():
        return None
    resource = Resource(domain=_UNRESOLVED_DOMAIN)
    if not authorize(principal, CONFIGURE, resource, roles_for(principal)).allowed:
        return OperationError(UNAUTHORIZED, _DENIAL_MESSAGE, recovery=_DENIAL_RECOVERY)
    return None


def review_surface_authorization_error(principal) -> OperationError | None:
    """The gate for OPENING the Review SURFACE (hy-mg8p), as a value: the uniform
    `unauthorized` denial when the enabled gate denies this principal the `review`
    action, else `None`.

    A no-op (returns `None`) unless `HYPERSET_AUTHZ_ENABLED` is on -- so with the flag
    off, the review page stays reachable, the LOCAL-ONLY dev shortcut (loopback). With
    authz on, only a principal holding a `review` grant (the `reviewer` role, or the
    in-process system identity) may open the surface; an unauthenticated caller or a
    read-only role (reader/explorer/service) or `admin` (configure, not review) is
    denied. Opening the queue is not domain-scoped, so it authorizes REVIEW over the
    unresolvable-domain sentinel, which only an all-scope review grant covers -- the
    SAME action the review-authoring ops require at `run_operation`, so the page and the
    ops it drives share one decision rather than a second, divergent gate.
    """
    if not authz_enabled():
        return None
    # Same approved-reviewer allowlist the authoring ops enforce (hy-a607k), ANDed with
    # the reviewer role, so an unapproved reviewer cannot OPEN the surface either -- the
    # page and the ops it drives share one decision. Not configured => role-only.
    if not reviewer_allowlist_approves(principal):
        return OperationError(UNAUTHORIZED, _DENIAL_MESSAGE, recovery=_DENIAL_RECOVERY)
    resource = Resource(domain=_UNRESOLVED_DOMAIN)
    if not authorize(principal, REVIEW, resource, roles_for(principal)).allowed:
        return OperationError(UNAUTHORIZED, _DENIAL_MESSAGE, recovery=_DENIAL_RECOVERY)
    return None


def _principal_identity(principal) -> str:
    """The VERIFIED caller as an OPAQUE `subject@issuer`, never a raw email or other IdP
    profile claim -- the subject is an opaque IdP identifier, so it traces to an identity
    without publishing PII. `anonymous` when there is no principal (the authz gate off,
    the loopback dev path), matching the audit row's actor.

    The one server-side identity computation reused wherever a verified caller's identity
    is recorded from the Principal rather than accepted as caller free text: the proposer
    attributed on the PR trail (hy-mg8p) and the self-claimed review-task assignee
    (hy-s8a6). Computing it here is what keeps those fields PII-safe by construction."""
    if principal is None:
        return "anonymous"
    return f"{principal.subject}@{principal.issuer}"


def _principal_workspace(principal) -> str:
    """The TENANT/WORKSPACE the verified caller acts in (hq-t6nx, ADR-0037), or the
    single implicit 'default' when the authz gate is off (no principal). Never a
    wildcard, so a proposal routes within exactly one workspace."""
    return principal.workspace if principal is not None else "default"


def run_operation(name: str, params: dict, *, session_factory, principal=None) -> dict:
    """Run one named operation and return its serialized public response.

    Every failure leaves here as an `OperationError`, including one nobody
    predicted: a transport that receives an exception instead has no answer
    to give, and an agent whose tool call ends in silence cannot recover
    from it.

    `principal` is the verified caller (or `None`). The authorization gate runs
    BEFORE dispatch, so a denied call never reaches retrieval -- crucial for
    non-disclosure, since a nonexistent domain otherwise returns a 200 bundle with
    warnings and would signal existence by its absence.
    """
    started_at = time.perf_counter()
    if name not in OPERATIONS:
        raise OperationError(
            UNKNOWN_OPERATION,
            f"{name!r} is not an operation of this server",
            recovery=f"call one of: {', '.join(OPERATIONS)}",
        )
    # Authorize BEFORE dispatch. A denied TRACED call still records a trace row --
    # status=denied plus the correlation, and NONE of the content it was denied --
    # so a refused tool call is reconstructable without leaking the protected source
    # (hy-oqevj). The denial is then raised exactly as `_authorize` would.
    denial = authorization_error(name, params, principal)
    if denial is not None:
        if name in _TRACED_OPERATIONS:
            _record_interaction_trace(
                name,
                params,
                principal=principal,
                status="denied",
                hit_ids=[],
                duration_ms=_duration_ms(started_at),
                source_staleness={},
                miss=None,
                answer_bundle_id=None,
                session_factory=session_factory,
            )
        raise denial
    # The caller's tenant/workspace (hq-t6nx #438), threaded to the read ops whose
    # readers list context SOURCES, so a tenant's catalog/discover/expand/validate see
    # only their own sources. VALIDATE scopes its internal resolve too (a validation
    # bundle must not be built from a sibling tenant). The public RESOLVE op's data
    # scoping stays deferred (ADR-0037); its authz is still workspace-scoped via
    # `_authorization_resources`.
    workspace = _principal_workspace(principal)
    try:
        if name == CATALOG:
            return _catalog(params, session_factory=session_factory, workspace=workspace).to_dict()
        if name == DISCOVER:
            return _discover(params, session_factory=session_factory, workspace=workspace).to_dict()
        if name == RESOLVE:
            bundle = _resolve(params, session_factory=session_factory, workspace=workspace)
            _record_miss(params, bundle, session_factory=session_factory)
            # Trace the resolve so the search that carried the same correlation id can
            # be tied to the answer that followed (hy-oqevj). A NO_MATCH/OBSERVED_ONLY
            # resolve found no governed context -> a miss, and holds no hit ids; a
            # governed/mixed answer is a hit keyed by its deterministic bundle id.
            _resolve_is_miss = bundle.status in (NO_MATCH, OBSERVED_ONLY)
            _record_interaction_trace(
                name,
                params,
                principal=principal,
                status="miss" if _resolve_is_miss else "hit",
                hit_ids=[] if _resolve_is_miss else [bundle.bundle_id],
                duration_ms=_duration_ms(started_at),
                source_staleness=_resolve_source_staleness(bundle),
                miss=_resolve_miss_detail(params) if _resolve_is_miss else None,
                answer_bundle_id=bundle.bundle_id,
                session_factory=session_factory,
            )
            # Record which citations supplied this answer (hy-cpkvu), keyed by the same
            # correlation id, so the answer's exact citations are enumerable later. Only a
            # real governed/mixed answer HAS citations; a miss records none.
            if not _resolve_is_miss:
                _record_answer_citations(
                    bundle, principal=principal, session_factory=session_factory
                )
            return bundle.to_dict()
        if name == VALIDATE:
            return _validate(params, session_factory=session_factory, workspace=workspace).to_dict()
        if name == EXPAND:
            return _expand(
                params, session_factory=session_factory, principal=principal, workspace=workspace
            ).to_dict()
        if name == SEARCH_KNOWLEDGE:
            _reject_unknown(params, _SEARCH_KNOWLEDGE_PARAMS)
            try:
                payload = _search_knowledge(
                    params,
                    session_factory=session_factory,
                    principal=principal,
                    workspace=workspace,
                )
                # Trace hits by opaque LOCATION id (source:path:line), never the matched
                # snippet bytes -- the search already dropped denied sources, so no denied
                # content can reach here (hy-oqevj). Any hit is a `hit`, none is a `miss`.
                hits = payload.get("hits") or []
                _record_interaction_trace(
                    name,
                    params,
                    principal=principal,
                    status="hit" if hits else "miss",
                    hit_ids=[
                        f"{hit.get('source_id')}:{hit.get('path')}:{hit.get('line')}"
                        for hit in hits
                    ],
                    duration_ms=_duration_ms(started_at),
                    source_staleness=_search_source_staleness(
                        hits,
                        searched_sources=payload.get("searched_sources") or [],
                        session_factory=session_factory,
                        workspace=workspace,
                    ),
                    miss=_search_miss_detail(payload) if not hits else None,
                    answer_bundle_id=None,
                    session_factory=session_factory,
                )
                return payload
            except ValueError as exc:
                # A bad request is the CALLER's fault (400), not the server's (500): the
                # knowledge module validates the request and raises ValueError, which would
                # otherwise fall to the internal-error handler below and mis-report a
                # malformed query as a server failure.
                raise OperationError(
                    INVALID_PARAMS,
                    str(exc),
                    recovery=f"fix the request to match the {SEARCH_KNOWLEDGE} input schema",
                ) from exc
        if name == RECORD_ANSWER_FEEDBACK:
            return _record_answer_feedback(
                params,
                session_factory=session_factory,
                principal=principal,
                workspace=workspace,
            )
        if name == LOOKUP_ANSWER_FEEDBACK:
            return _lookup_answer_feedback(
                params,
                session_factory=session_factory,
                principal=principal,
            )
        if name == LIST_REVIEW_TASKS:
            return _list_review_tasks(params, session_factory=session_factory, workspace=workspace)
        if name == GET_REVIEW_TASK:
            return _get_review_task(params, session_factory=session_factory, workspace=workspace)
        if name == EDIT_REVIEW_DRAFT:
            return _edit_review_draft(params, session_factory=session_factory, workspace=workspace)
        if name == REFINE_REVIEW_DRAFT:
            return _refine_review_draft(
                params, session_factory=session_factory, workspace=workspace
            )
        if name == PROPOSE_REVIEW_TO_GIT:
            return _propose_review_to_git(
                params,
                session_factory=session_factory,
                workspace=workspace,
                principal=principal,
            )
        if name == SET_REVIEW_ASSIGNEE:
            return _set_review_assignee(
                params, session_factory=session_factory, principal=principal, workspace=workspace
            )
        # Unreachable: `name` was checked against OPERATIONS above, and every member has a
        # branch. A NEW op that reached here (added to OPERATIONS but not dispatched) is a
        # wiring bug, answered as such rather than silently routed to the wrong handler.
        raise OperationError(
            UNKNOWN_OPERATION,
            f"{name!r} is served but has no dispatch branch",
            recovery=f"call one of: {', '.join(OPERATIONS)}",
        )
    except OperationError:
        raise
    except Exception as exc:
        # The caller learns the failure class and what to do about it, and
        # nothing else: a traceback or a driver message can carry the
        # database host and user. The detail goes to the server's log.
        traceback.print_exc(file=sys.stderr)
        raise OperationError(
            INTERNAL_ERROR,
            f"the server could not answer this request ({type(exc).__name__})",
            recovery=(
                "retry; if it persists, check the api and postgres logs -- the "
                "request itself may be fine"
            ),
        ) from exc


def serialize(payload: dict) -> str:
    """One serializer for both transports, so an HTTP body and the text an
    MCP tool returns are the same bytes and not merely the same fields."""
    return json.dumps(payload, sort_keys=False, default=str)


def _catalog(params: dict, *, session_factory, workspace: str | None = None) -> ContextCatalog:
    _reject_unknown(params, _CATALOG_PARAMS)
    limit = _bound(params, "limit", minimum=CATALOG_MIN_LIMIT, maximum=CATALOG_MAX_LIMIT)
    try:
        return list_context_catalog(
            session_factory=session_factory,
            limit=CATALOG_DEFAULT_LIMIT if limit is None else limit,
            offset=_bound(params, "offset", minimum=CATALOG_MIN_OFFSET) or 0,
            workspace=workspace,
        )
    except CatalogBoundError as exc:
        # `_bound` refuses these before the service is reached, so nothing on
        # the wire arrives here. It is kept because `run_operation` turns any
        # unconverted exception into `internal_error`, whose recovery text
        # says retry -- so if the check above is ever removed or bypassed, an
        # unconvertible request would be answered with a 500 telling the
        # caller to try again forever. A refusal it can act on beats a lie it
        # cannot.
        raise OperationError(
            INVALID_PARAMS,
            str(exc),
            recovery=f"send {exc.key!r} as {exc.allowed()}",
        ) from exc


def _discover(params: dict, *, session_factory, workspace: str | None = None) -> DiscoveryResult:
    _reject_unknown(params, _DISCOVER_PARAMS)
    limit = _bound(params, "limit", minimum=1)
    return discover_analytics_context(
        question=_required_string(params, "query"),
        session_factory=session_factory,
        limit=CANDIDATE_LIMIT if limit is None else limit,
        workspace=workspace,
    )


def _domain_authorizer(principal):
    """A `domain -> bool` ACL predicate for the hive-mind walk, or `None` when the authz
    gate is off (every domain visible, behaviour-preserving). Mirrors the per-source ACL
    the grep search runs: a governed READ on `Resource(domain, workspace)`, fail-closed."""
    if not authz_enabled():
        return None
    registry = roles_for(principal)
    workspace = _principal_workspace(principal)

    def _ok(domain: str) -> bool:
        resource = Resource(domain=domain, workspace=workspace)
        return authorize(principal, READ, resource, registry).allowed

    return _ok


def _expand(params: dict, *, session_factory, principal=None, workspace: str | None = None):
    _reject_unknown(params, _EXPAND_PARAMS)
    if params.get("from_root"):
        # ROOT walk (hy-l93sc slice 1): no start 'domain'/'concepts'; the walk enters the
        # synthetic root and descends, ACL-filtered per domain.
        for forbidden in ("domain", "concepts"):
            if params.get(forbidden) is not None:
                raise OperationError(
                    INVALID_PARAMS,
                    f"'{forbidden}' is not used with 'from_root': the root walk starts from the "
                    "workspace root, not a named domain",
                    recovery="send 'from_root': true with no 'domain'/'concepts', or omit "
                    "'from_root' and name a 'domain' + 'concepts' to expand from an exact node",
                )
        try:
            return expand_from_root(
                query=_required_string(params, "query"),
                session_factory=session_factory,
                max_hops=_bound(params, "max_hops", minimum=1),
                max_components=_bound(params, "max_components", minimum=1),
                context_budget=_bound(params, "context_budget", minimum=1),
                workspace=workspace,
                authorize_domain=_domain_authorizer(principal),
            )
        except ValueError as exc:
            raise OperationError(
                INVALID_PARAMS,
                str(exc),
                recovery=f"raise 'context_budget' to at least {MIN_CONTEXT_BUDGET} bytes",
            ) from exc
    concepts = _string_list(params, "concepts")
    if not concepts:
        # `concepts` is required and must be non-empty, the same bar resolve applies: a
        # start named with no coverage claim is the "say nothing, get an answer" path
        # hy-9lct closes. An empty or absent list is refused, not treated as "cover
        # nothing" (which would let expansion start from any governed domain unchecked).
        raise OperationError(
            INVALID_PARAMS,
            "'concepts' must be a non-empty list of the terms the start domain declares",
            recovery=(
                "send 'concepts' as the exact terms list_context_catalog shows for the "
                "domain you expand from"
            ),
        )
    try:
        return expand_analytics_context(
            query=_required_string(params, "query"),
            domain=_required_string(params, "domain"),
            concepts=concepts,
            session_factory=session_factory,
            max_hops=_bound(params, "max_hops", minimum=1),
            max_components=_bound(params, "max_components", minimum=1),
            context_budget=_bound(params, "context_budget", minimum=1),
            workspace=workspace,
            authorize_domain=_domain_authorizer(principal),
        )
    except ValueError as exc:
        raise OperationError(
            INVALID_PARAMS,
            str(exc),
            recovery=f"raise 'context_budget' to at least {MIN_CONTEXT_BUDGET} bytes",
        ) from exc


def _resolve(params: dict, *, session_factory, workspace: str | None = None) -> ContextBundle:
    _reject_unknown(params, _RESOLVE_PARAMS)
    return _resolve_question(params, session_factory=session_factory, workspace=workspace)


def _record_miss(params: dict, bundle: ContextBundle, *, session_factory) -> None:
    """Log a resolve outcome worth revisiting, at the transport boundary (hy-jrpm).

    Written HERE, after `_resolve` returned, so the resolver itself stays
    deterministic and side-effect-free -- the miss-log is a property of the
    serving boundary, not of resolution. Operational only: nothing written here
    is governed context, and nothing reads it to decide authority (ADR 0012, ADR
    0020 decision 5).

    Best-effort, and that is deliberate: an operational log must never gate a
    served answer. A miss-log write that fails is a line in the server log, not
    a failed resolve for the caller -- the same reason `run_operation` turns an
    unexpected resolver failure into an answer rather than a dropped connection.
    """
    warning_codes = [entry["code"] for entry in bundle.resolution.get("warnings", [])]
    if bundle.status not in (NO_MATCH, OBSERVED_ONLY) and not warning_codes:
        return
    try:
        # PII guard on the miss-log query BEFORE it persists (hy-hbtz). When the
        # guard is engaged and Presidio cannot be hosted it RAISES, and this
        # best-effort block swallows it -- so the miss is NOT persisted rather
        # than persisted unredacted. Fail closed. The guard is a no-op unless
        # HYPERSET_PII_GUARD is set, so it does not touch the default path.
        safe_query = guard_text(str(params.get("query") or ""), boundary="miss_log")
        PostgresResolveMissRepository(session_factory).record(
            query=safe_query,
            directive=params.get("directive") or {},
            status=bundle.status,
            warning_codes=warning_codes,
            bundle_id=bundle.bundle_id,
        )
    except Exception:
        traceback.print_exc(file=sys.stderr)


def _feedback_view(record) -> dict:
    return {
        "id": record.id,
        "outcome": record.outcome,
        "session_id": record.session_id,
        "correlation_id": record.correlation_id,
        "bundle_id": record.bundle_id,
        "source_ref": record.source_ref,
        "review_task_id": record.review_task_id,
        "recorded_by": record.principal_identity,
        "notes": record.notes,
        "recorded_at": _iso(record.created_at),
    }


def _feedback_token(params: dict, key: str) -> str | None:
    value = _optional_string(params, key)
    if value is not None and opaque_token(value) is None:
        raise OperationError(
            INVALID_PARAMS,
            f"'{key}' must be a well-formed opaque id",
            recovery=f"use the exact {key} returned by Hyperset, or omit it",
        )
    return value


def _record_answer_feedback(
    params: dict, *, session_factory, principal=None, workspace: str = "default"
) -> dict:
    """Append feedback only when its target exists in this request's trace chain."""
    _reject_unknown(params, _RECORD_FEEDBACK_PARAMS)
    outcome = params.get("outcome")
    if outcome not in ANSWER_FEEDBACK_OUTCOMES:
        raise OperationError(
            INVALID_PARAMS,
            f"'outcome' must be one of {', '.join(ANSWER_FEEDBACK_OUTCOMES)}",
            recovery="send one of the outcomes published in this tool's input schema",
        )
    bundle_id = _feedback_token(params, "bundle_id")
    source_ref = _optional_string(params, "source_ref")
    if bundle_id is None and source_ref is None:
        raise OperationError(
            INVALID_PARAMS,
            "feedback must identify a traced 'bundle_id' or 'source_ref'",
            recovery="send the bundle id, exact hit id, or source_id:path returned by Hyperset",
        )
    review_task_id = _feedback_token(params, "review_task_id")
    notes = _optional_string(params, "notes")
    context = current_trace_context()
    if context.session_id is None or context.correlation_id is None:
        raise OperationError(
            INVALID_PARAMS,
            "feedback requires session and correlation metadata from the traced answer",
            recovery=(
                "repeat the call with mcp-session-id (or x-hyperset-session-id) and "
                "x-correlation-id matching the search/resolve request"
            ),
        )
    if review_task_id is not None:
        task = _load_review_task(
            review_task_id, session_factory=session_factory, workspace=workspace
        )
        task_correlation = opaque_token((task.proposal_payload or {}).get("correlation_id"))
        if task_correlation != context.correlation_id:
            raise OperationError(
                INVALID_PARAMS,
                "review_task_id does not belong to this correlation chain",
                recovery="use the review task created from this traced search, or omit it",
            )
    try:
        record = PostgresAnswerFeedbackRepository(session_factory).record(
            workspace=_principal_workspace(principal),
            principal_identity=_principal_identity(principal),
            session_id=context.session_id,
            correlation_id=context.correlation_id,
            outcome=outcome,
            bundle_id=bundle_id,
            source_ref=redact_free_text_userinfo(source_ref) if source_ref is not None else None,
            review_task_id=review_task_id,
            notes=redact_free_text_userinfo(notes) if notes is not None else None,
        )
    except ValueError as exc:
        raise OperationError(
            INVALID_PARAMS,
            str(exc),
            recovery="use a hit/source or bundle from this session and correlation chain",
        ) from exc
    return {"schema_version": SCHEMA_VERSION, "feedback": _feedback_view(record)}


def _lookup_answer_feedback(params: dict, *, session_factory, principal=None) -> dict:
    """Read feedback by exact keys, workspace-scoped and never as an unbounded listing."""
    _reject_unknown(params, _LOOKUP_FEEDBACK_PARAMS)
    session_id = _feedback_token(params, "session_id")
    correlation_id = _feedback_token(params, "correlation_id")
    source_ref = _optional_string(params, "source_ref")
    review_task_id = _feedback_token(params, "review_task_id")
    if all(value is None for value in (session_id, correlation_id, source_ref, review_task_id)):
        raise OperationError(
            INVALID_PARAMS,
            "feedback lookup requires at least one exact filter",
            recovery="send session_id, correlation_id, source_ref, or review_task_id",
        )
    records = PostgresAnswerFeedbackRepository(session_factory).lookup(
        workspace=_principal_workspace(principal),
        session_id=session_id,
        correlation_id=correlation_id,
        source_ref=redact_free_text_userinfo(source_ref) if source_ref is not None else None,
        review_task_id=review_task_id,
        limit=_bound(params, "limit", minimum=1, maximum=100) or 100,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "feedback": [_feedback_view(record) for record in records],
        "count": len(records),
    }


# The operations that write a durable interaction-trace row (hy-oqevj). The
# search and the resolve are the two ends the correlation id ties together: a
# search finds candidates, the resolve that follows compiles the answer.
_TRACED_OPERATIONS = (SEARCH_KNOWLEDGE, RESOLVE)


def _duration_ms(started_at: float) -> int:
    """Elapsed boundary time as a non-negative whole millisecond."""
    return max(0, round((time.perf_counter() - started_at) * 1000))


_SEARCH_STALENESS_FIELDS = (
    "last_attempt_status",
    "last_attempt_at",
    "synced_at",
    "committed_at",
    "stale",
)
_RESOLVE_STALENESS_FIELDS = (
    "last_observed_at",
    "observed_version_at",
    "source_modified_at",
    "deleted_at",
)


def _search_source_staleness(
    hits: list[dict],
    *,
    searched_sources: list[str],
    session_factory,
    workspace: str,
) -> dict:
    """Narrow staleness for every authorized source the search actually examined."""
    result: dict[str, dict] = {}
    for hit in hits:
        source_id = hit.get("source_id")
        raw = hit.get("staleness")
        if not isinstance(source_id, str) or not isinstance(raw, dict):
            continue
        result[redact_pointer(source_id)] = {
            key: raw.get(key) for key in _SEARCH_STALENESS_FIELDS if key in raw
        }
    missing = set(searched_sources) - set(result)
    if missing:
        try:
            for candidate in PostgresContextRepository(session_factory).list_source_candidates(
                workspace=workspace
            ):
                if str(candidate.id) not in missing:
                    continue
                result[redact_pointer(str(candidate.id))] = {
                    "last_attempt_status": candidate.last_attempt_status,
                    "last_attempt_at": _iso(candidate.last_attempt_at),
                    "synced_at": _iso(candidate.synced_at),
                    "committed_at": _iso(candidate.committed_at),
                    "stale": candidate.last_attempt_status == "failed",
                }
        except Exception:
            # The search answer already exists; as with the trace write itself,
            # metadata completion is best-effort but explicitly degraded.
            _log.warning("interaction trace source staleness degraded", exc_info=True)
    return result


def _resolve_source_staleness(bundle: ContextBundle) -> dict:
    """Narrow freshness metadata for observed sources actually served by resolve."""
    result: dict[str, dict] = {}
    for item in (bundle.linked_evidence or {}).get("freshness", []):
        if not isinstance(item, dict) or not isinstance(item.get("ref"), str):
            continue
        ref = redact_pointer(item["ref"])
        narrow = {key: item.get(key) for key in _RESOLVE_STALENESS_FIELDS if key in item}
        narrow["stale"] = bool(item.get("deleted_at"))
        result[ref] = narrow
    return result


def _search_miss_detail(payload: dict) -> dict:
    """Explicitly name the authorized sources searched with zero returned hits."""
    return {
        "operation": SEARCH_KNOWLEDGE,
        "searched_sources": [
            redact_pointer(str(source_id)) for source_id in payload.get("searched_sources") or []
        ],
    }


def _resolve_miss_detail(params: dict) -> dict:
    """Explicitly name the exact directive targets a no-match resolve searched."""
    directive = params.get("directive") if isinstance(params.get("directive"), dict) else {}
    detail: dict[str, object] = {"operation": RESOLVE}
    for key in ("domains", "concepts", "asset_refs"):
        values = directive.get(key)
        if isinstance(values, list):
            detail[key] = [redact_pointer(str(value)) for value in values if isinstance(value, str)]
    return detail


def _redact_trace_text(value) -> str | None:
    """Redact one free-text field for the DURABLE trace, or None.

    UNCONDITIONAL canonical redaction (hy-oqevj dual-block fix 2): a permanent,
    queryable audit table must strip caller credentials REGARDLESS of
    HYPERSET_PII_GUARD -- the guard's no-op-when-unset default is not an acceptable
    posture for a durable store. `redact_free_text_userinfo` is pure and NEVER raises,
    so a `scheme://user:secret@host` anywhere in the text is stripped before it lands,
    on every deployment, with no env flag in the path."""
    if not value:
        return None
    return redact_free_text_userinfo(str(value))


# The ONLY filter fields the search consumes (`_path_allowed`): a substring `path` and a
# `path_prefix`. The durable trace persists a NARROW, redacted projection of exactly these
# -- never the raw caller dict -- so an arbitrary key like `filters={"token": "...secret..."}`
# lands NOTHING in `mcp_interaction_trace.filters` (hy-oqevj dual-block fix 1).
_TRACED_FILTER_FIELDS = ("path", "path_prefix")


def _redact_trace_filters(filters) -> dict:
    """A narrow, redacted projection of the caller's filters for the durable trace.

    Keeps ONLY the validated `path`/`path_prefix` fields, as redacted strings, and drops
    every other key -- so no raw caller-controlled dict (and no secret hidden in an unknown
    key) is ever persisted."""
    if not isinstance(filters, dict):
        return {}
    narrow: dict[str, str] = {}
    for field_name in _TRACED_FILTER_FIELDS:
        value = filters.get(field_name)
        if isinstance(value, str) and value:
            narrow[field_name] = redact_free_text_userinfo(value)
    return narrow


def _record_interaction_trace(
    name: str,
    params: dict,
    *,
    principal,
    status: str,
    hit_ids: list[str],
    duration_ms: int,
    source_staleness: dict,
    miss: dict | None,
    answer_bundle_id: str | None,
    session_factory,
) -> None:
    """Persist one durable MCP interaction-trace row at the serving boundary (hy-oqevj).

    Written HERE, after dispatch, so the traced operations stay side-effect-free
    -- the trace is a property of the boundary, like the miss-log. Operational
    audit only: nothing here is governed context or decides authority (ADR 0012).

    Identity is SERVER-DERIVED (`_principal_identity`/`_principal_workspace`),
    never a caller header. The linkage ids come from the transport-bound trace
    context; the correlation id is minted when the caller supplied none, so every
    row is correlatable. `query`/`intent` are REDACTED, and `hit_ids` are opaque
    location ids the caller was AUTHORIZED to see -- a denied call carries none.

    Best-effort for the ANSWER, but NOT silent for the OPERATOR: a failed write
    is logged as DEGRADED (a warning naming the tool, status, and correlation),
    so the trace is explicitly reported as degraded rather than dropped. It never
    gates the served answer.
    """
    context = current_trace_context()
    # The linkage ids on `context` are ALREADY validated to the opaque-token shape (or
    # dropped) by `trace_context_from_headers`, so no raw caller header can be here. The
    # correlation id is a validated caller token, the request's own minted id, or a fresh
    # server mint -- all opaque, so it is safe to persist AND to name in the degraded log.
    correlation_id = context.correlation_id or current_correlation_id() or new_correlation_id()
    # Canonical, UNCONDITIONAL redaction of the caller's free text before it lands in the
    # durable row (fixes 1+2): query/intent stripped of credentials on every deployment, and
    # filters projected to only the validated path fields -- never the raw caller dict.
    query = _redact_trace_text(params.get("query"))
    # HTTP callers may declare intent in trace metadata; MCP/stdio callers can provide the
    # same declaration as the search argument because stdio has no request headers. Keep it
    # audit-only: it never affects ranking, retrieval, or authorization.
    intent = _redact_trace_text(context.intent or params.get("intent"))
    # HTTP headers are optional, and stdio has no headers at all. Every persisted trace still
    # gets per-call linkage so a write-back reviewer can distinguish calls in one session.
    turn_id = context.turn_id or new_correlation_id()
    tool_call_id = context.tool_call_id or new_correlation_id()
    filters = _redact_trace_filters(params.get("filters")) if name == SEARCH_KNOWLEDGE else {}
    try:
        PostgresInteractionTraceRepository(session_factory).record(
            workspace=_principal_workspace(principal),
            principal_identity=_principal_identity(principal),
            session_id=context.session_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            correlation_id=correlation_id,
            intent=intent,
            query=query,
            tool_name=name,
            search_mode=params.get("mode") if name == SEARCH_KNOWLEDGE else None,
            filters=filters,
            hit_ids=list(hit_ids or []),
            duration_ms=duration_ms,
            source_staleness=source_staleness,
            miss=miss,
            answer_bundle_id=answer_bundle_id,
            status=status,
        )
    except Exception:
        # DEGRADED, explicitly reported -- never a silent drop (Overseer: "make the
        # event chain durable OR explicitly report degraded logging"). The answer is
        # unaffected; only the audit trail is.
        _log.warning(
            "mcp interaction trace degraded: tool=%s status=%s correlation=%s not persisted",
            name,
            status,
            correlation_id,
            exc_info=True,
        )


def _bundle_citations(bundle: ContextBundle) -> list[tuple[str, str, str | None]]:
    """The citations a governed answer drew on, as (citation_ref, kind, source_ref).

    Two kinds, both ALREADY on the served ContextBundle (so mirroring them adds no
    served key): each `provenance_refs` entry (the Git-owned context that authorized
    the answer) and each `instructions.approved_sources` ref (the dataset the answer
    names). Opaque refs only -- never a snippet.

    Defense-in-depth for the DURABLE row (hy-cpkvu): every ref is passed through the
    canonical URL-userinfo redactor before it is returned, so a governed source URL that
    ever carried credentials cannot land them in `answer_citations`. Pure, never raises."""
    citations: list[tuple[str, str, str | None]] = []
    for ref in bundle.provenance_refs or []:
        if isinstance(ref, str) and ref:
            citations.append((redact_pointer(ref), "provenance", None))
    approved = (bundle.instructions or {}).get("approved_sources") or []
    for entry in approved:
        ref = entry.get("ref") if isinstance(entry, dict) else None
        if isinstance(ref, str) and ref:
            safe = redact_pointer(ref)
            citations.append((safe, "approved_source", safe))
    return citations


def _record_answer_citations(bundle: ContextBundle, *, principal, session_factory) -> None:
    """Persist the citation<->answer links for one governed answer (hy-cpkvu).

    Written at the serving boundary, like the interaction trace, keyed by the same
    correlation id so a search->resolve->citations chain is reconstructable. Audit only
    (ADR 0012); it mirrors bundle fields, decides no authority. Best-effort for the
    answer but DEGRADED-explicit for the operator: a failed write is a warning, never a
    silent drop and never a gate on the served answer."""
    correlation_id = (
        current_trace_context().correlation_id or current_correlation_id() or new_correlation_id()
    )
    try:
        repository = PostgresAnswerCitationRepository(session_factory)
        for citation_ref, kind, source_ref in _bundle_citations(bundle):
            repository.record(
                workspace=_principal_workspace(principal),
                correlation_id=correlation_id,
                bundle_id=bundle.bundle_id,
                citation_ref=citation_ref,
                citation_kind=kind,
                source_ref=source_ref,
            )
    except Exception:
        _log.warning(
            "answer citation linkage degraded: bundle=%s correlation=%s not persisted",
            bundle.bundle_id,
            correlation_id,
            exc_info=True,
        )


def _citation_decision_view(record) -> dict:
    """The served shape of a recorded citation decision. Redacted notes only; the
    principal is the server-derived opaque identity, never a raw claim."""
    return {
        "id": record.id,
        "decision": record.decision,
        "citation_ref": record.citation_ref,
        "source_ref": record.source_ref,
        "review_task_id": record.review_task_id,
        "correlation_id": record.correlation_id,
        "decided_by": record.principal_identity,
        "superseded_by": record.superseded_by,
        "notes": record.notes,
        "decided_at": _iso(record.created_at),
    }


def _decide_citation(
    params: dict, *, session_factory, principal=None, workspace: str = "default"
) -> dict:
    """Record a human include/exclude/approve/reject on a citation (hy-cpkvu, epic
    slice 3). Served ONLY as a bespoke HTTP route (off OPERATIONS/MCP).

    FAIL-CLOSED at the SERVICE, not just the route: the REVIEW gate is decided here, so a
    direct call (bypassing the handler) is denied too -- a principal not authorized for
    the review surface cannot record a decision, so a decision on a denied item is
    impossible. Identity is SERVER-DERIVED (`_principal_identity`), never caller free text
    (set_review_assignee discipline). All caller text is redacted UNCONDITIONALLY for the
    DURABLE row (same discipline as the #503 trace, hy-oqevj dual-block): `notes` and the
    refs get canonical URL-userinfo redaction (not the env-gated guard), and the linkage
    ids are validated to an opaque-token shape. The write is idempotent by SUPERSEDE
    (latest-wins): re-submitting supersedes the prior live decision. It writes only the
    internal audit store: no governed row, no resolve, no SQL (ADR 0012)."""
    denial = review_surface_authorization_error(principal)
    if denial is not None:
        raise denial
    _reject_unknown(params, _DECIDE_CITATION_PARAMS)
    decision = _required_string(params, "decision")
    if decision not in CITATION_DECISIONS:
        raise OperationError(
            INVALID_PARAMS,
            f"'decision' must be one of {', '.join(CITATION_DECISIONS)}",
            recovery='send {"citation_ref": ..., "decision": "include|exclude|approve|reject"}',
        )
    citation_ref = _required_string(params, "citation_ref")
    source_ref = params.get("source_ref")
    review_task_id = params.get("review_task_id")
    correlation_id = params.get("correlation_id")
    for key, value in (
        ("source_ref", source_ref),
        ("review_task_id", review_task_id),
        ("correlation_id", correlation_id),
    ):
        if value is not None and not isinstance(value, str):
            raise OperationError(
                INVALID_PARAMS, f"'{key}' must be a string", recovery="send a string or omit it"
            )
    # The linkage ids are opaque tokens: a review_task_id must be a well-formed id (it is an
    # FK too), so a malformed one is rejected; a malformed correlation link is dropped rather
    # than persisted raw. So no credential-bearing string reaches these columns.
    if review_task_id is not None and opaque_token(review_task_id) is None:
        raise OperationError(
            INVALID_PARAMS,
            "'review_task_id' must be a well-formed task id",
            recovery="use the id from list_review_tasks, or omit it",
        )
    if review_task_id is not None:
        _require_mutable_review_task(
            _load_review_task(review_task_id, session_factory=session_factory, workspace=workspace)
        )
    correlation_id = opaque_token(correlation_id) or current_trace_context().correlation_id
    # UNCONDITIONAL canonical redaction of every caller free-text field before it lands in
    # the durable row -- notes and the refs alike (dual-block discipline). Pure, never raises.
    notes = redact_free_text_userinfo(str(params.get("notes"))) if params.get("notes") else None
    record = PostgresCitationDecisionRepository(session_factory).record(
        workspace=_principal_workspace(principal),
        principal_identity=_principal_identity(principal),
        decision=decision,
        citation_ref=redact_free_text_userinfo(citation_ref),
        source_ref=redact_free_text_userinfo(source_ref) if source_ref else None,
        review_task_id=review_task_id,
        correlation_id=correlation_id,
        notes=notes,
    )
    if correlation_id is not None:
        try:
            PostgresInteractionTraceRepository(session_factory).link_decision(
                workspace=_principal_workspace(principal),
                correlation_id=correlation_id,
                decision_id=record.id,
            )
        except Exception:
            _log.warning(
                "interaction decision linkage degraded: decision=%s correlation=%s",
                record.id,
                correlation_id,
                exc_info=True,
            )
    return {"schema_version": SCHEMA_VERSION, "decision": _citation_decision_view(record)}


# The sole acl_decision value search_knowledge emits for an ADMITTED hit (a denied hit is
# absent, never returned -- hy-r0szz). A proposal may cite only admitted hits, so this is the
# fail-closed gate: a hit not marked admitted is rejected, and a proposal can never cite an
# ACL-denied item. Bound to the search producer by test_only_an_admitted_hit_may_be_cited.
_ADMITTED_ACL = "allowed"


def _redact_definition(value):
    """Deep-redact every string LEAF of a proposed context change before it lands in the
    durable proposal_payload (hy-27nl6). `redact_free_text_userinfo` strips `scheme://
    userinfo@` anywhere, so a source ref that ever carried credentials cannot persist them --
    UNCONDITIONALLY, no HYPERSET_PII_GUARD in the path (dual-block discipline). Pure, never
    raises: dicts and lists are walked, strings redacted, everything else passed through."""
    if isinstance(value, dict):
        return {k: _redact_definition(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_definition(v) for v in value]
    if isinstance(value, str):
        return redact_free_text_userinfo(value)
    return value


def _citation_from_hit(hit: dict) -> dict | None:
    """Reduce one search_knowledge hit to the OPAQUE provenance a proposal cites -- the
    source id, path, line, and commit that LOCATE the evidence -- with NEVER the snippet
    (ACL-guarded content) or any credential. Every textual field is redacted at the boundary
    for the durable proposal_payload.

    Returns None for an INCOMPLETE hit (hy-27nl6 blocker 2): the promised provenance is the
    full source_id/path/line/commit tuple, so a missing/non-string source_id, path or commit,
    or a non-int line is rejected here -- the caller raises rather than persisting a partial
    citation. `bool(line)` is not required, so line 0 is a valid line."""
    source_id = hit.get("source_id")
    path = hit.get("path")
    commit = hit.get("commit")
    line = hit.get("line")
    if not (isinstance(source_id, str) and source_id):
        return None
    if not (isinstance(path, str) and path):
        return None
    if not (isinstance(commit, str) and commit):
        return None
    if not isinstance(line, int) or isinstance(line, bool):
        return None
    return {
        "source_id": redact_free_text_userinfo(source_id),
        "path": redact_free_text_userinfo(path),
        "line": line,
        "commit": redact_free_text_userinfo(commit),
    }


def _proposal_idempotency_key(
    workspace: str, proposer: str, domain: str, definition: dict, citations: list[dict]
) -> str:
    """A DETERMINISTIC idempotency key over the proposer + workspace + domain + the proposed
    change + the cited hits (hy-27nl6), so re-submitting the SAME proposal returns the SAME
    task rather than opening a duplicate. Server-derived from a canonical JSON digest -- never
    a caller-supplied idempotency token, and never the raw content (only its hash)."""
    material = json.dumps(
        {
            "workspace": workspace,
            "proposer": proposer,
            "domain": domain,
            "definition": definition,
            "citations": citations,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "propose-search:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _governed_domains(session_factory, workspace: str) -> set[str]:
    """The governed domains configured IN the caller's workspace (hy-27nl6) -- the current
    snapshot domain of each enabled source. A proposal may name only one of these; validating
    against it (not merely redacting) is what stops a caller-controlled domain string -- e.g. a
    credential-bearing URL -- from ever being persisted or routed.

    Reads via `list_source_candidates`, the METADATA-only listing (hy-r0szz, #500): it selects
    only the identity + snapshot-metadata columns and NEVER `context_snapshots.files`, so
    computing this set reads no source CONTENT -- it does not reintroduce an
    authorize-before-content leak. Only the domain metadata is needed; the bytes are not."""
    candidates = PostgresContextRepository(session_factory).list_source_candidates(
        workspace=workspace
    )
    return {
        candidate.domain
        for candidate in candidates
        if candidate.enabled and candidate.domain is not None
    }


def _propose_context_from_search(
    params: dict, *, session_factory, principal=None, workspace: str = "default"
) -> dict:
    """Open a PROPOSAL-ONLY write-back review task from selected search hits + a proposed
    context change (hy-27nl6, epic slice 4 CAPSTONE). Served ONLY as a bespoke HTTP route
    (off OPERATIONS/MCP), so it publishes no MCP tool and moves no tools_hash/SCHEMA_VERSION.

    This is the FRONT HALF of the search->writeback loop: it turns search_knowledge hits and a
    proposed context change into a human ReviewTask (status 'open') whose proposal_payload
    carries the proposed `definition`, its `domain`, the originating hit CITATIONS (opaque
    provenance, NEVER a snippet), the #503 correlation, and the routed write-back TARGET.

    It NEVER exercises direct authority (ADR 0012): it does not approve, does not write a
    governed version, does not open a PR, and does not merge. A human still drives
    propose_review_to_git -> PR and the eventual merge from this task -- which is why the
    payload's `definition`/`domain` are exactly the keys that op reads.

    FAIL-CLOSED at the SERVICE, not just the route: the REVIEW gate is decided here, so a
    direct call is denied too. The proposer is SERVER-DERIVED (`_principal_identity`), never
    caller free text. Routing is scoped to the caller's workspace (`_principal_workspace`):
    the deterministic-id lesson from #504 -- an unscoped read is cross-tenant by construction
    -- so get_by_routing never crosses tenants and fails closed with no target. Every durable
    caller field (the definition refs, the hit provenance, notes) is redacted UNCONDITIONALLY.
    The write is idempotent on a server-derived deterministic key, so a re-submit is a no-op."""
    from hyperset.context.errors import ContextValidationError
    from hyperset.context.schema import validate_definition_draft

    denial = review_surface_authorization_error(principal)
    if denial is not None:
        raise denial
    _reject_unknown(params, _PROPOSE_FROM_SEARCH_PARAMS)
    domain = _required_string(params, "domain")
    definition = params.get("definition")
    if not isinstance(definition, dict) or not definition:
        raise OperationError(
            INVALID_PARAMS,
            "'definition' must be a non-empty object carrying the proposed context change",
            recovery='send {"domain": ..., "definition": {...}, "hits": [...]}',
        )
    raw_hits = params.get("hits")
    if not isinstance(raw_hits, list) or not raw_hits:
        raise OperationError(
            INVALID_PARAMS,
            "'hits' must be a non-empty list of the search_knowledge hits this proposal cites",
            recovery="pass the hit(s) from search_knowledge that motivate this change",
        )
    # FAIL-CLOSED on ACL: only an ADMITTED hit may be cited, and only its OPAQUE provenance is
    # persisted -- never the snippet. A hit not marked admitted (or a hand-crafted denied one)
    # is rejected, so a proposal can never cite an ACL-denied item.
    citations: list[dict] = []
    for hit in raw_hits:
        if not isinstance(hit, dict):
            raise OperationError(
                INVALID_PARAMS,
                "each hit must be a search_knowledge hit object",
                recovery="pass the hits from search_knowledge verbatim",
            )
        if hit.get("acl_decision") != _ADMITTED_ACL:
            raise OperationError(
                INVALID_REQUEST,
                "a proposal may cite only ADMITTED search hits",
                recovery=(
                    "cite only hits returned by search_knowledge (denied items are never returned)"
                ),
            )
        citation = _citation_from_hit(hit)
        if citation is None:
            raise OperationError(
                INVALID_PARAMS,
                "each hit must carry a string source_id, path and commit, and an integer line",
                recovery="pass the hits from search_knowledge verbatim",
            )
        citations.append(citation)
    # The proposed change faces the SAME structural rules a human's Git commit faces at sync
    # time (reusing validate_definition_draft), so an invalid draft is refused here and never
    # persisted, and only the KNOWN definition fields land -- no arbitrary caller dict.
    try:
        validate_definition_draft(definition, domain=domain)
    except ContextValidationError as exc:
        raise OperationError(
            INVALID_REQUEST,
            f"the proposed change is not a valid context definition: {'; '.join(exc.reasons)}",
            recovery="the definition must satisfy the manifest rules",
        ) from exc
    safe_definition = _redact_definition(definition)
    trace_context = current_trace_context()
    correlation_id = opaque_token(params.get("correlation_id")) or trace_context.correlation_id
    session_id = opaque_token(params.get("session_id")) or trace_context.session_id
    if correlation_id is None or session_id is None:
        raise OperationError(
            INVALID_REQUEST,
            "a proposal must be linked to a traced search session and correlation chain",
            recovery="send the session and correlation metadata from search_knowledge",
        )
    # A caller must cite a hit that this workspace actually returned from a traced search.
    # The hit envelope is caller-controlled JSON, so acl_decision=allowed alone is not
    # evidence. Compare opaque locations against the durable search trace and require the
    # same session/correlation chain before any task is written.
    traced_hits = {
        hit_id
        for trace in PostgresInteractionTraceRepository(session_factory).for_correlation(
            workspace=workspace, correlation_id=correlation_id
        )
        if trace.session_id == session_id
        and trace.tool_name == SEARCH_KNOWLEDGE
        and trace.status == "hit"
        for hit_id in trace.hit_ids
    }
    if any(
        f"{citation['source_id']}:{citation['path']}:{citation['line']}" not in traced_hits
        for citation in citations
    ):
        raise OperationError(
            INVALID_REQUEST,
            "each proposal hit must come from a traced search in this session and "
            "correlation chain",
            recovery="reuse the exact hits returned by search_knowledge for this trace",
        )
    notes = redact_free_text_userinfo(str(params.get("notes"))) if params.get("notes") else None
    proposer = _principal_identity(principal)
    # VALIDATE the domain against the workspace's GOVERNED domain set and FAIL CLOSED on
    # anything else (hy-27nl6 blocker 1) -- BEFORE it is routed or persisted. The caller's
    # `domain` lands verbatim in ReviewTask.reason + proposal_payload["domain"], so an
    # unvalidated one (e.g. "https://u:secret@host") would leak a credential via a default
    # target. Validating (not merely redacting) also means a proposal for an unknown domain
    # fails closed regardless of whether a default target exists. The message names the
    # redacted domain so the refusal itself carries no credential.
    if domain not in _governed_domains(session_factory, workspace):
        raise OperationError(
            INVALID_REQUEST,
            f"no governed domain {redact_free_text_userinfo(domain)!r} is configured "
            "for this workspace",
            recovery="propose a change for a domain the catalog lists (see list_context_catalog)",
        )
    # Route to the write-back TARGET this domain belongs to, WITHIN the caller's workspace
    # (hq-t6nx, #504 lesson): an enabled keyed target of this workspace, else its default,
    # else FAIL CLOSED -- never another tenant's target, never a fall-through.
    config = PostgresWritebackConfigRepository(session_factory).get_by_routing(
        domain, workspace=workspace
    )
    if config is None:
        raise OperationError(
            INVALID_REQUEST,
            f"no write-back target is configured for domain {redact_free_text_userinfo(domain)!r}",
            recovery=(
                "add a write-back target whose routing key is this domain, or a default "
                "target, in the admin panel first"
            ),
        )
    proposal_payload = {
        # `definition` + `domain` are exactly the keys propose_review_to_git reads to open the
        # proposal PR later, so a human can drive this task straight to a Git PR.
        "definition": safe_definition,
        "domain": domain,
        # The originating search hits, as OPAQUE citations (no snippet); correlation ties the
        # proposal back to the #503 trace so the search->writeback chain is reconstructable.
        "citations": citations,
        "correlation_id": correlation_id,
        # SERVER-DERIVED proposer on the durable trail, never caller text.
        "proposer": proposer,
        # The routed target, recorded PROPOSAL-ONLY: a fixed 'proposal_only' status (NOT a
        # discovery/proposal OUTCOME token). Opening this task neither approves nor merges;
        # emitting the PR is a later explicit human action.
        "review_routing": {
            "status": "proposal_only",
            "target": {
                "id": config.id,
                "routing_key": config.routing_key,
                "repository": redact_pointer(config.repository),
                "base_ref": config.base_ref,
            },
        },
        "source": "search",
    }
    if notes is not None:
        proposal_payload["notes"] = notes
    # Idempotent on a server-derived deterministic key: a re-submit of the SAME proposal
    # returns the SAME task (create_task upserts on the key) rather than opening a duplicate.
    idempotency_key = _proposal_idempotency_key(
        workspace, proposer, domain, safe_definition, citations
    )
    task = PostgresReviewRepository(session_factory).create_task(
        reason=f"proposal-only context change for domain {domain!r} from search",
        idempotency_key=idempotency_key,
        workspace=workspace,
        proposal_payload=proposal_payload,
    )
    return {"schema_version": SCHEMA_VERSION, "task": _review_task_view(task)}


def _validate(params: dict, *, session_factory, workspace: str | None = None) -> PlanValidation:
    _reject_unknown(params, _RESOLVE_PARAMS + _PLAN_PARAMS)
    # The plan is decoded before the question is re-asked: a malformed
    # request should cost a rejection, not a resolution.
    plan = AnalyticsPlan(
        bundle_id=_required_string(params, "bundle_id"),
        source_refs=_string_list(params, "source_refs"),
        fields=_entry_list(params, "fields"),
        joins=_entry_list(params, "joins"),
        filters=_entry_list(params, "filters"),
        grain=_optional_string(params, "grain"),
        checks=_entry_list(params, "checks"),
    )
    # VALIDATE scopes its internal resolve to the caller's workspace (hq-t6nx #438
    # round 2): unlike the public RESOLVE op (data scoping deferred per ADR-0037),
    # a validation bundle built from a SIBLING tenant's source would be a
    # cross-tenant leak, so the plan is validated against ONLY this tenant's estate.
    bundle = _resolve_question(params, session_factory=session_factory, workspace=workspace)
    return validate_analytics_plan(bundle=bundle, plan=plan)


def _resolve_question(
    params: dict, *, session_factory, workspace: str | None = None
) -> ContextBundle:
    return resolve_analytics_context(
        query=_required_string(params, "query"),
        directive=_directive(params),
        session_factory=session_factory,
        workspace=workspace,
    )


def _directive(params: dict) -> ContextDirective:
    """Decode the planner's directive, and refuse an empty one.

    Refused at the door rather than answered with an empty bundle: an agent
    that sent no selection has a next action to take, and the recovery names
    it. Guessing one from the question is the behaviour #70 removed.
    """
    raw = params.get("directive")
    if raw is None:
        raise OperationError(
            DIRECTIVE_REQUIRED,
            "'directive' is required: Hyperset retrieves what a directive names",
            recovery=PLAN_FIRST,
        )
    if not isinstance(raw, dict):
        raise OperationError(
            INVALID_PARAMS,
            f"'directive' must be an object, got {type(raw).__name__}",
            recovery=f"send 'directive' as an object with keys from: {', '.join(_DIRECTIVE_KEYS)}",
        )
    unknown = sorted(set(raw) - set(_DIRECTIVE_KEYS))
    if unknown:
        raise OperationError(
            UNKNOWN_PARAMETER,
            f"unknown directive key(s): {', '.join(unknown)}",
            recovery=f"remove them; a directive accepts: {', '.join(_DIRECTIVE_KEYS)}",
        )
    try:
        directive = ContextDirective(
            domains=_string_list(raw, "domains"),
            asset_refs=_string_list(raw, "asset_refs"),
            concepts=_string_list(raw, "concepts"),
            max_hops=_bound(raw, "max_hops", minimum=0),
            context_budget=_bound(raw, "context_budget", minimum=1),
        )
    except ValueError as unpaired:
        # 'concepts' without 'domains' (hy-9lct). The directive refuses that
        # pairing at construction, so every caller gets the same answer; this
        # only puts it into the served error vocabulary.
        raise OperationError(
            INVALID_PARAMS,
            str(unpaired),
            recovery=f"call {CATALOG} and send 'domains' and 'concepts' together",
        ) from unpaired
    if directive.is_empty:
        raise OperationError(
            DIRECTIVE_REQUIRED,
            "the directive names no domains and no asset_refs, so there is nothing to retrieve",
            recovery=PLAN_FIRST,
        )
    return directive


def _bound(params: dict, key: str, *, minimum: int, maximum: int | None = None) -> int | None:
    value = params.get(key)
    if value is None:
        return None
    # `True` is an int in Python, and a bound of 1 hop asked for as `true` is
    # a mistake worth naming rather than honouring.
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        allowed = f">= {minimum}" if maximum is None else f"between {minimum} and {maximum}"
        # A limit past the cap is refused rather than quietly clamped: a
        # caller that asked for everything and was served a page has to be
        # able to tell.
        raise OperationError(
            INVALID_PARAMS,
            f"{key!r} must be an integer {allowed} or omitted, got {value!r}",
            recovery=(
                f"send {key!r} as an integer {allowed}"
                + (", or omit it for no bound" if maximum is None else "")
            ),
        )
    return value


def _reject_unknown(params: dict, accepted: tuple[str, ...]) -> None:
    if not isinstance(params, dict):
        raise OperationError(
            INVALID_PARAMS,
            f"parameters must be an object, got {type(params).__name__}",
            recovery=f"send an object with keys from: {', '.join(accepted)}",
        )
    unknown = sorted(set(params) - set(accepted))
    if unknown:
        # A silently ignored typo would answer a different question than the
        # agent asked and look like a correct answer.
        raise OperationError(
            UNKNOWN_PARAMETER,
            f"unknown parameter(s): {', '.join(unknown)}",
            recovery=(
                f"remove them; accepted parameters are: {', '.join(accepted)}"
                if accepted
                else "remove them; this operation takes no parameters -- send {}"
            ),
        )


# What to do about each required parameter, in the terms the agent is
# working in rather than the schema's.
_REQUIRED_RECOVERY = {
    "query": (
        f"send 'query' as the analytics question, e.g. {_QUERY_EXAMPLE!r}. It is recorded "
        f"with the answer; what gets retrieved is what 'directive' names"
    ),
    "bundle_id": (
        "send 'bundle_id' as the id resolve_analytics_context returned for this "
        "question; without it the plan cannot be checked against the bundle it was "
        "built from. Resolve the question again if you no longer have it"
    ),
    "task_id": "send 'task_id' as the id of a review task, e.g. one from list_review_tasks",
    "domain": (
        "send 'domain' as an exact governed domain name from list_context_catalog to "
        "expand from; it must declare the 'concepts' you claim"
    ),
}


def _required_string(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OperationError(
            INVALID_PARAMS,
            f"{key!r} must be a non-empty string, got {value!r}",
            recovery=_REQUIRED_RECOVERY[key],
        )
    return value


def _optional_string(params: dict, key: str) -> str | None:
    value = params.get(key)
    if value is None or (isinstance(value, str) and value.strip()):
        return value
    raise OperationError(
        INVALID_PARAMS,
        f"{key!r} must be a non-empty string or omitted, got {value!r}",
        recovery=f"omit {key!r} or send a non-empty string",
    )


def _string_list(params: dict, key: str) -> list[str]:
    values = _list(params, key)
    if any(not isinstance(value, str) for value in values):
        example = "['revenue']" if key == "domains" else f"['{_REF_EXAMPLE}']"
        raise OperationError(
            INVALID_PARAMS,
            f"{key!r} must be a list of strings, got {values!r}",
            recovery=f"send {key!r} as a list of strings, e.g. {example}",
        )
    return values


def _entry_list(params: dict, key: str) -> list:
    """Plan entries are strings or the bundle's own mappings: an agent that
    echoes an instruction back is compared on every attribute it echoed."""
    values = _list(params, key)
    if any(not isinstance(value, str | dict) for value in values):
        raise OperationError(
            INVALID_PARAMS,
            f"{key!r} must be a list of strings or objects, got {values!r}",
            recovery=(
                f"send {key!r} as a list, either of names or of the bundle's own "
                f"instruction entries"
            ),
        )
    return values


def _list(params: dict, key: str) -> list:
    values = params.get(key)
    if values is None:
        return []
    if not isinstance(values, list):
        raise OperationError(
            INVALID_PARAMS,
            f"{key!r} must be a list, got {type(values).__name__}",
            recovery=f"send {key!r} as a JSON array, or omit it",
        )
    return values


# The flywheel Review operations (hy-jis1). Served first-class on BOTH transports
# through `run_operation`, so the agent surface and the operator /review page read
# one shape. They read and mutate ONLY the step-4 UNAPPROVED assist draft, or
# propose it as a Git PR and stop -- none approves, merges, writes a governed row,
# or runs SQL (ADR 0012). A NotFoundError, a ContextValidationError, or a
# GitProposalError leaves here as `INVALID_REQUEST`, the same shape the bespoke
# HTTP handlers returned; a bad or missing param is `INVALID_PARAMS`.


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


# A remote target (URL/SSH) needs a server-side credential the browser must
# never hold, so the interactive write-back supports LOCAL PATHS only for now;
# a URL is refused with a pointer to the secret-referenced follow-on (hy-eji4).
_REMOTE_PREFIXES = ("http://", "https://", "git://", "ssh://", "git@")


def _is_local_path(repository: str) -> bool:
    return not repository.strip().lower().startswith(_REMOTE_PREFIXES)


# The model runtime the refine re-run drives (hy-murb) is INJECTED by the server
# builders, never imported here (hy-jis1). This module is reachable from the
# reporting path (hyperset.evals.stability, via the planner executor), which must
# stay one import away from no inference runtime (test_report_time_purity). So
# operations names no inference module; `build_server`/`build_mcp_server` set the
# factory from `hyperset.transport.review_runtime`, and a test substitutes a
# `ScriptedRuntime` by patching `_authoring_runtime` directly.
_AUTHORING_RUNTIME_FACTORY = None


def _authoring_runtime():
    """The injected authoring runtime, or a wired-transport error if absent."""
    if _AUTHORING_RUNTIME_FACTORY is None:
        raise OperationError(
            INTERNAL_ERROR,
            "the refine operation has no model runtime wired on this server",
            recovery="serve through build_server or the MCP server, which wire it",
        )
    return _AUTHORING_RUNTIME_FACTORY()


# The proposal-only Git writer is INJECTED for the same reason as the runtime:
# it reaches `subprocess` through `git_pr`, and this module is on the reporting
# path that must not. The writer returns the proposal dict and maps its own
# ContextValidationError/GitProposalError to OperationError.
_PROPOSE_WRITER = None


def _propose_writer(**kwargs) -> dict:
    """The injected proposal-only Git writer, or a wired-transport error."""
    if _PROPOSE_WRITER is None:
        raise OperationError(
            INTERNAL_ERROR,
            "the propose operation has no git writer wired on this server",
            recovery="serve through build_server or the MCP server, which wire it",
        )
    return _PROPOSE_WRITER(**kwargs)


def _resolve_reviewers(reviewer_routing: str | None) -> list[str]:
    """The ordered, de-duplicated reviewer handles a target's `reviewer_routing`
    names (hq-1rq7).

    Comma-separated handles; empty/None yields `[]`, which the caller records as
    an explicit needs-routing state -- never an auto-approve. Order and first-seen
    de-duplication are preserved so the recorded routing reads as the operator
    wrote it.
    """
    if not reviewer_routing:
        return []
    seen: dict[str, None] = {}
    for handle in reviewer_routing.split(","):
        stripped = handle.strip()
        if stripped and stripped not in seen:
            seen[stripped] = None
    return list(seen)


def _task_domain(task) -> str | None:
    """The governed domain a review task concerns, read from its proposal payload the
    same way `_edit_review_draft` does: the top-level `domain`, or a miss's `domain`.
    None when the payload names neither."""
    payload = task.proposal_payload or {}
    return payload.get("domain") or (payload.get("miss") or {}).get("domain")


SUGGESTION_SIGNAL = "prior_in_domain_reviewer"
SUGGESTION_SUMMARY = "Most recent prior reviewer in this governed domain."


def _suggestion_rationale() -> dict:
    """The deterministic, ASSIST-labeled explanation served beside a suggestion (hy-38mk8
    r2), so an MCP/HTTP consumer sees WHY a hint was made instead of an opaque id. The
    signal is fixed (there is one signal), so the rationale is a constant naming it."""
    return {"signal": SUGGESTION_SIGNAL, "summary": SUGGESTION_SUMMARY, "assist": True}


def _suggested_owner(task, tasks) -> str | None:
    """An assist-class owner HINT for an unassigned task (hy-38mk8, S3).

    The likely reviewer, inferred from prior in-domain reviews: the assignee of the
    most-recently-created OTHER task in the SAME governed domain that already carries
    an owner. It is a SUGGESTION a human overrides -- confirmed only by an explicit
    `set_review_assignee` (S1) -- NEVER an auto-assignment or an approval (ADR 0019:
    assist may reason, governance may not). Returns None when the task already has an
    owner, has no domain, or no eligible same-domain candidate exists, so the surface
    stays byte-identical whenever there is nothing to suggest.

    A candidate must be a KNOWN APPROVED reviewer, not merely a truthy assignee (hy-38mk8
    r2): the identity is filtered through `reviewer_allowlist()` membership, so the shared
    `anonymous` id (authz off) and any legacy PII/credential-shaped row are excluded --
    redaction at the view is not enough, the suggested identity itself must be an approved
    principal. The allowlist is the Git-owned registry and is ITSELF grammar-validated and
    fail-closed, so membership already means a well-formed opaque `subject@issuer`. When no
    policy is configured (`None`, unset) or it is empty/misconfigured (`frozenset()`), there
    is no registry to trust and there is NO suggestion at all. Ties on `created_at` break by
    id, so the hint is deterministic.

    Gated on `authz_enabled()` FIRST (hy-38mk8 r2): the allowlist is only meaningful behind
    the authz gate (its own module says callers check `authz_enabled()` before consulting
    it), and with the gate off (the default / loopback dev path) there are no verified
    identities to trust -- so the surface stays DEFAULT-OFF and byte-identical, serving no
    `suggested_assignee` even when an allowlist file happens to be configured.
    """
    if not authz_enabled():
        return None
    if task.assignee:
        return None
    domain = _task_domain(task)
    if not domain:
        return None
    known = reviewer_allowlist()
    if not known:
        return None
    prior = [
        other
        for other in tasks
        if other.id != task.id and other.assignee in known and _task_domain(other) == domain
    ]
    if not prior:
        return None
    return max(prior, key=lambda other: (other.created_at, other.id)).assignee


def _review_task_view(
    task, *, suggested_assignee: str | None = None, detail: dict | None = None
) -> dict:
    """One review task as the read-only Review surface shows it (hy-167c).

    The whole record a human needs to judge a proposal: why it exists, what it
    touches, the processor evidence behind it, and the step-4 UNAPPROVED draft
    in `proposal_payload`. It carries `status` and nothing that could be read as
    an applied approval -- the surface presents; a human Git commit approves.

    `detail` (hy-z6zv) is the extra current-vs-proposed context a reviewer needs AT DETAIL
    -- the governed CURRENT meaning, the unresolved uncertainty, and the exact diff -- present
    only on the detail-bearing paths (get/list, which compute it) and absent from the mutation
    responses, the same present-when-provided shape `suggested_assignee` follows.
    """

    def _redact_nested_text(value):
        """Redact URL userinfo anywhere in legacy or newly stored review JSON."""
        if isinstance(value, dict):
            return {key: _redact_nested_text(inner) for key, inner in value.items()}
        if isinstance(value, list):
            return [_redact_nested_text(inner) for inner in value]
        if isinstance(value, str):
            return redact_free_text_userinfo(value)
        return value

    view = {
        "id": task.id,
        "reason": redact_free_text_userinfo(task.reason),
        "status": task.status,
        "priority": task.priority,
        "affected_asset_ids": list(task.affected_asset_ids),
        # Defense in depth (hy-s8a6): the write validator already refuses anything but an
        # opaque subject@issuer, so a valid value carries no `scheme://userinfo@`. Redact
        # here too, so a value written before this rule (or by any other path) can never
        # surface a credential over HTTP or MCP.
        "assignee": redact_free_text_userinfo(task.assignee),
        "proposal_payload": _redact_nested_text(task.proposal_payload),
        "processor_evidence": _redact_nested_text(task.processor_evidence),
        "created_at": _iso(task.created_at),
    }
    # Present ONLY when there is a hint (hy-38mk8, S3), so a task with no suggestion is
    # byte-identical to before. Redacted like `assignee`: it is an assist HINT, not an
    # assignment -- the task stays unowned until a human calls set_review_assignee. The
    # rationale rides with it (r2) so an MCP/HTTP consumer sees the deterministic signal,
    # assist-labeled, not just an opaque id.
    if suggested_assignee:
        view["suggested_assignee"] = redact_free_text_userinfo(suggested_assignee)
        view["suggested_assignee_rationale"] = _suggestion_rationale()
    if detail is not None:
        view.update(detail)
    return view


def _review_uncertainty(task) -> dict:
    """The UNRESOLVED meaning a reviewer is judging (hy-z6zv): the concepts named by the miss
    that the governed context does not yet declare. Assist-class (ADR 0019) -- a signal about
    what is unresolved, never authority."""
    payload = task.proposal_payload or {}
    undeclared = payload.get("undeclared_concepts")
    if undeclared is None:
        undeclared = (payload.get("miss") or {}).get("undeclared_concepts") or []
    return {"undeclared_concepts": list(undeclared), "assist": True}


def _current_governed_definition(task, *, governed_repo, workspace: str = "default"):
    """The domain's CURRENT governed meaning as a manifest-shaped definition, or None when
    nothing is governed yet (hy-z6zv). A task that names the exact governed context it touches
    (`affected_context_id`) reads that context's approved version; otherwise the domain's whole
    current governed meaning is the union of its governed contexts. Read-only: it never writes
    a governed row or an approval."""
    from hyperset.repositories.errors import NotFoundError

    payload = task.proposal_payload or {}
    domain = payload.get("domain") or (payload.get("miss") or {}).get("domain")
    affected = getattr(task, "affected_context_id", None)
    if affected:
        try:
            record = governed_repo.get(affected, workspace=workspace)
        except NotFoundError:
            return None
        return record.current_version.definition if record.current_version else None
    if not domain:
        return None
    definitions = [
        record.current_version.definition
        for record in governed_repo.list_all(domain=domain, workspace=workspace)
        if record.current_version
    ]
    return merge_definitions(definitions) or None


def _review_detail(task, *, governed_repo, workspace: str = "default") -> dict:
    """The detail-only current-vs-proposed context for one task (hy-z6zv): the governed CURRENT
    meaning beside the proposed draft, the uncertainty, and the EXACT diff between current and
    proposed -- the diff that today only materialises inside the PR. Deterministic and
    credential-free (the governed snapshot is credential-free by construction, ADR 0012, and
    the proposed draft is the same trust level as the already-served `proposal_payload`)."""
    current = _current_governed_definition(task, governed_repo=governed_repo, workspace=workspace)
    proposed = (task.proposal_payload or {}).get("definition")
    proposed = proposed if isinstance(proposed, dict) else None
    return {
        "current_meaning": current,
        "uncertainty": _review_uncertainty(task),
        "proposed_diff": diff_definition(current, proposed),
    }


def _load_review_task(task_id: str, *, session_factory, workspace: str = "default"):
    """The task by id, or an `INVALID_REQUEST` naming how to get a real id."""
    from hyperset.repositories.errors import NotFoundError

    try:
        return PostgresReviewRepository(session_factory).get_task(task_id, workspace=workspace)
    except NotFoundError as exc:
        raise OperationError(
            INVALID_REQUEST,
            f"no review task {task_id!r}",
            recovery="use a task id from list_review_tasks",
        ) from exc


def _require_mutable_review_task(task):
    """Keep task mutations and proposal opening on a non-terminal review task.

    Resolved and dismissed cards remain useful audit history, but must be read-only.  This
    service-level check is deliberately shared by every review mutation so a hidden UI control
    or a direct HTTP caller cannot turn a terminal task into a new write-back proposal.
    """
    if task.status not in ("open", "in_progress"):
        raise OperationError(
            INVALID_REQUEST,
            f"review task {task.id!r} is {task.status} and is read-only",
            recovery="select an open or in-progress review task",
        )
    return task


def _list_review_tasks(params: dict, *, session_factory, workspace: str = "default") -> dict:
    _reject_unknown(params, _LIST_REVIEW_PARAMS)
    # Empty, whitespace, or null all mean 'all tasks', normalized explicitly so a
    # schema-strict client and the server agree: the schema's enum has no empty
    # member, and this makes omitting the field and sending "" the same 'all'
    # (hy-gh-281 item 5 nit). A non-empty UNRECOGNISED value is REFUSED loudly,
    # not answered with an empty list -- `list_tasks` filters by exact match, so
    # 'banana' would return {"tasks": []}, a false 'no open tasks' a caller reports
    # as fact. Enumerate the accepted values, the help `unknown_parameter` gives.
    status = (params.get("status") or "").strip() or None
    if status is not None and status not in REVIEW_TASK_STATUSES:
        raise OperationError(
            INVALID_PARAMS,
            f"unknown status {status!r}",
            recovery=(
                f"omit 'status' for all tasks, or use one of: {', '.join(REVIEW_TASK_STATUSES)}"
            ),
        )
    tasks = PostgresReviewRepository(session_factory).list_tasks(status=status, workspace=workspace)
    governed_repo = PostgresGovernedContextRepository(session_factory)
    return {
        "schema_version": SCHEMA_VERSION,
        "tasks": [
            _review_task_view(
                task,
                suggested_assignee=_suggested_owner(task, tasks),
                detail=_review_detail(task, governed_repo=governed_repo, workspace=workspace),
            )
            for task in tasks
        ],
    }


def _get_review_task(params: dict, *, session_factory, workspace: str = "default") -> dict:
    _reject_unknown(params, _GET_REVIEW_PARAMS)
    task_id = _required_string(params, "task_id")
    task = _load_review_task(task_id, session_factory=session_factory, workspace=workspace)
    governed_repo = PostgresGovernedContextRepository(session_factory)
    detail = _review_detail(task, governed_repo=governed_repo, workspace=workspace)
    return {"schema_version": SCHEMA_VERSION, "task": _review_task_view(task, detail=detail)}


def _edit_review_draft(params: dict, *, session_factory, workspace: str = "default") -> dict:
    """The expert's edit of the assist draft (hy-murb, part c).

    Validates the edited definition against the SAME manifest rules a human's
    commit faces and replaces the draft on the task. It touches the assist draft
    only: the task stays `unapproved`, and no governed row is written.
    """
    from hyperset.context.errors import ContextValidationError
    from hyperset.context.schema import validate_definition_draft

    _reject_unknown(params, _EDIT_REVIEW_PARAMS)
    task_id = _required_string(params, "task_id")
    task = _require_mutable_review_task(
        _load_review_task(task_id, session_factory=session_factory, workspace=workspace)
    )
    definition = params.get("definition")
    if not isinstance(definition, dict):
        raise OperationError(
            INVALID_PARAMS,
            "an edit needs a 'definition' mapping",
            recovery="send the edited definition as a manifest-shaped mapping",
        )
    payload = dict(task.proposal_payload or {})
    domain = payload.get("domain") or (payload.get("miss") or {}).get("domain")
    try:
        normalized = validate_definition_draft(definition, domain=domain or "")
    except ContextValidationError as exc:
        raise OperationError(
            INVALID_REQUEST,
            f"the edited draft is not valid: {'; '.join(exc.reasons)}",
            recovery="the definition must satisfy the manifest rules",
        ) from exc
    payload.update(
        {
            "definition": definition,
            "normalized": normalized,
            "governance": "unapproved",
            "edited_by_human": True,
        }
    )
    # A manual edit changes the draft after the last authoring pass. Do not let
    # acknowledgement ids from an older draft satisfy the proposal gate.
    payload.pop("acknowledged_feedback_ids", None)
    payload.pop("acknowledged_decision_ids", None)
    saved = PostgresReviewRepository(session_factory).set_proposal_payload(
        task.id, payload, workspace=workspace
    )
    return {"schema_version": SCHEMA_VERSION, "task": _review_task_view(saved)}


def _validated_known_assignee(target) -> str:
    """The opaque identity a caller may assign to ANOTHER user, validated against the
    KNOWN-principals registry (hy-ip8do, gated on hy-a607k).

    NOT free text. hy-s8a6's ruling stands -- syntax alone cannot tell an opaque subject
    apart from a PII/credential-shaped value -- so a caller-supplied assignee is accepted
    ONLY when it is a member of the approved-reviewer allowlist, i.e. an operator-curated,
    already-well-formed KNOWN identity. A target the allowlist does not contain (or any
    non-string) is refused, and assigning another user is impossible at all when the
    allowlist is not configured (there is no registry to resolve against, so it fails
    closed). The membership denial is UNIFORM and does not echo the target, so it neither
    leaks the roster nor reflects a PII/credential value a caller tried to smuggle."""
    known = reviewer_allowlist()
    if known is None:
        raise OperationError(
            INVALID_PARAMS,
            "assigning another user requires the approved-reviewer allowlist",
            recovery=(
                "configure HYPERSET_REVIEWER_ALLOWLIST with the approved reviewers, or omit "
                "'assignee' to claim the task yourself"
            ),
        )
    if not isinstance(target, str) or target.strip() not in known:
        raise OperationError(
            INVALID_PARAMS,
            "'assignee' is not a known approved reviewer",
            recovery=(
                "assign an identity on the approved-reviewer allowlist, or omit 'assignee' "
                "to claim the task yourself"
            ),
        )
    return target.strip()


def _set_review_assignee(
    params: dict, *, session_factory, principal=None, workspace: str = "default"
) -> dict:
    """Claim a review task, unclaim it, or assign it to ANOTHER approved reviewer
    (hy-s8a6 S1 + hy-ip8do).

    `assigned` is a boolean: true assigns, false unassigns (clears any owner). On an
    assign:
    - OMIT `assignee` to SELF-claim: the owner is the caller's OWN identity, computed by
      the server from the verified `Principal` (the hy-mg8p proposer pattern), never
      caller free text -- PII-safe by construction and the 'assigned to me' concurrency
      path.
    - GIVE `assignee` to assign ANOTHER user: it is accepted ONLY as a KNOWN, approved
      identity -- a member of the reviewer allowlist -- never as typed free text
      (hy-ip8do). So the value is still a resolved, operator-curated identity, not a
      caller's free choice, and the hy-s8a6 no-typed-subject rule holds.

    Unassigning takes no `assignee` (there is no one to name). Assignment is task
    METADATA, never an approval or a grant: no governed row, no resolve, no SQL (ADR
    0012).
    """
    _reject_unknown(params, _SET_ASSIGNEE_PARAMS)
    task_id = _required_string(params, "task_id")
    assigned = params.get("assigned")
    if not isinstance(assigned, bool):
        raise OperationError(
            INVALID_PARAMS,
            "'assigned' must be a boolean: true to assign this task, false to unassign",
            recovery='send {"task_id": ..., "assigned": true} to claim, or false to unassign',
        )
    target = params.get("assignee")
    if not assigned:
        # Unassign clears the owner; naming someone to unassign is meaningless.
        if target is not None:
            raise OperationError(
                INVALID_PARAMS,
                "'assignee' is not accepted when unassigning",
                recovery='send {"task_id": ..., "assigned": false} with no assignee to unassign',
            )
        assignee = None
    elif target is None:
        # Self-claim: the caller's own server-computed identity.
        assignee = _principal_identity(principal)
    else:
        # Assign to ANOTHER user: a validated, known approved identity only.
        assignee = _validated_known_assignee(target)
    task = _require_mutable_review_task(
        _load_review_task(task_id, session_factory=session_factory, workspace=workspace)
    )
    saved = PostgresReviewRepository(session_factory).set_assignee(
        task.id, assignee, workspace=workspace
    )
    return {"schema_version": SCHEMA_VERSION, "task": _review_task_view(saved)}


def _refine_review_draft(params: dict, *, session_factory, workspace: str = "default") -> dict:
    """Ask the agent to refine the draft with the expert's feedback (part d).

    Re-runs the assist-class authoring producer with the expert's feedback
    travelling on the question, and REPLACES the draft on the same task. It stays
    assist-class and attributed (the trace's model/prompt hash ride on the
    payload); the task stays `unapproved`. It never approves, merges, writes a
    governed version, creates an approvable object, or runs SQL.
    """
    from hyperset.bundle.gather import gather
    from hyperset.flywheel.authoring import draft_definition

    _reject_unknown(params, _REFINE_REVIEW_PARAMS)
    task_id = _required_string(params, "task_id")
    task = _require_mutable_review_task(
        _load_review_task(task_id, session_factory=session_factory, workspace=workspace)
    )
    feedback_state, _ = _review_feedback_state(
        task, session_factory=session_factory, workspace=workspace
    )
    feedback = str(params.get("feedback", "")).strip()
    if (
        feedback_state["blocking_feedback_ids"] or feedback_state["blocking_decision_ids"]
    ) and not feedback:
        raise OperationError(
            INVALID_PARAMS,
            "human feedback is required to acknowledge prior negative feedback or citation "
            "decisions",
            recovery=(
                "send non-empty feedback explaining how the refined draft addresses those decisions"
            ),
        )
    payload = task.proposal_payload or {}
    miss = payload.get("miss") or {}
    question = miss.get("question")
    domain = payload.get("domain") or miss.get("domain")
    undeclared = payload.get("undeclared_concepts") or miss.get("undeclared_concepts") or []
    if not question or not domain:
        raise OperationError(
            INVALID_REQUEST,
            "this task carries no miss to refine from",
            recovery="refine a task drafted by the authoring step",
        )
    gathered = gather(
        domain=domain,
        undeclared=list(undeclared),
        session_factory=session_factory,
        workspace=workspace,
    )
    outcome = draft_definition(
        domain=domain,
        undeclared=list(undeclared),
        question=question,
        gathered=gathered or {"candidates": []},
        runtime=_authoring_runtime(),
        session_factory=session_factory,
        feedback=feedback or None,
        existing_task_id=task.id,
        workspace=workspace,
    )
    if outcome.status != "drafted":
        raise OperationError(
            INVALID_REQUEST,
            f"the refine run produced no usable draft ({outcome.status}): "
            f"{'; '.join(outcome.reasons)}",
            recovery="adjust the feedback and try again; the previous draft is unchanged",
        )
    # A successful explicit refinement is the acknowledgement event for the negative
    # feedback/decisions that were present when the human asked for it. Keep the ids on the
    # local task so a later proposal cannot be unblocked by an unrelated free-text field.
    refined_payload = dict(outcome.task.proposal_payload or {})
    refined_payload["acknowledged_feedback_ids"] = feedback_state["blocking_feedback_ids"]
    refined_payload["acknowledged_decision_ids"] = feedback_state["blocking_decision_ids"]
    saved = PostgresReviewRepository(session_factory).set_proposal_payload(
        task.id, refined_payload, workspace=workspace
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "task": _review_task_view(saved),
        "attribution": {
            "model": outcome.trace.model,
            "prompt_hash": outcome.trace.prompt_hash,
        },
    }


def _request_review_evidence(
    params: dict, *, session_factory, workspace: str | None = None
) -> dict:
    """Re-gather the observed evidence for one review task (hy-to8m, V1 gap Reviewer/3).

    Re-runs the DETERMINISTIC step-2 gather (no model, no credential -- it only ranks the
    observed estate already in the store) for the task's miss, and REPLACES the task's assist
    `gathered_sources` with the fresh summary. Assist-class and NOT SERVING: it touches only
    the UNAPPROVED payload, advances no status, writes no governed row, and runs no warehouse
    SQL (ADR 0012). It is served ONLY as a bespoke HTTP route (off OPERATIONS/MCP), but is a
    review-AUTHORING mutation, so its HTTP handler gates it with the REVIEW action.
    """
    from hyperset.bundle.gather import gather
    from hyperset.flywheel.authoring import _gathered_summary

    _reject_unknown(params, _REQUEST_EVIDENCE_PARAMS)
    task_id = _required_string(params, "task_id")
    task = _require_mutable_review_task(
        _load_review_task(
            task_id, session_factory=session_factory, workspace=workspace or "default"
        )
    )
    payload = task.proposal_payload or {}
    miss = payload.get("miss") or {}
    domain = payload.get("domain") or miss.get("domain")
    undeclared = payload.get("undeclared_concepts") or miss.get("undeclared_concepts") or []
    if not domain:
        raise OperationError(
            INVALID_REQUEST,
            "this task carries no domain to gather evidence for",
            recovery="request evidence for a task drafted by the authoring step",
        )
    gathered = gather(
        domain=domain,
        undeclared=list(undeclared),
        session_factory=session_factory,
        workspace=workspace,
    )
    updated = {**payload, "gathered_sources": _gathered_summary(gathered or {"candidates": []})}
    saved = PostgresReviewRepository(session_factory).set_proposal_payload(
        task_id, updated, workspace=workspace or "default"
    )
    return {"schema_version": SCHEMA_VERSION, "task": _review_task_view(saved)}


def _mint_github_app_token(config) -> str:
    """Mint a short-lived GitHub App installation token for the write-back target
    (hy-bdhg, ADR 0027). Decrypts the stored App private key with the KEK, signs a
    JWT, resolves the App installation, and exchanges it for a repository-scoped
    installation token. FAIL CLOSED (an OperationError, never a return) on a
    missing config, an undecryptable key, an App not installed on the repository,
    or a mint failure -- the caller never pushes unauthenticated. The decrypted
    private key lives only for the mint and is dropped here; the minted token is
    never stored.
    """
    if config.app_id is None or config.app_key_ciphertext is None:
        raise OperationError(
            INVALID_REQUEST,
            "the github_app write-back source has no App id or private key configured",
            recovery="set the GitHub App id and paste the App private key in admin settings",
        )
    from hyperset.security.github_app import GitHubAppError, mint_installation_token
    from hyperset.security.secret_box import SecretBoxError, decrypt

    try:
        private_key = decrypt(config.app_key_ciphertext, config.app_key_nonce)
    except SecretBoxError as exc:
        raise OperationError(
            INVALID_REQUEST,
            f"the stored GitHub App private key could not be decrypted: {exc}",
            recovery=(
                "ensure HYPERSET_SECRET_KEY matches the key the App private key was "
                "encrypted with, or re-enter the private key in admin settings"
            ),
        ) from exc
    try:
        return mint_installation_token(
            app_id=config.app_id,
            private_key=private_key,
            repository=config.repository,
        )
    except GitHubAppError as exc:
        raise OperationError(
            INVALID_REQUEST,
            f"could not mint a GitHub App installation token: {exc}",
            recovery=(
                "confirm the App id and private key are correct and the Hyperset GitHub "
                "App is installed on the write-back repository"
            ),
        ) from exc
    finally:
        private_key = None  # drop the decrypted key as soon as the mint is done


def resolve_writeback_token(config) -> str | None:
    """Resolve a write-back target's server-side credential, or FAIL CLOSED.

    A URL target authenticates with a server-side token, resolved at request time
    and handed only to the git child's ENVIRONMENT (never argv, hy-6haz). Three
    sources: 'env_ref' reads the token from the environment by the configured NAME
    (hy-eji4); 'encrypted' decrypts a stored PAT ciphertext with the KEK (hy-up4k);
    'github_app' decrypts the App private key, signs a short-lived JWT, and MINTS a
    per-op installation token (hy-bdhg). Returns None for a LOCAL-PATH target (no
    auth). Raises OperationError when a URL target's token is missing,
    undecryptable, or unmintable, so no caller pushes -- or probes -- a private URL
    unauthenticated. Shared by the propose path and the admin target probe
    (hq-095h) so both resolve a credential exactly one way.
    """
    if _is_local_path(config.repository):
        return None
    token: str | None = None
    if config.mode == "github_app":
        token = _mint_github_app_token(config)
    elif config.mode == "encrypted":
        if config.token_ciphertext is not None:
            from hyperset.security.secret_box import SecretBoxError, decrypt

            try:
                token = decrypt(config.token_ciphertext, config.token_nonce)
            except SecretBoxError as exc:
                raise OperationError(
                    INVALID_REQUEST,
                    f"the stored write-back token could not be decrypted: {exc}",
                    recovery=(
                        "ensure HYPERSET_SECRET_KEY matches the key the token was "
                        "encrypted with, or re-enter the token in admin settings"
                    ),
                ) from exc
    else:
        token_ref = (config.token_ref or "").strip()
        token = os.environ.get(token_ref) if token_ref else None
    if not token:
        raise OperationError(
            INVALID_REQUEST,
            "a URL write-back target needs a server-side token, and none is available",
            recovery=(
                "set a token in admin settings -- an env var NAME (env_ref) whose secret is "
                "in the server environment, or a pasted token (encrypted); the raw token is "
                "never entered or stored in the browser"
            ),
        )
    return token


def _review_feedback_state(task, *, session_factory, workspace: str) -> tuple[dict, bool]:
    """Read local human feedback for a proposal and return safe control metadata.

    Feedback is deliberately consumed at this boundary without exporting it to a hosted
    model or remote PR: negative outcomes pause an unrefined proposal, while the normal
    ``refine_review_draft`` path lets a human explicitly choose what should be sent to the
    Luna authoring runtime. Notes, identities, and source refs stay in the local audit store.
    """
    payload = task.proposal_payload or {}
    correlation_id = opaque_token(payload.get("correlation_id"))
    feedback_repo = PostgresAnswerFeedbackRepository(session_factory)
    records = []
    seen_feedback: set[str] = set()
    for filters in ({"review_task_id": task.id}, {"correlation_id": correlation_id}):
        filter_value = next(iter(filters.values()))
        if filter_value is None:
            continue
        for record in feedback_repo.lookup(workspace=workspace, limit=20, **filters):
            if record.id not in seen_feedback:
                seen_feedback.add(record.id)
                records.append(record)
    decision_repo = PostgresCitationDecisionRepository(session_factory)
    decisions = list(decision_repo.for_task(workspace=workspace, review_task_id=task.id))
    if correlation_id:
        for_correlation = getattr(decision_repo, "for_correlation", None)
        if for_correlation is not None:
            seen_decision_ids = {getattr(decision, "id", None) for decision in decisions}
            decisions.extend(
                decision
                for decision in for_correlation(workspace=workspace, correlation_id=correlation_id)
                if getattr(decision, "id", None) not in seen_decision_ids
            )
    decisions = [
        decision for decision in decisions if getattr(decision, "superseded_by", None) is None
    ]
    outcomes = Counter(str(record.outcome) for record in records)
    decision_values = Counter(str(decision.decision) for decision in decisions)
    blocking_feedback_ids = feedback_repo.blocking_ids(
        workspace=workspace,
        review_task_id=task.id,
        correlation_id=correlation_id,
    )
    blocking_decision_ids = [
        decision.id
        for decision in decisions
        if decision.decision in {"exclude", "reject"} and getattr(decision, "id", None)
    ]
    state = {
        "answer_feedback": dict(sorted(outcomes.items())),
        "citation_decisions": dict(sorted(decision_values.items())),
        "feedback_count": len(records),
        "decision_count": len(decisions),
        "blocking_feedback_ids": blocking_feedback_ids,
        "blocking_decision_ids": blocking_decision_ids,
    }
    blocked = bool(blocking_feedback_ids or blocking_decision_ids)
    return state, blocked


def _propose_review_to_git(
    params: dict, *, session_factory, workspace: str = "default", principal=None
) -> dict:
    """Fire the proposal-only writer for one review task (hy-8o8m).

    It may OPEN A PR PROPOSAL only: it reads the admin-configured target and the
    task's UNAPPROVED draft, and calls the already-proven proposal-only writer,
    which pushes a NEW branch and never touches the base ref. It never approves,
    merges, writes a governed version, creates a Hyperset-side approvable object,
    or runs SQL (ADR 0012). The draft stays `unapproved` until a human merges the
    Git PR.
    """
    _reject_unknown(params, _PROPOSE_REVIEW_PARAMS)
    task_id = _required_string(params, "task_id")
    # The INPUT is validated BEFORE the deployment config (hy-gh-281 item 7): a
    # nonexistent task_id is the caller's typo, and it must be diagnosed as 'no
    # review task ...', exactly like the sibling task-scoped tools -- not answered
    # with 'no write-back repository is configured', which sends someone off to
    # configure write-back to fix a typo. The task's own draft is checked here too,
    # for the same reason, before anything about the deployment is read.
    task = _require_mutable_review_task(
        _load_review_task(task_id, session_factory=session_factory, workspace=workspace)
    )
    payload = task.proposal_payload or {}
    draft = payload.get("definition")
    domain = payload.get("domain") or (payload.get("miss") or {}).get("domain")
    if not isinstance(draft, dict) or not domain:
        raise OperationError(
            INVALID_REQUEST,
            "this review task carries no draft definition to propose",
            recovery="propose a task drafted by the authoring step",
        )
    # Route to the write-back TARGET this proposal's domain belongs to (hq-1h1z),
    # WITHIN the proposing caller's workspace (hq-t6nx): an enabled target of this
    # workspace keyed to `domain`, else this workspace's enabled default target,
    # else FAIL CLOSED. The proposal touches ONLY the routed target's repository --
    # a missing route never falls through to another target, a keyed match is never
    # combined with the default, and a target in another tenant is never eligible,
    # so no proposal crosses targets or tenants.
    config = PostgresWritebackConfigRepository(session_factory).get_by_routing(
        domain, workspace=workspace
    )
    if config is None:
        raise OperationError(
            INVALID_REQUEST,
            f"no write-back target is configured for domain {domain!r}",
            recovery=(
                "add a write-back target whose routing key is this domain, or a default "
                "target, in the admin panel first"
            ),
        )
    # A target created by the current admin path cannot contain URL userinfo, but a
    # legacy row can. Do not pass such a row to Git: redacting it would silently change
    # the remote identity and using it as-is would send credentials to subprocess.
    if redact_pointer(config.repository) != config.repository.strip():
        raise OperationError(
            INVALID_REQUEST,
            "the write-back target contains embedded repository credentials",
            recovery="edit the target and move its token to env_ref, encrypted, or github_app",
        )
    feedback_state, feedback_blocked = _review_feedback_state(
        task, session_factory=session_factory, workspace=workspace
    )

    # A negative human outcome must be explicitly addressed through the local review
    # refinement operation before an automatic proposal can leave the machine. This is the
    # feedback flywheel's safe default: feedback changes behavior, but never grants authority.
    def _acknowledged_ids(key: str) -> set[str]:
        raw = payload.get(key)
        if raw is None:
            return set()
        if not isinstance(raw, list) or any(opaque_token(item) is None for item in raw):
            raise OperationError(
                INVALID_REQUEST,
                f"{key} is not a valid refinement acknowledgement",
                recovery="refine the draft again so the server records current feedback ids",
            )
        return set(raw)

    acknowledged_feedback = _acknowledged_ids("acknowledged_feedback_ids")
    acknowledged_decisions = _acknowledged_ids("acknowledged_decision_ids")
    unacknowledged_feedback = set(feedback_state["blocking_feedback_ids"]) - acknowledged_feedback
    unacknowledged_decisions = set(feedback_state["blocking_decision_ids"]) - acknowledged_decisions
    if feedback_blocked and (unacknowledged_feedback or unacknowledged_decisions):
        raise OperationError(
            INVALID_REQUEST,
            "prior human feedback or citation decisions require this draft to be refined "
            "before proposing",
            recovery="call refine_review_draft with human feedback, then retry",
        )
    token = resolve_writeback_token(config)
    # The Git writer is INJECTED (like the refine runtime): it names `git_pr`,
    # which reaches `subprocess`, and this module must not (test_report_time_
    # purity). The writer opens the proposal-only PR (a local target has no PR to
    # open, so the pushed branch IS the proposal; a URL target authenticates with
    # the token and opens a real PR, hy-eji4) and maps its own failures to
    # OperationError, so operations names neither git_pr nor its error type.
    # Remote proposal metadata is deliberately minimal. The task's question, evidence,
    # feedback, and proposer identity stay in Hyperset's local review/audit store; the
    # remote writer receives only a task backlink and non-sensitive source linkage.
    review_repo = PostgresReviewRepository(session_factory)
    try:
        task = review_repo.reserve_proposal(
            task_id, workspace=workspace, expected_version=task.row_version
        )
    except (NotFoundError, OptimisticConcurrencyError) as exc:
        raise OperationError(
            INVALID_REQUEST,
            str(exc),
            recovery="refresh the review task and retry only while it is open and unchanged",
        ) from exc
    try:
        try:
            review_repo.assert_proposal_lease(
                task_id,
                workspace=workspace,
                lease_id=task.proposal_lease_id,
            )
        except (NotFoundError, OptimisticConcurrencyError) as exc:
            raise OperationError(
                INVALID_REQUEST,
                str(exc),
                recovery="refresh the review task and retry only while its proposal lease is valid",
            ) from exc

        def _assert_current_lease() -> None:
            # The Git writer invokes this fence at each external write boundary, after
            # clone/commit work. A reclaiming worker therefore fails the stale proposal
            # before it can push a branch or open a PR, without holding a DB lock across
            # network I/O.
            review_repo.assert_proposal_lease(
                task_id,
                workspace=workspace,
                lease_id=task.proposal_lease_id,
            )

        proposal = _propose_writer(
            draft=draft,
            domain=domain,
            repository=config.repository,
            base_ref=config.base_ref,
            path=config.manifest_path,
            token=token,
            review={
                "task_id": task_id,
                "backlink": (
                    f"Hyperset review task {task_id} -- fetch it with the get_review_task "
                    "tool, or open it on the Review surface"
                ),
            },
            before_remote_write=_assert_current_lease,
        )
    except Exception:
        review_repo.release_proposal(task_id, workspace=workspace, lease_id=task.proposal_lease_id)
        raise
    # Route the proposal to its reviewer(s) and RECORD the routing on the task
    # (hq-1rq7). The reviewers come from the SAME target the write-back routed to,
    # so a proposal for domain A can never carry B's reviewers. A target with no
    # reviewer routing FAILS CLOSED to an explicit needs-routing state: the
    # proposal-only PR still opened (that is not approval), but no reviewer is
    # invented and nothing is auto-approved -- a human must route it. The record
    # carries the handoff a reviewer needs (task id, target identity, the authority
    # commit, and the PR backlink); the task's own evidence and the exact draft
    # are already on the task. It is written into `proposal_payload`, which the
    # read-only Review surface returns verbatim, so it surfaces in task detail
    # WITHOUT changing any served response SHAPE (this response is unchanged, so
    # SCHEMA_VERSION and tools_hash do not move -- ADR 0018).
    reviewers = _resolve_reviewers(config.reviewer_routing)
    review_routing = {
        "status": "routed" if reviewers else "needs_routing",
        "reviewers": reviewers,
        "task_id": task_id,
        "target": {
            "id": config.id,
            "routing_key": config.routing_key,
            "repository": redact_pointer(config.repository),
            "base_ref": config.base_ref,
        },
        "authority_commit": proposal["commit_sha"],
        "backlink": proposal["pr_url"],
        "head_branch": proposal["head_branch"],
        "path": proposal["path"],
    }
    # Record the routing on the task AND a durable, workspace-scoped admin-audit row
    # for the propose action in ONE transaction (hq-hnrf area 3): propose is a mutating
    # admin action that OPENS A PR against a specific target repository, so -- like
    # reconcile / context_source.sync / connection ops -- it leaves a tamper-evident
    # audit entry naming WHO proposed WHICH task to WHICH target and at WHICH source
    # commit. The audit is coupled to the payload write so a failed append rolls the
    # payload back (the trail is not omittable at its own failure mode). The target
    # repository is credential-free by construction (the target-set path refuses a URL
    # with embedded userinfo), so `detail` carries no secret. The audit row is stamped
    # with the caller's workspace, so a tenant's admin reads only its own propose
    # actions.
    actor = principal.subject if principal is not None else "anonymous"
    issuer = principal.issuer if principal is not None else None
    # REDACT the target repository at the audit boundary (hq-hnrf, adversary round 2):
    # the target-SET path rejects a NEW credential-bearing pointer, but a legacy/stored
    # row could still hold `https://user:token@...`, and this detail is read by any
    # READ-authorized principal. `redact_pointer` strips URL userinfo, so a token can
    # never land in the audit row.
    audit_detail = (
        f"target={config.id} key={config.routing_key or 'default'} "
        f"repo={redact_pointer(config.repository)}@{config.base_ref} "
        f"commit={proposal['commit_sha']} pr={redact_pointer(proposal.get('pr_url') or 'local')}"
    )
    try:
        with session_factory() as session, session.begin():
            review_repo.finish_proposal(
                task_id,
                {**payload, "review_routing": review_routing},
                workspace=workspace,
                lease_id=task.proposal_lease_id,
                session=session,
            )
            PostgresAdminAuditRepository(session_factory).record(
                actor=actor,
                actor_issuer=issuer,
                action="review_task.propose",
                target=task_id,
                result="ok",
                detail=audit_detail,
                workspace=workspace,
                session=session,
            )
    except Exception:
        # The remote side effect is idempotent by branch/PR identity, but the local
        # reservation must not wedge the task if finalization/audit fails. Release it in
        # a fresh transaction so a retry can reconcile the same proposal safely.
        try:
            review_repo.release_proposal(
                task_id, workspace=workspace, lease_id=task.proposal_lease_id
            )
        except Exception:  # noqa: BLE001 -- preserve the original failure for the caller
            _log.exception("could not release proposal reservation after finalization failure")
        raise
    return {"schema_version": SCHEMA_VERSION, "proposal": proposal}
