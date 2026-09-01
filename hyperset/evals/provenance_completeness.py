"""The provenance-completeness grader for the release benchmark (hy-bwo, #25 scope 3).

RE-AUTHORED against the SHIPPED contract, not the deleted `hyperset/trust` residue
(there is no such package on main). It grades whether a served governed answer
carries a COMPLETE, resolvable evidence chain -- GitHub #30's minimum reference
contract -- so the #25 release gate can require 100% evidence completeness for the
canonical-metric regression cases.

COMPLETENESS IS NOT CORRECTNESS. The deterministic scorers judge whether the arm
answered the right question; this judges whether the `ContextBundle` +
`PlanValidation` it served can be AUDITED: every governed and observed reference
present, well-formed, and bound to the evidence it names. A bundle can be complete
and wrong (the scorers catch wrong); a bundle that is incomplete cannot be trusted
either way, which is the hole this closes.

Graded against the served artefacts a governed recording already carries in its
trace -- the `resolve_analytics_context` result (a `ContextBundle`,
`hyperset/bundle/schema.py`) and the `validate_analytics_plan` result (a
`PlanValidation`, `hyperset/bundle/plan.py`). No model, no store, no credential:
it reads the committed recording, like every other #25 per-PR check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Every field #30's minimum reference contract needs to resolve the GOVERNED
# context reference: record kind, scope, stable id, exact version, content hash.
_CONTEXT_AUTHORITY_FIELDS = (
    "type",
    "repository",
    "ref",
    "path",
    "commit_sha",
    "committed_at",
    "context_snapshot_id",
    "content_sha256",
)

# Every field an OBSERVED asset entry needs to be a resolvable reference rather
# than a name: record kind, stable id, exact version, content hash, lifecycle.
_OBSERVED_ASSET_FIELDS = (
    "asset_id",
    "asset_type",
    "observed_version_id",
    "content_sha256",
    "governance",
    "ref",
    "connector",
)

# A provenance ref is `<kind>:<id>` with an optional `@<version>` -- the shape the
# resolver emits (`git_context:<snap>@<commit>`, `observed_version:<oav>`). A ref
# that does not parse cannot be resolved, so it is incomplete by definition.
_PROVENANCE_REF = re.compile(r"^(?P<kind>[a-z_]+):(?P<id>[^@]+)(?:@(?P<version>.+))?$")


@dataclass(frozen=True)
class ProvenanceCompleteness:
    """What a served evidence chain has, and what it is missing.

    `complete` is true only when `missing` is empty: 100% of the required
    references are present, well-formed, and resolvable. `missing` names each
    failed requirement so a gate reports WHICH reference a recording dropped,
    never a bare "incomplete".
    """

    complete: bool
    present: tuple[str, ...]
    missing: tuple[str, ...]


def _nonempty(value: object) -> bool:
    return value is not None and value != "" and value != []


def grade_bundle_completeness(bundle: dict, plan: dict | None) -> ProvenanceCompleteness:
    """Grade one served `ContextBundle` (+ its `PlanValidation`) for completeness.

    Only a GOVERNED bundle carries a governed evidence chain; a `no_match` or
    observed-only bundle has no governed context to reference, so its completeness
    is the disclosure being present, not a chain. This grader is for the governed
    chain and its caller decides which bundles to hold to it (see
    `grade_recording_completeness` and the gate).
    """
    present: list[str] = []
    missing: list[str] = []

    def require(name: str, ok: bool) -> None:
        (present if ok else missing).append(name)

    authority = bundle.get("context_authority") or {}
    for field in _CONTEXT_AUTHORITY_FIELDS:
        require(f"context_authority.{field}", _nonempty(authority.get(field)))

    refs = bundle.get("provenance_refs") or []
    require("provenance_refs.present", bool(refs))
    parsed = {ref: _PROVENANCE_REF.match(ref) for ref in refs}
    require("provenance_refs.well_formed", bool(refs) and all(parsed.values()))

    # Every observed_version reference must RESOLVE to an observed asset by
    # identity -- membership is not enough, a ref naming a version the evidence
    # does not carry is dangling and must be disclosed, not silently accepted.
    observed = bundle.get("linked_evidence", {}).get("observed_assets") or []
    observed_version_ids = {asset.get("observed_version_id") for asset in observed}
    observed_refs = [
        match.group("id")
        for ref, match in parsed.items()
        if match and match.group("kind") == "observed_version"
    ]
    require(
        "provenance_refs.observed_resolve",
        bool(observed_refs) and all(vid in observed_version_ids for vid in observed_refs),
    )

    # The git_context reference must bind to the served context authority: same
    # snapshot id and same commit, or the chain names a context it did not serve.
    git_ctx = next(
        (match for match in parsed.values() if match and match.group("kind") == "git_context"),
        None,
    )
    require(
        "provenance_refs.git_context_binds",
        git_ctx is not None
        and git_ctx.group("id") == authority.get("context_snapshot_id")
        and git_ctx.group("version") == authority.get("commit_sha"),
    )

    require("linked_evidence.observed_assets.present", bool(observed))
    require(
        "linked_evidence.observed_assets.complete",
        bool(observed)
        and all(all(_nonempty(asset.get(f)) for f in _OBSERVED_ASSET_FIELDS) for asset in observed),
    )

    execution = bundle.get("execution") or {}
    require(
        "execution.flags",
        isinstance(execution.get("performed_by_hyperset"), bool)
        and isinstance(execution.get("result_validated_by_hyperset"), bool),
    )

    resolution = bundle.get("resolution") or {}
    require(
        "resolution.complete",
        _nonempty(resolution.get("status"))
        and _nonempty(resolution.get("summary"))
        and "warnings" in resolution,
    )

    require("bundle_id.present", _nonempty(bundle.get("bundle_id")))

    # The plan validation must exist and be traceable to THIS served answer: the
    # arm PLANNED over the served bundle (`planned_bundle_id`), even when the plan
    # status is `unverifiable` because the bundle it re-checked against
    # (`bundle_id`) has since moved -- that is a DISCLOSED freshness state, not a
    # missing reference. Either identity binding it to the served bundle is enough.
    checked = (plan or {}).get("checked_against") or {}
    require("plan.present", bool(plan) and _nonempty((plan or {}).get("status")))
    require(
        "plan.binds_bundle",
        bool(plan)
        and bundle.get("bundle_id") in (checked.get("planned_bundle_id"), checked.get("bundle_id")),
    )
    require(
        "plan.execution.flags",
        isinstance((plan or {}).get("execution", {}).get("performed_by_hyperset"), bool)
        and isinstance((plan or {}).get("execution", {}).get("result_validated_by_hyperset"), bool),
    )

    return ProvenanceCompleteness(
        complete=not missing,
        present=tuple(present),
        missing=tuple(missing),
    )


def _served(trace: dict, operation: str) -> dict | None:
    """The result the arm was served for one operation, from its recorded trace."""
    for step in trace.get("steps") or []:
        detail = step.get("detail") or {}
        if detail.get("operation") == operation and isinstance(detail.get("result"), dict):
            return detail["result"]
    return None


def resolution_status(payload: dict) -> str | None:
    """The status of the governed answer this recording served, or `None`.

    Read off the recorded `resolve_analytics_context` result so a caller can hold
    only GOVERNED answers to the governed-chain contract.
    """
    bundle = _served(payload.get("trace") or {}, "resolve_analytics_context")
    return (bundle or {}).get("resolution", {}).get("status") if bundle else None


def grade_recording_completeness(payload: dict) -> ProvenanceCompleteness:
    """Grade a committed recording's served evidence chain.

    Extracts the `ContextBundle` and `PlanValidation` the arm was served from the
    recorded trace and grades them. A recording that served no bundle at all is
    maximally incomplete (it references nothing), which a gate reads as a failure
    rather than a pass.
    """
    trace = payload.get("trace") or {}
    bundle = _served(trace, "resolve_analytics_context")
    plan = _served(trace, "validate_analytics_plan")
    if bundle is None:
        return ProvenanceCompleteness(
            complete=False,
            present=(),
            missing=("resolve_analytics_context.served",),
        )
    return grade_bundle_completeness(bundle, plan)
