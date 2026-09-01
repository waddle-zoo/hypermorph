"""Plain-dataclass return shapes for `hyperset.repositories`.

Repository methods never return `hyperset.db.models` ORM instances: the
domain/service layer (MCP tools, API, connectors, evaluator) must not
import SQLAlchemy table definitions (hy-gh-26 "Repository boundaries"), so
every read crosses the boundary as one of these dataclasses instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ConnectionRecord:
    id: str
    connector_type: str
    display_name: str
    enabled: bool
    health_status: str
    health_checked_at: datetime | None
    health_detail: str | None
    config_ref: str | None
    created_at: datetime
    updated_at: datetime
    # The tenant/workspace this connection belongs to (hq-t6nx). Defaulted so a
    # hand-built legacy-shaped record stays valid.
    workspace_id: str = "default"


@dataclass
class SyncRunRecord:
    id: str
    connection_id: str
    mode: str
    transport: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    counters: dict
    checkpoint: dict | None
    warnings: list[str]
    errors: list[str]


@dataclass
class ObservedAssetVersionRecord:
    id: str
    asset_id: str
    sync_run_id: str
    version: int
    raw_payload: dict
    normalized: dict
    content_hash: str
    hash_basis: dict | None
    transport: str | None
    created_at: datetime


@dataclass
class ObservedAssetRecord:
    id: str
    connection_id: str
    external_id: str
    asset_type: str
    current_version: ObservedAssetVersionRecord | None
    first_seen_at: datetime
    last_seen_at: datetime
    source_modified_at: datetime | None
    deleted_at: datetime | None


@dataclass
class AssetRelationshipRecord:
    """One reference an observed asset declared to another, as persisted.

    `relation` is the connector's own word for the reference ("queries",
    "contains", "belongs_to", "derived_from", ...), carried through
    unchanged: sync resolves the target's identity, never the meaning."""

    id: str
    from_asset_id: str
    to_asset_id: str
    relation: str
    detail: dict | None


@dataclass
class IncomingReferenceRecord:
    """One live reference INTO an asset, carrying the referring asset's identity.

    Distinct from `AssetRelationshipRecord`, which names both endpoints by id
    only. A caller ranking on how many things reference a source has to say
    WHICH things, and an id is not checkable against evidence -- so the referring
    asset's own source-native fields travel with the row rather than costing one
    read each (hy-g1y8).

    `from_connection_id` rather than a connector name: the connector type lives
    on `connections`, and resolving it here would make this read join a table it
    otherwise does not need. Callers that want a ref already hold that map.
    """

    to_asset_id: str
    from_asset_id: str
    from_asset_type: str
    from_external_id: str
    from_connection_id: str
    relation: str


@dataclass
class ConnectorChangeRecord:
    id: str
    connection_id: str
    sync_run_id: str
    asset_id: str
    change_type: str
    from_version_id: str | None
    to_version_id: str | None
    detail: dict
    detected_at: datetime


@dataclass
class ContextSnapshotRecord:
    """One immutable Git context read. `files` is the original Git content;
    `normalized` is the deterministic runtime projection of it."""

    id: str
    source_id: str
    commit_sha: str
    committed_at: datetime | None
    domain: str
    title: str
    files: dict
    normalized: dict
    content_hash: str
    owner_refs: list
    evidence_refs: list
    # `{code, ref, message}` per declared ref that resolved to no observation,
    # recorded beside the links rather than instead of the snapshot (ADR 0017).
    evidence_findings: list
    synced_at: datetime


@dataclass
class ContextSourceRecord:
    id: str
    repository: str
    ref: str
    path: str
    display_name: str
    enabled: bool
    current_snapshot: ContextSnapshotRecord | None
    last_attempt_status: str
    last_attempt_at: datetime | None
    last_attempted_commit_sha: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    # The tenant/workspace this source belongs to (hq-t6nx), part of its identity.
    workspace_id: str = "default"


