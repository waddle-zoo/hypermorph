"""Narrow, capability-oriented repository protocols (hy-gh-26 "Repository
boundaries"): the domain/service layer depends on these, never on
`hyperset.db.models` or SQLAlchemy directly. Each is deliberately scoped to
one entity family's required workflows rather than a generic
`Repository[T]` — a future DynamoDB implementation can satisfy the ones
that fit its access patterns without pretending SQL joins and DynamoDB
partition/sort keys are the same abstraction (MANIFESTO.md "Storage and
Deployment Must Remain Replaceable").
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hyperset.repositories.dto import (
    AssetRelationshipRecord,
    ConnectionRecord,
    ConnectorChangeRecord,
    ContextSnapshotRecord,
    ContextSourceRecord,
    EvaluationCaseRecord,
    EvaluationRunRecord,
    FindingRecord,
    GovernedContextRecord,
    GovernedContextVersionRecord,
    IncomingReferenceRecord,
    ObservedAssetRecord,
    ObservedAssetVersionRecord,
    ProcessorRunRecord,
    ResolveMissRecord,
    ReviewApprovalResult,
    ReviewDecisionRecord,
    ReviewTaskRecord,
    SyncRunRecord,
)
from hyperset.repositories.scope import _AllWorkspaces


class ConnectionRepository(Protocol):
    def create_or_update(
        self,
        *,
        connection_id: str | None = None,
        connector_type: str,
        display_name: str,
        config_encrypted: bytes | None = None,
        config_ref: str | None = None,
        enabled: bool = True,
    ) -> ConnectionRecord: ...

    def get(self, connection_id: str, *, workspace: str | None = None) -> ConnectionRecord: ...

    def list(
        self, *, workspace: str | _AllWorkspaces, enabled_only: bool = False
    ) -> list[ConnectionRecord]:
        """Connections in `workspace` (hq-t6nx). `workspace` is REQUIRED and
        FAIL-CLOSED: a concrete tenant scopes the list; only the explicit
        `ALL_WORKSPACES` sentinel reads across every tenant (a SYSTEM opt-in). There is
        no silent global default, so an enumeration -- and the observed assets keyed on
        these connections -- can never span tenants by omission."""

    def record_health(
        self, connection_id: str, *, status: str, detail: str | None = None
    ) -> ConnectionRecord: ...


class SyncRepository(Protocol):
    def begin_run(
        self, connection_id: str, *, mode: str, transport: str | None = None
    ) -> SyncRunRecord:
        """`mode` is what kind of sync this is ("full", "fixture_import");
        `transport` is the transport that read it. They are not the same
        question -- REST and GraphQL are both "full" -- and change detection
        compares within a read mode (hy-6t4), so a reader asking which lineage
        a run's counts were measured against needs the second."""

    def finish_run(
        self, run_id: str, *, counters: dict, warnings: list[str] | None = None
    ) -> SyncRunRecord: ...

    def fail_run(self, run_id: str, *, errors: list[str]) -> SyncRunRecord: ...

    def get_run(self, run_id: str) -> SyncRunRecord: ...

    def list_runs(self, connection_id: str) -> list[SyncRunRecord]: ...

    def get_checkpoint(self, connection_id: str) -> dict | None: ...

    def set_checkpoint(self, connection_id: str, *, checkpoint: dict, sync_run_id: str) -> None: ...


