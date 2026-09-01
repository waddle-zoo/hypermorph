"""The offline processor's one job in v0 (hy-gh-38): take a completed sync,
compare the pinned Git context against what the sources now say, and persist
one explainable `Finding` plus one idempotent human `ReviewTask` per real
disagreement.

Walking-skeleton step 8. Step 7 (the source change and its `ConnectorChange`)
belongs to the connectors; steps 9+ (`ContextBundle`, plan validation) read
findings later. Nothing here edits Git, approves context, or writes a
governed version -- v0 review is a human commit in the customer's
repository (ADR 0012). The one write this module makes beyond a finding is
`review.create_task`, which OPENS a human review task in the "open" state; it
never approves, never resolves, and never writes a governed version (that is
`review.approve`, which this module does not call).

Rerunning a sync's processing is safe: the rule is a pure function of pinned
inputs, and persistence keys findings on (rule, asset, context commit); the
review task is keyed on the same subject, so a second run over unchanged
evidence records neither a new finding nor a new task.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from hyperset.processor.rules import (
    FINDING_TYPES,
    RULE_VERSIONS,
    GitContext,
    ObservedSource,
    approved_expression_drift,
)
from hyperset.repositories.dto import FindingRecord
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresConnectorChangeRepository,
    PostgresContextRepository,
    PostgresObservedAssetRepository,
    PostgresProcessorRepository,
    PostgresReviewRepository,
)

TRIGGER_TYPE = "sync"


@dataclass
class ProcessingResult:
    """One processing pass. `status` is "succeeded", or "already_running"
    when another worker holds the claim for this sync run -- not an error,
    just this caller having nothing to do."""

    sync_run_id: str
    status: str
    processor_run_id: str | None = None
    findings: list[FindingRecord] = field(default_factory=list)
    counters: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def run_sync_processing(*, sync_run_id: str, session_factory) -> ProcessingResult:
    processor = PostgresProcessorRepository(session_factory)
    reviews = PostgresReviewRepository(session_factory)
    # Claiming is the concurrency boundary: Postgres rejects a second running
    # row for the same trigger, so two workers cannot process one sync at once
    # regardless of process boundaries.
    run = processor.claim_run(
        trigger_type=TRIGGER_TYPE, trigger_ref=sync_run_id, rule_versions=RULE_VERSIONS
    )
    if run is None:
        return ProcessingResult(sync_run_id=sync_run_id, status="already_running")

    try:
        contexts, changes, warnings = _load(session_factory, sync_run_id)
        candidates = [
            candidate for context in contexts for candidate in approved_expression_drift(context)
        ]

        findings: list[FindingRecord] = []
        created = deduplicated = 0
        for candidate in candidates:
            # Open the human review task first, keyed on the same subject as the
            # finding, so the queue `list_review_tasks` serves populates from a
            # real sync. `create_task` is idempotent on the key, so a rerun over
            # the same disagreement returns the existing task rather than a
            # duplicate -- matching the finding's own (rule, asset, commit) dedup.
            task = reviews.create_task(
                reason=candidate.explanation,
                idempotency_key=_review_task_key(candidate),
                priority=_severity_priority(candidate.severity),
                affected_asset_ids=[candidate.asset_id],
                proposal_payload={
                    "finding_type": candidate.finding_type,
                    **candidate.proposed_action,
                },
                processor_evidence=candidate.evidence,
            )
            record, is_new = processor.record_current_finding(
                processor_run_id=run.id,
                finding_type=candidate.finding_type,
                rule_version=candidate.rule_version,
                affected_asset_id=candidate.asset_id,
                affected_context_snapshot_id=candidate.context_snapshot_id,
                severity=candidate.severity,
                explanation=candidate.explanation,
                evidence=candidate.evidence,
                proposed_action=candidate.proposed_action,
                proposed_reviewer=candidate.proposed_reviewer,
                review_task_id=task.id,
            )
            findings.append(record)
            created += int(is_new)
            deduplicated += int(not is_new)

        resolved = _resolve_settled(processor, contexts, candidates)

        counters = {
            "context_snapshots": len(contexts),
            "connector_changes": len(changes),
            "fields_evaluated": sum(len(context.fields) for context in contexts),
            "findings_created": created,
            "findings_deduplicated": deduplicated,
            "findings_resolved": resolved,
        }
        processor.finish_run(run.id, counters=counters, warnings=warnings)
    except Exception as exc:
        # Evidence first: the run keeps why it failed, so a retry starts from
        # a recorded reason rather than a silent gap.
        processor.fail_run(run.id, errors=[str(exc)])
        raise

    return ProcessingResult(
        sync_run_id=sync_run_id,
        status="succeeded",
        processor_run_id=run.id,
        findings=findings,
        counters=counters,
        warnings=warnings,
    )


def _review_task_key(candidate) -> str:
    """The idempotency key for a candidate's review task: the same (finding
    type, asset, context commit) subject the finding is deduplicated on, so the
    task and the finding stay one-to-one across reruns. A drift under a new
    commit is a new subject -- a new finding and a new task -- exactly as the
    finding's supersede-per-commit behaviour intends."""
    finding_type, asset_id, snapshot_id = candidate.dedup_key
    return f"processor:{finding_type}:{asset_id}:{snapshot_id}"


def _severity_priority(severity: str) -> int:
    """Map a finding's severity to a task priority (lower is more urgent). An
    `error`/`critical` disagreement is a contradiction a reviewer should see
    first; everything else is the default queue priority."""
    return 1 if severity in {"error", "critical"} else 2