@dataclass
class ContextSourceCandidate:
    """A configured source's IDENTITY + current-snapshot version METADATA, deliberately
    WITHOUT the snapshot's `files` content (hy-r0szz).

    The ACL-aware search lists these to decide authorization per source BEFORE any content
    is read: a candidate carries only what the authorize check and hit provenance need
    (id/repository/domain/commit/content version + staleness), so a source the caller is
    denied never has its file bytes fetched from the database. Content is loaded separately,
    by id, only for sources that pass authorize. `current_snapshot_id` is None for a source
    with no current snapshot (nothing to search)."""

    id: str
    repository: str
    enabled: bool
    workspace_id: str
    current_snapshot_id: str | None
    domain: str | None
    commit_sha: str | None
    content_hash: str | None
    last_attempt_status: str
    last_attempt_at: datetime | None
    synced_at: datetime | None
    committed_at: datetime | None
    # The hierarchy parent is snapshot metadata, selected without loading snapshot files.
    parent: str | None = None


@dataclass
class GovernedContextVersionRecord:
    id: str
    context_id: str
    version: int
    title: str
    definition: dict
    reviewer: str | None
    approval_decision_id: str | None
    created_at: datetime
    created_by: str | None
    review_interval_days: int | None = None


@dataclass
class GovernedContextRecord:
    id: str
    context_type: str
    domain: str
    name: str
    lifecycle: str
    current_version: GovernedContextVersionRecord | None
    created_at: datetime
    updated_at: datetime
    workspace: str = "default"


@dataclass
class ReviewTaskRecord:
    id: str
    reason: str
    priority: int
    affected_asset_ids: list[str]
    affected_context_id: str | None
    proposal_payload: dict
    processor_evidence: dict
    evaluation_impact: dict | None
    assignee: str | None
    status: str
    idempotency_key: str
    row_version: int
    created_at: datetime
    updated_at: datetime
    workspace: str = "default"
    proposal_in_flight: bool = False
    proposal_lease_id: str | None = None
    proposal_lease_expires_at: datetime | None = None


@dataclass
class ReviewDecisionRecord:
    id: str
    review_task_id: str
    decision: str
    decided_by: str
    decided_at: datetime
    resulting_context_version_id: str | None
    notes: str | None


@dataclass
class ReviewApprovalResult:
    decision: ReviewDecisionRecord
    context_version: GovernedContextVersionRecord | None


@dataclass
class EvaluationCaseRecord:
    id: str
    name: str
    version: int
    question: str
    expected: dict
    domain: str | None
    created_at: datetime


@dataclass
class EvaluationRunRecord:
    id: str
    case_id: str
    started_at: datetime
    finished_at: datetime | None
    attempt_payload: dict
    scorecard: dict
    passed: bool | None
    context_versions_used: list = field(default_factory=list)
    created_at: datetime | None = None


@dataclass
class ProcessorRunRecord:
    id: str
    trigger_type: str
    trigger_ref: str | None
    rule_versions: dict
    started_at: datetime
    completed_at: datetime | None
    status: str
    counters: dict
    retries: int
    errors: list[str]
    warnings: list[str]


@dataclass
class FindingRecord:
    id: str
    finding_type: str
    rule_version: int
    processor_run_id: str
    affected_asset_id: str | None
    affected_context_snapshot_id: str | None
    affected_context_id: str | None
    severity: str
    confidence: float | None
    explanation: str
    evidence: dict
    proposed_reviewer: str | None
    proposed_action: dict
    state: str
    review_task_id: str | None
    created_at: datetime


@dataclass
class ResolveMissRecord:
    """One operational miss-log row (hy-jrpm). Not governed meaning."""

    id: str
    query: str
    directive: dict
    status: str
    warning_codes: list[str]
    bundle_id: str
    created_at: datetime


@dataclass
class InteractionTraceRecord:
    """One durable MCP interaction-trace row (hy-oqevj). Operational audit only,
    not governed meaning. The redacted `query`/`intent` and the opaque `hit_ids`
    are all this crosses the boundary with -- never the matched content."""

    id: str
    workspace: str
    principal_identity: str
    session_id: str | None
    turn_id: str | None
    tool_call_id: str | None
    correlation_id: str
    intent: str | None
    query: str | None
    tool_name: str
    search_mode: str | None
    filters: dict
    hit_ids: list[str]
    duration_ms: int | None
    source_staleness: dict
    miss: dict | None
    answer_bundle_id: str | None
    decision_ids: list[str]
    feedback_ids: list[str]
    status: str
    created_at: datetime