class ObservedAssetRepository(Protocol):
    def upsert(
        self,
        *,
        connection_id: str,
        external_id: str,
        asset_type: str,
        sync_run_id: str,
        raw_payload: dict,
        normalized: dict | None = None,
        source_modified_at: datetime | None = None,
        hash_basis: dict | None = None,
    ) -> tuple[ObservedAssetRecord, str]:
        """Upsert asset identity; append a new version only if the source
        content actually changed (by content hash). `raw_payload` is stored
        whole; `hash_basis`, when supplied, is the rule narrowing what the
        hash covers, stored on the version row so the hash stays recomputable
        (`hyperset.repositories.hash_basis`). Returns `(record, outcome)`,
        where `outcome` is the `ConnectorChange.change_type` written or
        "unchanged" when none was.

        `source_modified_at=None` means the read disclosed none and leaves any
        timestamp an earlier read proved untouched.

        A new version also writes exactly one `ConnectorChange`
        ("created"/"updated") in the same transaction. Observing a
        soft-deleted asset again clears `deleted_at` and writes one
        "restored" change instead, even when the content is unchanged and
        no version is appended, so the change stream never leaves a live
        asset looking deleted -- and neither does the returned outcome."""

    def mark_missing_deleted(
        self, *, connection_id: str, asset_type: str, seen_external_ids: set[str], sync_run_id: str
    ) -> list[str]:
        """Soft-delete every current asset of `asset_type` on `connection_id`
        not in `seen_external_ids`, each with one "deleted" `ConnectorChange`
        attributed to `sync_run_id`. Returns the deleted asset ids. History
        is never removed."""

    def get(self, asset_id: str) -> ObservedAssetRecord: ...

    def get_by_external_id(
        self, *, connection_id: str, external_id: str, asset_type: str
    ) -> ObservedAssetRecord: ...

    def history(self, asset_id: str) -> list[ObservedAssetVersionRecord]: ...

    def list_all(
        self,
        *,
        workspace: str | _AllWorkspaces,
        connection_id: str | None = None,
        asset_type: str | None = None,
        include_deleted: bool = True,
    ) -> list[ObservedAssetRecord]:
        """Every asset at HEAD -- for policy scans, not search relevance. `workspace`
        is REQUIRED and FAIL-CLOSED (hq-t6nx): the scan joins `Connection` and returns
        only assets whose connection is in `workspace`; only `ALL_WORKSPACES` spans
        every tenant. No silent global default."""

    def search(
        self, query: str, *, asset_type: str | None = None, limit: int = 20
    ) -> list[ObservedAssetRecord]: ...

    def count_by_type(self, *, workspace: str | _AllWorkspaces) -> list[tuple[str, str, int]]:
        """(connection_id, asset_type, live count), for the discovery catalog: how much
        of each kind exists without listing the corpus. `workspace` is REQUIRED and
        FAIL-CLOSED (hq-t6nx): the count covers only connections in `workspace`, so one
        tenant's catalog never carries another's connection id or count; only
        `ALL_WORKSPACES` counts across every tenant."""

    def replace_relationships(
        self, *, declared: dict[str, list[tuple[str, str]]]
    ) -> list[AssetRelationshipRecord]:
        """Rewrite the outgoing references of each asset in `declared` to
        exactly the `(relation, to_asset_id)` pairs given for it, and return
        the resulting rows.

        `declared` maps a from-asset id to what its newest observation
        declared and sync could resolve -- one entry per asset, so a source
        that served the same asset twice in one run resolves to its last
        payload, the same last-one-wins the version chain applies (hy-3h7f).
        An entry mapped to `[]` is not a no-op: it means that asset's latest
        payload declared no resolvable reference, so its previous rows are
        removed rather than left standing as a claim the source no longer
        makes (hy-d7xh). An asset absent from `declared` is untouched, so a
        partial sync never retracts references it did not re-read.

        Idempotent by (from, to, relation): re-observing an unchanged asset
        writes no row and changes no row id."""

    def list_relationships(
        self,
        *,
        from_asset_id: str | None = None,
        to_asset_id: str | None = None,
        include_deleted: bool = True,
    ) -> list[AssetRelationshipRecord]:
        """Declared references out of and/or into one asset -- at least one
        endpoint is required, since the projection is never read whole.

        Rows survive their endpoints being soft-deleted, so the default is
        `include_deleted=True` exactly as `list_all`'s is. Counting *live*
        references is `include_deleted=False`, which the store answers with a
        join: the record carries no `deleted_at`, so a caller filtering for
        itself would pay one read per row (hy-z21y)."""

    def list_live_references(self, *, to_asset_ids: list[str]) -> list[IncomingReferenceRecord]:
        """Live references INTO each of `to_asset_ids`, in one statement.

        The set-at-a-time form of `list_relationships(to_asset_id=...,
        include_deleted=False)`, for the caller that ranks every candidate in
        the estate and would otherwise issue one statement per candidate. Live
        means both endpoints are undeleted. Rows carry the REFERRING asset's own
        identity, so a count can name what it counted (hy-g1y8)."""


