"""The public v0 response shape (hy-gh-31), `docs/v0-foundation.md` section 6.

One shape serves HTTP, MCP, deterministic clients, and the evaluator, so it
is defined once, here, and never derived from a persistence object. No code
in this module reads a database: it is the contract plus its deterministic
serialization.

It cannot yet be IMPORTED without one, though, and that is worth stating
where a reader can see it rather than discovering it: `hyperset/bundle/
__init__.py` re-exports eagerly, so importing anything under `hyperset.bundle`
executes `catalog` and `resolver` and pulls in the Postgres repositories and
SQLAlchemy. True before this module named a code from `hyperset.context` and
still true after. hy-abh carries the packaging fix; nothing here can reach it,
and no test catches it today because the driver is always installed under
`uv`.

Determinism is a product requirement, not a test convenience -- the same
pinned Git commit and source state must produce the same bundle. `bundle_id`
is therefore content-derived: it hashes everything except the wall clock, so
two resolutions of unchanged evidence are provably the same answer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from hyperset.context.schema import (
    REF_AMBIGUOUS,
    REF_AWAITING_SYNC,
    REF_MALFORMED,
    REF_NOT_OBSERVED,
)
from hyperset.security.redaction import redact_free_text_userinfo

# 10 since a domain may declare NO eval bank -- `evals: none`, or an omitted key
# -- and the answer states that honestly (hy-gh-287, GitHub #287). The bundle's
# `context_authority` carries `unevaluated: true` on such a domain and nothing on
# one with a bank; the catalog carries `counts.eval_cases` on every domain and a
# corpus `page.unevaluated_domain_count`. Additive under decision 1 -- keys a
# caller RECEIVES that it did not before -- and it moves the number anyway,
# because an absent `unevaluated` cannot safely mean "evaluated" to a version-9
# reader: that reading is default-ALLOW, and would launder exactly the
# uncertainty the field exists to state. Not default-deny, so decision 5 does not
# make it additive-without-a-move; a new received key moves the number.
#
# 9 since a validate result may carry `sections_not_checkable` and a
# `valid_with_gaps` status (hy-gh-285): the sparse-domain disclosure that stops a
# domain checked against nothing from reading as a clean pass. Additive keys a
# caller receives, recorded here beside the earlier moves because section 7 of
# `docs/v0-foundation.md` already carried it and this comment had not.
#
# 8 since `assist.proposal.outcome` serves a fifth value, `no_governing_domain`
# (hy-xq55). This is the FIRST move here for an added value rather than for a
# shape, and it is not a reversal of decision 5 -- it is decision 5 applied. That
# ruling makes an added value additive ONLY where default-deny is published for
# that field, and this field is in neither place the ADR names: it is not a row
# in decision 5's own table and neither shipped client surface mentions it. It
# has been served since 6 with no rule attached to it, so a client meeting an
# unrecognised outcome has nothing to apply, and "the parser still parses" is
# what decision 5 exists to refuse as a safety argument.
#
# What it would have cost to publish instead, said here because the next author
# will face the same fork: naming the field in `planner.md` or in
# `_UNKNOWN_VALUE_RULE` moves `prompt_hash` and `tools_hash`, which invalidates
# every committed eval recording -- measured, 16 red arms in `tests/unit/evals`
# -- and the remedy is a recording session that rewrites #25's headline numbers.
# `docs/v0-foundation.md` already defers that cost to hy-hj9g's ratification
# pass, where it is taken once. So the publication is hy-1bh1 and this number
# moved, which is the honest order: an unversioned value would have been the
# only one of the two that a client cannot detect.
#
# The other half of hy-xq55 does NOT move it. A bundle refused `unknown_domain`
# now carries the same `assist` section a coverage refusal has carried since 4,
# and 4 is the version that announced that a bundle MAY carry one. Extending
# which refusals meet an announced condition adds no key.
#
# 7 since every `linked_evidence.conflicts` entry names its producer, and one of
# the two producers stands no persisted finding behind it (hy-llk4). Two changes
# in one number because they are one change: `produced_by` is the 6's shape again
# -- a new always-present key in a served section -- and it exists to say which
# entries are the ones whose `finding_id` is now null. That null is the part a
# consumer cannot parse around. Every entry carried an id; a reconciled entry
# carries None, so a client that read the field as always present breaks on a
# VALUE while the shape it validates stays satisfied, which is the direction
# ADR 0018 decision 4 asks about and the reason a purely additive-looking change
# still moves the number.
#
# 6 since the `assist` section always carries `proposal`: the one candidate the
# evidence separated, or a named reason it was withheld (hy-gh-124 slice 2).
#
# The argument for NOT moving it was that assist publishes no response schema,
# so there is nothing for a client to have validated against. The Mayor ruled
# against it and this comment records the reasoning rather than the verdict,
# because the verdict alone would not tell the next author which shape they
# have. hy-pvbu moved 2 -> 3 for the same shape -- a `recovery` key added to
# every `violations` entry, with no published response schema for that section
# either -- so "assist is not JSON Schema" does not separate the two cases. The
# alternative available to the ruling was to exempt the assist section in
# writing, and it was rejected: a served section exempted from the version
# signal is a section the number no longer covers, and then nothing tells a
# reader which half of the payload the version is about.
#
# The line, so this is not read as contradicting the `signals` decision: the
# question is whether the version already in a reader's hands told them to
# expect the addition. A new member of `produced_by.signals` -- a self-declaring
# list whose whole purpose is to be read at whatever length it arrives -- did,
# and did not move the number. A new ALWAYS-PRESENT key did not, and moves it.
# The two answer different shapes.
#
# 5 since `ref_not_observed` NARROWED: it used to mean "no observation carries
# this ref" for any reason, and it now means the connector read the estate and
# the asset was not in it, with the unread case split off as
# `ref_awaiting_sync` (hy-lcgq, hy-gh-118). The new code is an added value in
# `resolution.warnings[].code`, which decision 5 makes additive, so it would
# not have moved this number on its own. The NARROWING moves it, and it is the
# first move here that is not additive: a client holding version 4 read
# `ref_not_observed` as covering both worlds, and the same code in a version 5
# bundle covers one.
#
# ADR 0018's TEXT does not yet cover this case, and citing it as though it did
# would be the easy mistake. Decision 1 moves the number for a change in the
# SHAPE of what a caller receives, and nothing here gained or lost a key -- a
# shape diff finds nothing. Decision 5 rules an ADDED value additive, not a
# narrowed one. The mayor's third-case ruling is what this bump rests on: a
# served code whose meaning narrows moves the number even though the bytes are
# shaped identically. It is the case worst left unversioned, because it is the
# only one a client cannot detect by parsing -- the field is there, the code is
# one it knows, and the world it denotes shrank underneath it. The ADR
# amendment carrying that case, and the demonstration that no shipped gate can
# see a narrowing, is hy-87dn and rides in its own change, not this one.
#
# 4 was a bundle being able to carry an `assist` section at all: ranked
# candidate sources for a claim no governed context covers (hy-gh-124 slice 1,
# ADR 0019). A new top-level key is a shape change on the answer under any
# reading of ADR 0018, so the number moved even though the key is absent from
# every answer that was already being served -- a client that never asks a
# question governance is silent on will never see it, and that is not the test
# ADR 0018 sets.
#
# That one was 4 and not 3 because two changes claimed the 3 at once and one of
# them merged first (hy-fhtr): the number is allocated by merge order, not by
# which branch wrote it down first, so the loser rebases rather than the two
# shipping one number for two different shapes. The same rule governs this 6:
# hy-lcgq's narrowing took the 5 by merging first, so this rebased onto it.
#
# 3 was every `PlanValidation` violation gaining `recovery`: the move that
# answers its code, so a plan told what is wrong with it is also told what to
# send next (hy-pvbu). 2 was `linked_evidence` gaining `uncorroborated`: the
# refs the pinned Git commit declares that no observation carries (hy-zhv9).
# Additive every time, so nothing a client reads today changes meaning -- and it
# moves anyway, because ADR 0018 makes the question "which direction changed?"
# rather than "how bad is it?", and each changed the SHAPE of what a caller
# RECEIVES. The `no_declared_sources` code that arrived with `recovery` did NOT
# move it: a new value in an enumerated served field is additive under ADR 0018
# decision 5, and only the new key moved that number.
#
# It stayed 1 through two earlier shape changes in one day -- `resolution.
# warnings` entries became objects (hy-6ae) and `page.truncated` entries
# gained a reason (hy-6ae, over hy-74k). That was defensible only because
# nothing outside this repository consumed v0 yet, an excuse ADR 0018 declines
# to extend.
#
# It versions the ANSWER and not the REQUEST (ADR 0018). A change to what a
# caller may send -- `concepts` becoming required with `domains` refused every
# previously valid `{"domains": [...]}` directive (hy-9lct) -- does not move
# this number, because this number is a field on the bundle and a reader
# holding it is holding a response. A request-shape break is announced in
# `docs/v0-foundation.md` section 7 and carried as a `release-note` bead
# instead. A change to what a caller RECEIVES does move it.
#
# 11 through 19 were surface-facet and multi-domain slices (#283, #284, #230),
# each a new key a caller RECEIVES -- additive under decision 1, and the number
# moved anyway. Backfilled here so this ledger matches section 7 of
# `docs/v0-foundation.md` (which narrates each at its number) and the ADR-0015
# release-note register (hy-fz53). Measured from the commit that moved the
# constant, not predicted:
# 11 `resolution.projection` for a domain projected through a context adapter
#    (43c13fe, #283 slice 5).
# 12 a per-source `grain` on `instructions.approved_sources`, plus a grain node
#    and `has_grain` edge on the domain graph (d1d83ee, #284).
# 13 a per-source `classification` sensitivity label: a classification node and
#    `classified_as` edge (24421b7, #284).
# 14 a per-source `freshness` contract (cadence and/or max-staleness): a freshness
#    node and `has_freshness` edge (32a99f2, #284).
# 15 a per-source `lineage` contract (produced_by and/or upstream): a lineage node
#    and `has_lineage` edge (4839eb2, hy-sr7w).
# 16 a per-source `checks` contract (owned data-quality checks): a checks node and
#    `has_checks` edge (105c670, hy-w16y).
# 17 a governed `contains` hierarchy edge (ADR-0031): domain nodes and `contains`
#    edges, validated whole-estate and emitted fail-closed (d22a1d4, #230 slice 1).
# 18 a directive naming more than one governed domain resolves into a top-level
#    `domains[]` envelope, each entry byte-identical to its solo resolve and the
#    flat authority null/empty (b01e5c7, #230 slice 3, hy-cnto).
# 19 that multi-domain answer also carries a top-level `composition` cross-domain
#    graph: domain-level nodes and governed `contains` edges (08dab20, #230
#    slice 5, hy-uaks).
#
# 19 -> 20 (hy-xfhh, impl slice (i) of the reconciliation-engine design): every
# `linked_evidence.conflicts` entry gains a `severity` KEY (error/warning; a
# processor-finding conflict inherits its finding's severity, a reconciled conflict
# carries its kind's fixed severity). A NEW KEY is a change to what a caller receives
# -- a strict consumer validating against 19 misreads a 20 payload -- so it moves the
# number, the same precedent as `finding_id` gaining a null value. `severity`'s own
# VALUES (`SEVERITIES`) are default-deny-published, so a later value addition is
# additive under ADR 0018 decision 5 and will NOT move it again. `tools_hash` is
# untouched: `conflicts` is bundle output, not a served tool name/description/input
# schema.
#
# 20 -> 21 (hy-s8a6, S1): the review-task view gains a first-class `assignee` KEY (an
# opaque `subject@issuer` owner, or null when unassigned), set/cleared by the new
# `set_review_assignee` op. A NEW KEY a consumer must know to read is a change to what a
# caller receives -- a strict consumer validating against 20 misreads a 21 review task --
# so it moves the number (ADR-0018 wire-change discipline). `tools_hash` is untouched: the
# new op is served but OFF `RESOLVE_PATH_OPERATIONS`, and `assignee` is review-task output,
# not a resolve-path tool name/description/input schema.
#
# 21 -> 22 (hy-38mk8, S3): the `list_review_tasks` view MAY carry a `suggested_assignee`
# KEY -- an assist-class owner HINT (the most-recent prior in-domain reviewer, filtered to a
# KNOWN allowlisted reviewer) -- and a companion `suggested_assignee_rationale` object
# (`signal`/`summary`/`assist`) naming the deterministic reason, both present only when there
# is a suggestion, absent otherwise. It is a SUGGESTION a human overrides via
# set_review_assignee, NEVER an auto-assignment or approval (ADR 0019). New optional KEYS a
# consumer must know to read are still a change to what a caller receives (a strict consumer
# validating against 21 does not expect them), so it moves the number -- the same rule the
# review-view addition follows in v1-gap-matrix. `tools_hash` is untouched: `list_review_
# tasks` is served but OFF `RESOLVE_PATH_OPERATIONS`, and this is review-task output, not a
# resolve-path tool name/description/input schema.
#
# 22 -> 23 (hy-z6zv, V1 gap Reviewer/2): the review-task detail view (get_review_task and
# list_review_tasks) gains three KEYS a reviewer judges a proposal by AT DETAIL -- the
# governed `current_meaning` (the domain's approved definition, or null when nothing is
# governed yet), the `uncertainty` (the miss's undeclared concepts, assist-labelled), and the
# `proposed_diff` (the exact current-vs-proposed delta, the diff that today only materialises
# inside the PR). New KEYS a consumer must know to read are a change to what a caller receives
# -- a strict consumer validating against 22 does not expect them -- so it moves the number,
# the same review-view rule 20/21/22 followed. `tools_hash` is untouched: these are review-
# task output on ops OFF `RESOLVE_PATH_OPERATIONS`, not a resolve-path tool name/description/
# input schema.
#
# 23 -> 24 (hy-l93sc, hive-mind graph slice 1): `expand_analytics_context` gains a walk that
# starts from a synthetic workspace ROOT (`from_root`) and a richer served shape a consumer
# must know to read -- a `root` node (present only on a root walk), per-reached-domain
# `pointers` (source_id/repository/snapshot_id/commit_sha/context_doc path/approved_sources --
# POINTERS, never content), an `exclusion` marker on a disclosed-excluded domain, root->domain
# edges carrying a NEW edge `evidence: "system"` value (catalog-derived NAVIGATION, never
# `evidence: "git"`), and a new disclosure code `expansion_acl_excluded`. New KEYS/values a
# strict consumer validating against 23 does not expect are a change to what a caller receives,
# so it moves the number (ADR-0018 wire-change discipline). `tools_hash` is untouched: EXPAND
# is served but OFF `RESOLVE_PATH_OPERATIONS`, and this is expansion output plus an EXPAND
# input key, not a resolve-path tool name/description/input schema. The root is NAVIGATION
# only: `result_kind` stays "navigation", it carries no `context_authority` and no governed
# meaning, and its edges are never `evidence: "git"` -- so it creates no authority (ADR 0012).
#
# 24 -> 25 (hy-0unvk): `search_knowledge` accepts `mode="semantic"`; hits in that opt-in
# mode gain a `signal` object carrying cosine score and exact embedding-space metadata.
# Grep remains the default and its hit shape is unchanged. The new semantic hit key is still
# a served response-shape change a strict consumer must know to read, so the number moves
# under ADR 0018. `tools_hash` is untouched: SEARCH_KNOWLEDGE remains off
# `RESOLVE_PATH_OPERATIONS` and the governed resolve path is unchanged.
SCHEMA_VERSION = 26

# `governed` means the guidance comes from the customer's authoritative Git
# context -- not that Hyperset approved the business meaning. `mixed` is a
# governed domain plus refs it does not cover, and `observed_only` is raw
# observation with no authority behind it: neither may ever be read as
# approved meaning, which is why they are separate statuses rather than a
# warning on a governed bundle (hy-5c2).
RESOLUTION_STATUSES = ("governed", "mixed", "observed_only", "no_match")

# The status that means Git says nothing about what was named -- a valid answer,
# not an error, and the sharpest kind of miss the boundary logs (hy-jrpm). Named
# beside `OBSERVED_ONLY` so a consumer branches on a constant, not a literal.
NO_MATCH = "no_match"

# Carried by every entry in `linked_evidence.observed_assets`: whether the Git
# commit declared this source or a directive merely named it. Part of the
# contract, not a resolver detail -- a client that cannot tell governed
# evidence from ungoverned evidence has no way to honour the difference.
GIT_LINKED = "git_linked"
OBSERVED_ONLY = "observed_only"

# Every entry in `resolution.warnings` carries a stable `code` beside its
# sentence. The sentence is for a person; the code is what a client branches
# on, and it is part of the contract for the same reason `governance` is: a
# planner deciding whether to retry, to resolve again without `asset_refs`, or
# to give up cannot be asked to substring-match English to find out which
# happened. The message text may be reworded at any time. The code may not.
NO_CONTEXT_SOURCE = "no_context_source"
UNKNOWN_DOMAIN = "unknown_domain"
# RETIRED as a refusal (#230 slice 3, hy-cnto): a directive naming several governed
# domains now RESOLVES, each domain in its own `domains[]` entry, instead of being
# refused with this code. The constant and its vocabulary membership are KEPT so the
# published warning list and older clients that branch on it stay valid, but the
# resolver no longer emits it. An ESTATE ambiguity (one domain, several claimants) is
# still refused, with the distinct `domain_ambiguous` below.
MULTIPLE_DOMAINS = "multiple_domains"
# An ESTATE-side ambiguity, distinct from the retired `multiple_domains` (hy-gh-282): a
# SINGLE requested domain is claimed by more than one configured context source,
# so no single commit can be the authority. `multiple_domains` is the caller
# naming several domains -- a thing the caller fixes -- and conflating the two
# told a caller who named one domain that it named two, with a recovery
# ("resolve one directive per domain") a directive has no field to carry out.
# This code names the CONFLICTING SOURCES and commits instead, because the
# estate is what needs reconciling (disable or remove all but one). Sync now
# refuses the collision at write time; this is the disclosure for estates that
# already collided before that check existed.
DOMAIN_AMBIGUOUS = "domain_ambiguous"
# The coverage claim (hy-9lct), and the only way it can fail here. A claim
# naming what the domain does not declare is a fact about the corpus, so it
# is disclosed on a bundle. A domain named with no claim at all is a
# malformed request instead -- `ContextDirective` refuses it as
# `invalid_params` before retrieval -- so it has no warning code and must not
# acquire one (hy-bdff).
DOMAIN_DOES_NOT_DECLARE = "domain_does_not_declare"
PLAN_FIRST_REQUIRED = "plan_first_required"
REF_OUTSIDE_CONTEXT = "ref_outside_context"
EVIDENCE_REF_UNRESOLVED = "evidence_ref_unresolved"
# A gap the SNAPSHOT disclosed that has since been corroborated by a connector
# sync (hy-7ejr, hy-gh-118 slice 3). Real estates sync out of order, so a Git
# commit is routinely read before the evidence it cites exists; the ref is
# re-resolved at serve time rather than replayed, and this is what says so.
# Served rather than dropped, because dropping it makes a reconciled bundle
# indistinguishable from a bundle whose snapshot never had that gap, and the
# snapshot's own findings list still says the gap was there. The immutable
# snapshot is not re-authored to remove it -- it records what was true when the
# commit was read -- so the disclosure GAINS this line rather than replacing
# that one.
REF_CORROBORATED_LATE = "ref_corroborated_late"
PROJECTION_BOUNDED = "projection_bounded"
MAX_HOPS_NOT_APPLICABLE = "max_hops_not_applicable"
OBSERVED_PAYLOADS_OMITTED = "observed_payloads_omitted"
OVER_CONTEXT_BUDGET = "over_context_budget"

WARNING_CODES = (
    NO_CONTEXT_SOURCE,
    UNKNOWN_DOMAIN,
    MULTIPLE_DOMAINS,
    DOMAIN_AMBIGUOUS,
    DOMAIN_DOES_NOT_DECLARE,
    PLAN_FIRST_REQUIRED,
    REF_OUTSIDE_CONTEXT,
    # Produced one layer down, where the refusal is known, and imported rather
    # than restated: two spellings of one code is the drift a vocabulary is
    # for. A malformed ref the caller can fix by editing, an ambiguous one it
    # can fix by qualifying, an unobserved one it cannot fix by editing at all,
    # and one awaiting a sync it cannot fix at all -- that one says the estate
    # was never read, so the absence it reports is unmeasured rather than
    # established (hy-lcgq, hy-gh-118).
    REF_MALFORMED,
    REF_AMBIGUOUS,
    REF_NOT_OBSERVED,
    REF_AWAITING_SYNC,
    REF_CORROBORATED_LATE,
    EVIDENCE_REF_UNRESOLVED,
    PROJECTION_BOUNDED,
    MAX_HOPS_NOT_APPLICABLE,
    OBSERVED_PAYLOADS_OMITTED,
    OVER_CONTEXT_BUDGET,
)


# What a caller can fix by asking again, and it is hy-6ae's split used for the
# thing it actually specifies (hy-amtg). That PR divided the ref codes by what
# a caller must DO: edit the ref, qualify it, or neither -- an absent ref needs
# the estate to change, and no re-ask helps. So this is a retryability rule,
# and it governs WARNINGS, which is where ref problems are disclosed: a bad
# ref comes back inside a SERVED bundle, never as a refusal.
#
# Declared here rather than in the planner because this is where the
# vocabulary lives and where `warning()` gates it. A planner-local copy would
# be a second statement of one contract, and the earlier version of this rule
# was exactly that -- a set in `planner/loop.py` naming codes no operation can
# raise.
#
# Bound to `WARNING_CODES` by a test rather than an import-time `assert`: `-O`
# strips asserts, and `warning()`'s gate is a real check, so two gates in one
# module would have had different strength for no stated reason.
#
# `ref_awaiting_sync` is NOT here, and it is the code most likely to be added by
# someone reading it as "come back later" (hy-lcgq). Retryable here means the
# caller can fix it by asking again in the same session, which is why
# `planner/loop.py` records it and `prompts/planner.md` teaches acting on it. A
# ref whose estate has not been read needs a connector sync, which no re-ask
# performs, so listing it would prescribe exactly the loop
# `test_re_sending_a_ref_after_ref_not_observed_is_the_retry_loop_the_prompt_forbids`
# scores against -- the same loop, reached through a code that sounds
# encouraging. What the new code changes is what the absence MEANS, not what
# fixes it: both are unfixable by re-asking, and only one is evidence that the
# asset is gone. That scorer and `prompts/planner.md` still name only
# `ref_not_observed`, so the loop through this code is unmeasured; it is
# hy-yk66, held back because teaching it moves `prompt_hash`.
RETRYABLE_WARNING_CODES = (REF_MALFORMED, REF_AMBIGUOUS)


def warning(code: str, message: str) -> dict:
    """One disclosure: a code a client can branch on and a sentence a person
    can read.

    The code is checked against the vocabulary rather than accepted as given,
    because a warning invented at a call site is a warning no client can
    handle -- the failure this replaces, one layer up.

    The message is the ONE free-text field here and it interpolates untrusted
    input: an evidence-ref warning echoes `ref['ref']`, and a ref carries an
    arbitrary external-id after its 3-part prefix, so a caller can smuggle a
    credential-bearing `scheme://user:token@host` into it. Redacting URL userinfo
    at THIS boundary -- the single factory every served warning flows through --
    keeps the credential out of every consumer (MCP, HTTP, and the chat UI that
    renders the message verbatim), not just one panel (hy-icx1 #448, the
    #447/#448 leak class). It is a no-op on a clean message, so a warning with no
    userinfo is byte-identical and the recorded bundle hashes do not move.
    """
    if code not in WARNING_CODES:
        raise ValueError(f"unknown warning code {code!r}; add it to WARNING_CODES first")
    return {"code": code, "message": redact_free_text_userinfo(message)}


@dataclass
class ContextBundle:
    """One task-oriented answer: what a request asked, what the Git context
    says, which observations back it, and what is unsafe about it."""

    request: dict
    resolution: dict
    context_authority: dict | None
    instructions: dict
    linked_evidence: dict
    domain_graph: dict
    provenance_refs: list[str]
    resolved_at: datetime
    # Always false in v0 and stated on every response: Hyperset does not run
    # or check the customer's SQL (`docs/v0-foundation.md` invariant 6).
    execution: dict = field(
        default_factory=lambda: {
            "performed_by_hyperset": False,
            "result_validated_by_hyperset": False,
        }
    )
    schema_version: int = SCHEMA_VERSION
    # Assist output, and the only section of this bundle that is not governed
    # (ADR 0019). It is `None` on every answer that has none, and it is then
    # ABSENT from the served payload rather than served as null: a caller
    # reading the governed sections of an assisted answer must get exactly the
    # governed answer it would have got with assist switched off, byte for
    # byte, and the cheapest way to make that testable is for the key not to
    # exist. It is deliberately outside `_content()`; see `bundle_id`.
    assist: dict | None = None
    # The per-domain answers when a directive named MORE THAN ONE governed domain
    # (#230 slice 3, hy-cnto). `None` on every single-domain answer, and then
    # ABSENT from the served payload, so a single-domain resolve is byte-for-byte
    # what it was before this field existed. When present it is a list of the full
    # per-domain bundle dicts, EACH byte-identical to that domain's solo resolve --
    # no domain's authority, instructions, or evidence bleeds into another's.
    #
    # It is GOVERNED content (unlike `assist`), so it IS inside `_content()` and
    # covered by `bundle_id`. The guardrail below is the contract the Mayor
    # required (hy-cnto Fork-1): when `domains` is present the FLAT governed fields
    # are empty and `context_authority` is null, which MEANS "authority is
    # per-domain -- read `domains[]`", and can never be read as a single-authority
    # governed answer or an assist/downgraded one.
    domains: list[dict] | None = None
    # The COMPOSED cross-domain graph of a multi-domain answer (#230 slice 5, hy-uaks).
    # `None` on every single-domain answer and ABSENT from the payload, so single-domain
    # stays byte-identical. Present only WITH `domains[]`, and DOMAIN-LEVEL ONLY: its
    # graph carries `domain:{slug}` nodes and governed domain->domain edges (each with its
    # own `evidence` provenance), and NEVER within-domain nodes, instructions, or
    # authority. It exposes how the composed domains relate; it does NOT flatten their
    # separate authorities -- all per-domain content stays in `domains[]` (req 3). Governed
    # content, so it IS inside `_content()` and covered by `bundle_id`.
    composition: dict | None = None

    def __post_init__(self) -> None:
        # Fail-closed enforcement of the multi-domain envelope contract: a bundle
        # that carries `domains[]` MUST carry no flat governed content, so the
        # empty envelope can never be mistaken for a governed-but-empty answer.
        if self.composition is not None and self.domains is None:
            raise ValueError(
                "`composition` is the composed graph of a multi-domain answer and is only "
                "valid alongside `domains[]`"
            )
        if self.composition is not None:
            graph = self.composition.get("graph", {})
            nodes = graph.get("nodes", [])
            for node in nodes:
                # DOMAIN-LEVEL ONLY, on BOTH axes: a node must be kind `domain` AND carry a
                # `domain:{slug}` id. A `kind: "domain"` label over a `field:`/`source:` id
                # would still flatten within-domain content, so the id shape is enforced too.
                if node.get("kind") != "domain" or not str(node.get("id", "")).startswith(
                    "domain:"
                ):
                    raise ValueError(
                        "`composition.graph` is DOMAIN-LEVEL ONLY: every node must be kind "
                        "'domain' with a `domain:{slug}` id; per-domain content stays in "
                        "`domains[]`, never composed here"
                    )
            node_ids = {node.get("id") for node in nodes}
            for edge in graph.get("edges", []):
                # An edge whose endpoint is not one of the composed DOMAIN nodes flattens
                # within-domain content -- refused. The graph is GOVERNED and, THIS SLICE,
                # `contains`-ONLY: an observed edge (`evidence != "git"`) belongs to a
                # different graph, and a governed non-`contains` relation is not composed
                # here until its emit lands (slice 2b adds `depends_on`/`joinable_on`, and
                # will widen this allowlist then). Fail-closed until then.
                if edge.get("from") not in node_ids or edge.get("to") not in node_ids:
                    raise ValueError(
                        "`composition.graph` is DOMAIN-LEVEL ONLY: every edge must connect two "
                        "composed `domain` nodes; a within-domain endpoint flattens content"
                    )
                if edge.get("evidence") != "git":
                    raise ValueError(
                        "`composition.graph` is the GOVERNED composed graph: every edge must be "
                        "`evidence: 'git'`; observed cross-domain relations belong elsewhere"
                    )
                if edge.get("relation") != "contains":
                    raise ValueError(
                        "`composition.graph` carries governed `contains` edges only this slice; "
                        "`depends_on`/`joinable_on` are added when their emit lands (slice 2b)"
                    )
        if self.domains is None:
            return
        if self.context_authority is not None:
            raise ValueError(
                "a multi-domain bundle carries authority per-domain in `domains[]`; "
                "flat `context_authority` must be null"
            )
        if self.instructions or self.provenance_refs:
            raise ValueError(
                "a multi-domain bundle's flat `instructions`/`provenance_refs` must be "
                "empty; the governed content is per-domain in `domains[]`"
            )
        if self.domain_graph.get("nodes") or self.domain_graph.get("edges"):
            raise ValueError(
                "a multi-domain bundle's flat `domain_graph` must be empty; each domain's "
                "graph is in its own `domains[]` entry, and the composed cross-domain graph "
                "is in `composition.graph`"
            )
        if any(self.linked_evidence.get(key) for key in self.linked_evidence):
            raise ValueError(
                "a multi-domain bundle's flat `linked_evidence` must be empty; each "
                "domain's evidence is in its own `domains[]` entry"
            )

    @property
    def bundle_id(self) -> str:
        """Identity of the GOVERNED answer, and only of it.

        `assist` is excluded on purpose (ADR 0019 floor 8). The determinism
        promise in `docs/v0-foundation.md` section 6 is that a pinned commit,
        repository state and directive produce the same bundle; assist output
        need not be reproducible, and folding it into this hash would spend
        that promise on the governed slice too -- caching, equality, and the
        recorded evaluation comparisons all read it. Assist content carries
        its own `assist_id` instead.
        """
        return f"cb-{self.content_hash[:16]}"

    @property
    def content_hash(self) -> str:
        """Hash of everything except when it was resolved."""
        return hashlib.sha256(canonical_json(self._content()).encode()).hexdigest()

    @property
    def status(self) -> str:
        return self.resolution["status"]

    def _content(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "request": self.request,
            "resolution": self.resolution,
            "context_authority": self.context_authority,
            "instructions": self.instructions,
            "linked_evidence": self.linked_evidence,
            "domain_graph": self.domain_graph,
            "provenance_refs": self.provenance_refs,
            "execution": self.execution,
            # Present ONLY on a multi-domain answer, so a single-domain bundle_id and
            # payload are byte-identical to before this field existed (#230 slice 3).
            **({"domains": self.domains} if self.domains is not None else {}),
            # The composed cross-domain graph, present only WITH `domains[]` (#230 slice 5).
            **({"composition": self.composition} if self.composition is not None else {}),
        }

    def to_dict(self) -> dict:
        payload = {"bundle_id": self.bundle_id, "resolved_at": _isoformat(self.resolved_at)}
        payload.update(self._content())
        # bundle_id and resolved_at first, then the contract's own order, then
        # the ungoverned section last and only when there is one.
        if self.assist is not None:
            payload["assist"] = self.assist
        return payload

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False, default=str)


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def canonical_json(payload: dict) -> str:
    """The one deterministic serialization used for every content hash here.

    Public because the assist section hashes itself the same way and a second
    spelling of "canonical" is how two identities stop agreeing about what a
    change is.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