def _resolve_settled(processor, contexts, candidates) -> int:
    """Close findings whose disagreement no longer reproduces.

    Either side can end it: the customer commits the source's expression into
    Git, or the source is put back. Both are humans deciding, and both land
    here as "the rule ran again over the same asset and found nothing".
    Findings for assets this pass did not evaluate are left alone -- absence
    of evidence is not evidence the problem is gone.

    Keyed on (finding type, asset), not on the asset alone. One comparison now
    has three outcomes (ADR 0021), and dedup is per finding type, so an asset
    whose disagreement changes SHAPE -- a real drift becoming a qualifier-only
    difference the warehouse would have to settle -- would otherwise carry the
    old `error` and the new `warning` at once, each looking current. Closing per
    type means the pass that stops producing an outcome also ends it.
    """
    evaluated = {
        source.asset_id for context in contexts for source in context.sources_by_ref.values()
    }
    still_open = {(candidate.finding_type, candidate.asset_id) for candidate in candidates}
    resolved = 0
    for finding_type in FINDING_TYPES:
        for finding in processor.list_findings(finding_type=finding_type, state="current"):
            asset_id = finding.affected_asset_id
            if asset_id not in evaluated or (finding_type, asset_id) in still_open:
                continue
            processor.resolve_finding(finding.id)
            resolved += 1
    return resolved


def _load(session_factory, sync_run_id: str):
    """Read every enabled Git context that has a snapshot, and resolve its
    declared refs to the assets as they stand *now*."""
    changes = PostgresConnectorChangeRepository(session_factory).list_for_run(sync_run_id)
    changes_by_asset = defaultdict(list)
    for change in changes:
        # An asset can carry more than one change in one run: `upsert` is a
        # public repository call and may run twice under a single
        # `sync_run_id`. They are grouped as evidence; nothing iterates them.
        changes_by_asset[change.asset_id].append(change)

    assets = PostgresObservedAssetRepository(session_factory)
    contexts: list[GitContext] = []
    warnings: list[str] = []

    for source in PostgresContextRepository(session_factory).list_sources():
        snapshot = source.current_snapshot
        if not source.enabled or snapshot is None:
            continue
        if source.last_attempt_status == "failed":
            warnings.append(
                f"context source {source.id} last failed to sync ({source.last_error}); "
                f"evaluating the last valid commit {snapshot.commit_sha}"
            )
        sources_by_ref = {}
        for ref in snapshot.evidence_refs:
            try:
                # Deliberately the asset's *current* version, not the version
                # the ref resolved to when the context was synced: the gap
                # between those two is the drift being looked for.
                asset = assets.get(ref["asset_id"])
            except NotFoundError:
                warnings.append(
                    f"evidence ref {ref['ref']} of context {snapshot.commit_sha} "
                    f"points at asset {ref['asset_id']}, which no longer exists"
                )
                continue
            sources_by_ref[ref.get("governed_source_ref") or ref["ref"]] = ObservedSource(
                asset_id=asset.id,
                external_id=asset.external_id,
                asset_type=asset.asset_type,
                version_id=asset.current_version.id if asset.current_version else None,
                expressions=_expressions(asset),
                deleted=asset.deleted_at is not None,
                changes=tuple(
                    {
                        "id": change.id,
                        "change_type": change.change_type,
                        "sync_run_id": change.sync_run_id,
                    }
                    for change in changes_by_asset.get(asset.id, ())
                ),
                # The link point the movement fact is measured against: the
                # version this ref resolved to when the context was synced.
                linked_version_id=ref.get("observed_version_id"),
                expressions_at_link=_expressions_at(assets, asset, ref.get("observed_version_id")),
            )
        contexts.append(
            GitContext(
                snapshot_id=snapshot.id,
                commit_sha=snapshot.commit_sha,
                repository=source.repository,
                ref=source.ref,
                path=source.path,
                domain=snapshot.domain,
                fields=snapshot.normalized.get("fields", []),
                sources_by_ref=sources_by_ref,
                owner_refs=[owner["ref"] for owner in snapshot.owner_refs],
            )
        )
    return contexts, changes, warnings


def _expressions(asset) -> dict[str, str]:
    """What the asset computes now."""
    version = asset.current_version
    return {} if version is None else _metrics(version)


def _expressions_at(assets, asset, version_id: str | None) -> dict[str, str] | None:
    """What the version the commit linked computed, or `None` when there is
    nothing to read: the commit pinned no version for the ref, or the version it
    pinned is gone. Those are different facts and the rule states them
    differently (ADR 0021 decision 3).

    Costs one `history` read per declared ref whose link point is not already the
    current version -- which is the drifted refs, since an unchanged asset's
    linked version IS its current one. The cheap alternative, comparing version
    ids, answers "did this asset change" and a finding is about one field: a
    version written for another field would report movement in a field nothing
    touched.
    """
    if version_id is None:
        return None
    if asset.current_version is not None and asset.current_version.id == version_id:
        return _metrics(asset.current_version)
    for version in assets.history(asset.id):
        if version.id == version_id:
            return _metrics(version)
    return None


def _metrics(version) -> dict[str, str]:
    """The connector's normalized metrics, keyed by name. A source that
    declares no metrics (a DataHub glossary term, a Superset database)
    contributes nothing rather than an error."""
    return {
        metric["name"]: metric["expression"]
        for metric in version.normalized.get("metrics", [])
        if metric.get("name")
    }