class ConnectorChangeRepository(Protocol):
    """Read-only view of connector-owned change events. Writes happen in
    `ObservedAssetRepository`, transactionally with the version they
    describe, so nothing can record a change that never became an
    observation."""

    def list_for_run(self, sync_run_id: str) -> list[ConnectorChangeRecord]: ...

    def list_for_asset(self, asset_id: str) -> list[ConnectorChangeRecord]: ...


class ResolveMissRepository(Protocol):
    """Operational miss-log (hy-jrpm). Written at the transport boundary, never
    by the resolver, and never a system of record for meaning (ADR 0012, ADR
    0020 decision 5): it records what missed, not what anything means."""

    def record(
        self,
        *,
        query: str,
        directive: dict,
        status: str,
        warning_codes: list[str],
        bundle_id: str,
    ) -> ResolveMissRecord: ...

    def recent(self, *, limit: int = 50) -> list[ResolveMissRecord]: ...


class ContextRepository(Protocol):
    """Git-owned context persistence (hy-gh-43, ADR 0012).

    Records what Git said at an exact commit and how the last read went. It
    has no method that creates or edits a context version from anything but
    a commit, because a Postgres-only edit can never be authoritative
    domain meaning."""

    def register_source(
        self,
        *,
        repository: str,
        ref: str,
        path: str,
        display_name: str | None = None,
        workspace: str = "default",
    ) -> ContextSourceRecord:
        """Get-or-create the configured source identity `(workspace, repository,
        ref, path)` (hq-t6nx). Re-registering an existing identity never clears its
        snapshots or checkpoint. A source is stamped with `workspace` ('default'
        for a single-tenant/internal caller); two tenants may hold the same
        `(repository, ref, path)` pointer as distinct sources."""

    def get_source(
        self, source_id: str, *, workspace: str | None = None
    ) -> ContextSourceRecord: ...

    def get_source_by_identity(
        self, *, repository: str, ref: str, path: str, workspace: str | None = None
    ) -> ContextSourceRecord:
        """Resolve one source by its `(repository, ref, path)` pointer (hq-t6nx).

        A tenant-sensitive caller MUST pass `workspace`: identity is
        `(workspace, repository, ref, path)`, so a scoped lookup is unique and
        confined to that tenant. A workspace-LESS lookup matches on the pointer
        alone and FAILS CLOSED when two tenants share it -- it raises
        `AmbiguousIdentityError` (a `NotFoundError` subclass) rather than pick a
        tenant or crash, and `NotFoundError` when nothing matches."""

    def list_sources(self, *, workspace: str | None = None) -> list[ContextSourceRecord]: ...

    def record_snapshot(
        self,
        *,
        source_id: str,
        commit_sha: str,
        committed_at: datetime | None,
        domain: str,
        title: str,
        files: dict,
        normalized: dict,
        owner_refs: list | None = None,
        evidence_refs: list | None = None,
        evidence_findings: list | None = None,
    ) -> tuple[ContextSnapshotRecord, bool]:
        """Append the immutable snapshot for `commit_sha` and make it
        current. Returns `(record, created)`; a commit already snapshotted
        returns the existing row unchanged with `created=False` rather than
        rewriting context history.

        `evidence_refs` are the declared refs that resolved to an observed
        asset and `evidence_findings` the ones that did not. A snapshot is
        recorded whichever way that went: corroboration state is disclosed,
        never a precondition for Git context existing (ADR 0017)."""

    def record_unchanged(self, source_id: str, *, commit_sha: str) -> ContextSourceRecord:
        """The configured ref still points at the snapshotted commit: record
        the attempt, change no context."""

    def record_failure(
        self, source_id: str, *, error: str, commit_sha: str | None = None
    ) -> ContextSourceRecord:
        """Record a failed read/validation. The current snapshot pointer is
        never touched, so invalid context cannot replace valid context."""

    def get_snapshot(self, snapshot_id: str) -> ContextSnapshotRecord: ...

    def history(self, source_id: str) -> list[ContextSnapshotRecord]: ...