@dataclass
class AnswerFeedbackRecord:
    """One append-only decision on a traced hit/answer. Audit only."""

    id: str
    workspace: str
    principal_identity: str
    trace_id: str
    session_id: str
    correlation_id: str
    outcome: str
    bundle_id: str | None
    source_ref: str | None
    review_task_id: str | None
    notes: str | None
    created_at: datetime


@dataclass
class AnswerCitationRecord:
    """One durable citation<->answer link (hy-cpkvu). Operational audit only."""

    id: str
    workspace: str
    correlation_id: str
    bundle_id: str
    citation_ref: str
    citation_kind: str
    source_ref: str | None
    created_at: datetime


@dataclass
class CitationDecisionRecord:
    """One durable human decision on a citation (hy-cpkvu). Audit only, not
    governed meaning; the current decision is the one with `superseded_by is None`."""

    id: str
    workspace: str
    principal_identity: str
    decision: str
    citation_ref: str
    source_ref: str | None
    review_task_id: str | None
    correlation_id: str | None
    notes: str | None
    superseded_by: str | None
    created_at: datetime


@dataclass
class WritebackConfigRecord:
    """A configured proposal-only Git-PR write-back target (hy-8o8m, hq-1h1z). A
    target, never an authority: nothing here approves or merges."""

    repository: str
    base_ref: str
    manifest_path: str
    updated_at: datetime
    # The target's id, its routing key (null = the default/catch-all target),
    # whether it is the single default, and whether it is enabled (hq-1h1z).
    # Defaulted so a hand-built legacy-shaped record stays valid. The literal
    # matches `models.WRITEBACK_SINGLETON_ID`; the DTO layer does not import the
    # table module (hy-gh-26 boundary), so the value is inlined rather than shared.
    id: str = "writeback"
    routing_key: str | None = None
    is_default: bool = True
    enabled: bool = True
    test_result: str | None = None
    # The reviewer handle(s) a proposal routed to this target goes to (hq-1rq7),
    # comma-separated. Null/empty = the FAIL-CLOSED needs-routing default; no
    # secret, and no authority (authority stays a human Git merge, ADR 0012).
    reviewer_routing: str | None = None
    # The tenant/workspace this target belongs to (hq-t6nx). get_by_routing is
    # filtered by the proposing caller's workspace, so a proposal never crosses
    # tenants. Defaulted so a hand-built legacy-shaped record stays valid.
    workspace_id: str = "default"
    # The NAME of the server-side secret a URL target authenticates with, never
    # the token value (hy-eji4). Null for a local-path target.
    token_ref: str | None = None
    # The token-source mode (hy-up4k): 'env_ref' (the default when null) or
    # 'encrypted'. The ciphertext/nonce hold an AES-256-GCM encryption of the
    # token, decrypted only server-side with the KEK -- they are NOT the token
    # and are NEVER returned to a client.
    token_source: str | None = None
    token_ciphertext: bytes | None = None
    token_nonce: bytes | None = None
    # GitHub App write-back auth (hy-bdhg): the enterprise-default token_source
    # 'github_app'. `app_id` is not a secret; the App private key is encrypted at
    # rest (ciphertext/nonce) and NEVER returned. The minted installation token
    # is short-lived and never stored, so it has no field here.
    app_id: int | None = None
    app_key_ciphertext: bytes | None = None
    app_key_nonce: bytes | None = None

    @property
    def mode(self) -> str:
        """The effective token source; null legacy rows are env_ref (hy-eji4)."""
        return self.token_source or "env_ref"

    @property
    def token_set(self) -> bool:
        """Whether write-back auth is configured, without exposing any secret -- a
        NAME reference in env_ref mode, a stored token ciphertext in encrypted
        mode, or an App id plus a stored key ciphertext in github_app mode."""
        if self.mode == "encrypted":
            return self.token_ciphertext is not None
        if self.mode == "github_app":
            return self.app_id is not None and self.app_key_ciphertext is not None
        return bool(self.token_ref)


@dataclass
class AdminAuditRecord:
    """One append-only admin audit entry (hy-gh-75): actor / action / target / time /
    result. Carries no secret value -- `detail` is a short non-secret human string."""

    id: str
    at: datetime
    actor: str
    actor_issuer: str | None
    action: str
    target: str | None
    result: str
    detail: str | None
    workspace_id: str = "default"
    # The id of the request that performed the action (hy-w9ntg), or None for a row written
    # before correlation ids or off any request.
    correlation_id: str | None = None
