"""Flywheel step 4: an agent drafts ONE candidate definition (hy-jg2v).

The expert works WITH an agent that DRAFTS one candidate governed-context
definition from a miss (step 1) plus its gathered observed sources (step 2); the
expert edits and approves. This is not a co-authoring product and the draft is
never authority: it is an assist-class proposal, persisted UNAPPROVED, and the
only thing that confers approval stays `ReviewRepository.approve` over a human
decision -- with authority ultimately a human Git commit (ADR 0012, ADR 0019).

The draft is produced by driving the EXISTING agent loop
(`plan_analytics_context`) with a new authoring prompt, authoring tool
declarations, and an `AuthoringExecutor`. Three things keep the boundary
structural rather than careful:

- The loop reaches the `AuthoringExecutor`, never `InProcessExecutor` and never
  the locked `RESOLVE_PATH_OPERATIONS`, so the pinned #25 recordings and
  `tools_hash` are untouched -- the same coexistence the raw benchmark arm has.
- The executor holds no governed writer and no warehouse client, so it cannot
  approve, persist a governed version, or run SQL: the leak is not expressible.
- The draft lands as a `ReviewTask.proposal_payload`, which writes none of
  `governed_context` / `governed_context_versions` / `review_decisions`.

The agent emits its draft by CALLING a tool, not by writing prose a caller then
greps: a structured tool call is captured deterministically, the same reason the
resolver stopped reading the question's words (GitHub #70).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from hyperset.context.errors import ContextValidationError
from hyperset.context.schema import validate_definition_draft
from hyperset.flywheel.live_lookup import body_for_reference
from hyperset.planner.executor import ToolResult
from hyperset.planner.loop import plan_analytics_context
from hyperset.planner.runtime import Runtime
from hyperset.planner.trace import Trace, content_hash
from hyperset.repositories.dto import ReviewTaskRecord
from hyperset.repositories.postgres import (
    PostgresObservedAssetRepository,
    PostgresReviewRepository,
)

GET_GATHERED_SOURCE = "get_gathered_source"
LIVE_LOOKUP_ASSET = "live_lookup_asset"
PROPOSE_CONTEXT_DEFINITION = "propose_context_definition"

# What produced the draft, named for ADR 0019 floor 9. The model is reported
# separately, from the trace, because only the runtime knows what it used.
PRODUCER = "authoring/1"
# The label the draft carries so nothing can read it as governed. It is not an
# observation either -- it is an unapproved proposal -- so it says exactly that.
GOVERNANCE = "unapproved"

AUTHORING_TOOL_ERROR = "authoring_tool_error"


class AuthoringToolError(Exception):
    """A refusal from a tool that is not a served operation -- its own type,
    like the benchmark arm's, so it cannot add a code to the served
    `ERROR_CODES` vocabulary a client is told to handle."""

    def __init__(self, message: str, *, recovery: str) -> None:
        super().__init__(message)
        self.code = AUTHORING_TOOL_ERROR
        self.message = message
        self.recovery = recovery

    def to_dict(self) -> dict:
        return {"error": {"code": self.code, "message": self.message, "recovery": self.recovery}}


class AuthoringExecutor:
    """The authoring run's tool surface. Synchronous, like `InProcessExecutor`.

    It can read a gathered source, live-lookup an asset body, and capture the
    one draft the agent proposes. It holds NO governed writer and NO warehouse
    client: `session_factory` reaches only the read-only observation and
    connection repositories, and the body-read goes through a read-only
    transport. `propose_context_definition` writes to an in-process attribute,
    not a store -- persistence is the driver's, after validation.
    """

    def __init__(self, *, session_factory, gathered: dict, transport_factory=None) -> None:
        self._session_factory = session_factory
        self._assets = PostgresObservedAssetRepository(session_factory)
        self._by_ref = {
            candidate["ref"]: candidate for candidate in (gathered or {}).get("candidates", [])
        }
        self._transport_factory = transport_factory
        self.draft: dict | None = None

    def call(self, operation: str, params: dict) -> ToolResult:
        try:
            if operation == GET_GATHERED_SOURCE:
                return ToolResult(payload=self._gathered_source(params))
            if operation == LIVE_LOOKUP_ASSET:
                return ToolResult(payload=self._live_lookup(params))
            if operation == PROPOSE_CONTEXT_DEFINITION:
                return ToolResult(payload=self._propose(params))
        except AuthoringToolError as error:
            return ToolResult(error=error)
        return ToolResult(
            error=AuthoringToolError(
                f"unknown tool {operation!r}",
                recovery=(
                    f"call {GET_GATHERED_SOURCE}, {LIVE_LOOKUP_ASSET} or "
                    f"{PROPOSE_CONTEXT_DEFINITION}"
                ),
            )
        )

    def _candidate(self, params: dict) -> dict:
        ref = params.get("ref")
        candidate = self._by_ref.get(ref)
        if candidate is None:
            raise AuthoringToolError(
                f"{ref!r} is not one of the gathered sources for this miss",
                recovery=f"call {GET_GATHERED_SOURCE} with a ref from the gathered candidates",
            )
        return candidate

    def _gathered_source(self, params: dict) -> dict:
        """The held facts a gathered candidate already carries -- what it is, and
        the observed expressions the ranking read -- without a source call."""
        candidate = self._candidate(params)
        asset = self._assets.get_by_external_id(
            connection_id=candidate["connection_id"],
            external_id=candidate["external_id"],
            asset_type=candidate["asset_type"],
        )
        version = asset.current_version
        return {
            "ref": candidate["ref"],
            "asset_type": candidate["asset_type"],
            "external_id": candidate["external_id"],
            "governance": GOVERNANCE,
            "normalized": version.normalized if version is not None else {},
        }

    def _live_lookup(self, params: dict) -> dict:
        """The candidate asset's body, read-only. Held observation if full, else
        a live read -- never warehouse SQL (ADR 0024 decision 2)."""
        candidate = self._candidate(params)
        return body_for_reference(
            connection_id=candidate["connection_id"],
            external_id=candidate["external_id"],
            asset_type=candidate["asset_type"],
            session_factory=self._session_factory,
            transport_factory=self._transport_factory,
        )

    def _propose(self, params: dict) -> dict:
        """Capture the one draft. A second proposal replaces the first: exactly
        one candidate definition per miss (the bead's scope)."""
        definition = params.get("definition")
        if not isinstance(definition, dict):
            raise AuthoringToolError(
                "propose_context_definition needs a 'definition' mapping",
                recovery="pass the candidate definition as a manifest-shaped mapping",
            )
        self.draft = definition
        return {"captured": True}


@dataclass
class DraftOutcome:
    """What one authoring run produced. Exactly one of `task`/`reasons` is
    meaningful: a persisted unapproved proposal, or the reasons it was refused."""

    status: str  # "drafted" | "invalid" | "no_draft"
    trace: Trace
    task: ReviewTaskRecord | None = None
    reasons: list[str] = field(default_factory=list)


def draft_definition(
    *,
    domain: str,
    undeclared: list[str],
    question: str,
    gathered: dict,
    runtime: Runtime,
    session_factory,
    review_repository: PostgresReviewRepository | None = None,
    transport_factory=None,
    resolve_miss_id: str | None = None,
    feedback: str | None = None,
    existing_task_id: str | None = None,
    workspace: str = "default",
) -> DraftOutcome:
    """Drive one authoring run and persist its draft UNAPPROVED, or refuse it.

    Drives `plan_analytics_context` with the authoring prompt, tools and
    executor; validates the emitted draft against the SAME manifest rules a
    human's Git commit faces (`validate_definition_draft`); and on success
    persists it as a `ReviewTask.proposal_payload` carrying its attribution. An
    invalid draft is refused and NOT persisted; a run that emitted nothing is a
    `no_draft`. Nothing here approves, and nothing writes a governed table.

    `feedback` is an expert's steer on a re-run: it travels WITH THE QUESTION
    (never the instructions, so the prompt hash is unchanged) and is recorded on
    the trace like any question. `existing_task_id` makes the re-run REPLACE the
    draft on that task rather than create a new one -- still assist-class, still
    `unapproved`, a draft replacing a draft (hy-murb).
    """
    driven = json.dumps(
        {
            "domain": domain,
            "undeclared_concepts": list(undeclared),
            "question": question,
            "gathered_sources": _gathered_summary(gathered),
            "expert_feedback": feedback,
        },
        sort_keys=True,
    )
    executor = AuthoringExecutor(
        session_factory=session_factory, gathered=gathered, transport_factory=transport_factory
    )
    trace = plan_analytics_context(
        driven,
        runtime=runtime,
        executor=executor,
        instructions=authoring_prompt(),
        declarations=authoring_tool_specs(),
    )
    if executor.draft is None:
        return DraftOutcome(
            status="no_draft",
            trace=trace,
            reasons=["the authoring run emitted no candidate definition"],
        )
    try:
        normalized = validate_definition_draft(executor.draft, domain=domain)
    except ContextValidationError as error:
        # Refused, not persisted: an invalid draft never becomes even an
        # unapproved artifact.
        return DraftOutcome(status="invalid", trace=trace, reasons=list(error.reasons))

    proposal_payload = {
        "governance": GOVERNANCE,
        "domain": domain,
        "undeclared_concepts": list(undeclared),
        # The miss the expert is judging this proposal against: the literal
        # question, and a link back to the step-1 ResolveMiss row when the caller
        # has one. Persisted, not transient, so the Review surface shows what the
        # draft answers rather than only the draft (hy-1q9w).
        "miss": {
            "question": question,
            "domain": domain,
            "undeclared_concepts": list(undeclared),
            "resolve_miss_id": resolve_miss_id,
        },
        # A summary of the step-2 gathered sources the agent read while drafting.
        # Persisted so the expert can see which observed sources were considered
        # and how they ranked -- the sources were used transiently during the run
        # and would otherwise be discarded (hy-1q9w). Assist-labelled: every entry
        # stays `observed`, never governed.
        "gathered_sources": _gathered_summary(gathered),
        "definition": executor.draft,
        "normalized": normalized,
        # ADR 0019 floor 9: name what produced it; the model comes from the
        # runtime's own report on the trace, never a caller-set value.
        "produced_by": {"producer": PRODUCER, "model": trace.model or None},
        "provenance": {
            "prompt_hash": trace.prompt_hash,
            "tools_hash": trace.tools_hash,
            "model": trace.model,
            "runtime": trace.provenance,
        },
    }
    if feedback:
        proposal_payload["feedback"] = feedback
    repository = review_repository or PostgresReviewRepository(session_factory)
    if existing_task_id is not None:
        # A re-run replaces the draft on the SAME task -- assist-class, still
        # unapproved, no new task and no governed row.
        task = repository.set_proposal_payload(
            existing_task_id, proposal_payload, workspace=workspace
        )
    else:
        task = repository.create_task(
            reason=f"agent-drafted candidate definition for {domain!r} (unapproved)",
            idempotency_key=f"authoring:{domain}:{content_hash(question)}",
            workspace=workspace,
            proposal_payload=proposal_payload,
        )
    return DraftOutcome(status="drafted", trace=trace, task=task)


def _gathered_summary(gathered: dict) -> list[dict]:
    """The step-2 gathered candidates as a small, persistable summary: what each
    source is, how it ranked, and which signals ranked it. Assist output, so it
    keeps the `observed` label and carries no governed field."""
    return [
        {
            "rank": candidate.get("rank"),
            "ref": candidate.get("ref"),
            "asset_type": candidate.get("asset_type"),
            "governance": candidate.get("governance"),
            "signals": [signal.get("signal") for signal in candidate.get("signals", [])],
        }
        for candidate in (gathered or {}).get("candidates", [])
    ]


def authoring_prompt() -> str:
    from pathlib import Path

    return (Path(__file__).parent / "prompts" / "authoring.md").read_text()


def authoring_tool_specs() -> list[dict]:
    """The authoring run's OWN tool declarations -- separate from the resolve
    path's `tool_specs()`, so they never move `tools_hash` or the #25
    recordings."""
    ref_property = {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "A gathered source ref, 'connector:asset_type:external_id'.",
            }
        },
        "required": ["ref"],
        "additionalProperties": False,
    }
    return [
        {
            "name": GET_GATHERED_SOURCE,
            "description": (
                "Return the observed facts a gathered source already carries -- its type and "
                "the expressions the ranking read -- without any source call. These are observed, "
                "not approved or governed."
            ),
            "input_schema": ref_property,
        },
        {
            "name": LIVE_LOOKUP_ASSET,
            "description": (
                "Read one gathered asset's definition/metadata body. If the observation already "
                "holds the body it is returned as-is; otherwise it is read live from the source "
                "through a read-only transport. This NEVER runs the asset's query against the "
                "warehouse -- it reads the asset object only. Use it only when you need a body "
                "you do not already have."
            ),
            "input_schema": ref_property,
        },
        {
            "name": PROPOSE_CONTEXT_DEFINITION,
            "description": (
                "Emit exactly ONE candidate governed-context definition, in the manifest shape "
                "(definitions, approved_sources, fields, joins, filters, grain, checks, caveats). "
                "This is an UNAPPROVED proposal for a human to edit and approve into Git -- never "
                "approved, canonical, or validated by emitting it."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "definition": {
                        "type": "object",
                        "description": "The candidate definition, manifest-shaped.",
                    }
                },
                "required": ["definition"],
                "additionalProperties": False,
            },
        },
    ]