class GovernedContextRepository(Protocol):
    def propose_version(
        self,
        *,
        context_type: str,
        domain: str,
        name: str,
        title: str,
        definition: dict,
        created_by: str | None = None,
        review_interval_days: int | None = None,
        workspace: str = "default",
    ) -> GovernedContextVersionRecord:
        """Create (or reuse) the identity row for `(context_type, domain,
        name)` and append a candidate version. An approved pointer remains
        unchanged until a persisted human ReviewDecision replaces it."""

    def get(self, context_id: str, *, workspace: str = "default") -> GovernedContextRecord: ...

    def get_by_name(
        self, *, context_type: str, domain: str, name: str, workspace: str = "default"
    ) -> GovernedContextRecord: ...

    def get_version(
        self, context_id: str, version: int, *, workspace: str = "default"
    ) -> GovernedContextVersionRecord: ...

    def history(
        self, context_id: str, *, workspace: str = "default"
    ) -> list[GovernedContextVersionRecord]: ...

    def list_all(
        self,
        *,
        domain: str | None = None,
        context_type: str | None = None,
        workspace: str = "default",
    ) -> list[GovernedContextRecord]:
        """Every context at HEAD -- for policy scans, not search relevance."""

    def search(
        self,
        query: str,
        *,
        domain: str | None = None,
        context_type: str | None = None,
        workspace: str = "default",
    ) -> list[GovernedContextRecord]: ...


class ReviewRepository(Protocol):
    def create_task(
        self,
        *,
        reason: str,
        idempotency_key: str,
        workspace: str = "default",
        priority: int = 2,
        affected_asset_ids: list[str] | None = None,
        affected_context_id: str | None = None,
        proposal_payload: dict | None = None,
        processor_evidence: dict | None = None,
        evaluation_impact: dict | None = None,
    ) -> ReviewTaskRecord:
        """Get-or-create by `idempotency_key`: a second call with the same
        key returns the existing task rather than creating a duplicate."""

    def get_task(self, task_id: str, *, workspace: str = "default") -> ReviewTaskRecord: ...

    def list_tasks(
        self, *, status: str | None = None, workspace: str = "default"
    ) -> list[ReviewTaskRecord]: ...

    def set_proposal_payload(
        self, task_id: str, proposal_payload: dict, *, workspace: str = "default"
    ) -> ReviewTaskRecord:
        """Replace the assist draft on a task. Touches `proposal_payload` only --
        no approval, no status advance, no governed write (hy-murb)."""
        ...

    def set_assignee(
        self, task_id: str, assignee: str | None, *, workspace: str = "default"
    ) -> ReviewTaskRecord:
        """Set or clear the owner assigned to a task. Touches `assignee` only --
        metadata, not an approval, status advance, or governed write (hy-s8a6)."""
        ...

    def reserve_proposal(
        self, task_id: str, *, workspace: str = "default", expected_version: int, session=None
    ) -> ReviewTaskRecord: ...

    def assert_proposal_lease(
        self, task_id: str, *, workspace: str = "default", lease_id: str, session=None
    ) -> ReviewTaskRecord: ...

    def finish_proposal(
        self,
        task_id: str,
        proposal_payload: dict,
        *,
        workspace: str = "default",
        lease_id: str,
        session=None,
    ) -> ReviewTaskRecord: ...

    def release_proposal(
        self, task_id: str, *, workspace: str = "default", lease_id: str, session=None
    ) -> ReviewTaskRecord: ...

    def approve(
        self,
        task_id: str,
        *,
        decided_by: str,
        title: str,
        definition: dict,
        expected_version: int,
        edited: bool = False,
        notes: str | None = None,
        workspace: str = "default",
    ) -> ReviewApprovalResult:
        """Transactionally: append a GovernedContextVersion, record a
        ReviewDecision (`decision="edit"` if `edited` else `"approve"`),
        and resolve the task. Either both persist or neither does.

        `expected_version` must match the task's current `row_version` or this
        raises `hyperset.repositories.errors.OptimisticConcurrencyError`
        without applying any change -- two reviewers racing to decide the
        same task must not silently overwrite one another."""

    def reject(
        self,
        task_id: str,
        *,
        decided_by: str,
        expected_version: int,
        notes: str | None = None,
        workspace: str = "default",
    ) -> ReviewDecisionRecord: ...


class EvaluationRepository(Protocol):
    def create_case(
        self,
        *,
        name: str,
        question: str,
        expected: dict,
        domain: str | None = None,
        version: int = 1,
    ) -> EvaluationCaseRecord: ...

    def get_case(self, case_id: str) -> EvaluationCaseRecord: ...

    def list_cases(self, *, domain: str | None = None) -> list[EvaluationCaseRecord]: ...

    def record_run(
        self,
        *,
        case_id: str,
        attempt_payload: dict,
        scorecard: dict,
        passed: bool | None,
        context_versions_used: list | None = None,
        finished_at: datetime | None = None,
    ) -> EvaluationRunRecord: ...

    def list_runs(self, case_id: str) -> list[EvaluationRunRecord]: ...


class ProcessorRepository(Protocol):
    """hy-gh-38's offline processor: job claiming, findings, and the
    review-task deduplication a Finding feeds into
    (`hyperset.repositories.ReviewRepository.create_task`, unchanged --
    this protocol only owns ProcessorRun/Finding, never ReviewTask
    directly)."""

    def claim_run(
        self, *, trigger_type: str, trigger_ref: str | None = None, rule_versions: dict
    ) -> ProcessorRunRecord | None:
        """Begin a new run for `(trigger_type, trigger_ref)`. Returns
        `None` if a run for the same trigger is already `running` --
        enforced by a DB uniqueness constraint, not an in-process lock, so
        it holds across concurrent workers/processes."""

    def finish_run(
        self, run_id: str, *, counters: dict, warnings: list[str] | None = None
    ) -> ProcessorRunRecord: ...

    def fail_run(self, run_id: str, *, errors: list[str]) -> ProcessorRunRecord: ...

    def get_run(self, run_id: str) -> ProcessorRunRecord: ...

    def retry_run(self, run_id: str, *, rule_versions: dict) -> ProcessorRunRecord:
        """Start a new run for the same trigger as a prior (usually
        failed) run, with `retries` set to that trigger's total prior
        attempt count. Subject to the same active-trigger uniqueness
        constraint as `claim_run` -- raises if the prior run somehow isn't
        actually finished."""

    def record_finding(
        self,
        *,
        processor_run_id: str,
        finding_type: str,
        rule_version: int,
        severity: str,
        explanation: str,
        evidence: dict,
        affected_asset_id: str | None = None,
        affected_context_id: str | None = None,
        confidence: float | None = None,
        proposed_reviewer: str | None = None,
        proposed_action: dict | None = None,
        review_task_id: str | None = None,
    ) -> FindingRecord: ...

    def list_findings(
        self, *, processor_run_id: str | None = None, state: str | None = None
    ) -> list[FindingRecord]: ...

    def resolve_finding(self, finding_id: str, *, state: str = "resolved") -> FindingRecord: ...
