"""SQLAlchemy ORM models for the Postgres persistence layer (hy-gh-26).

Table groups follow the bead's "Required entities" list exactly:
Connection; SyncRun/ConnectorCheckpoint; ObservedAsset/ObservedAssetVersion
(+AssetRelationship); GovernedContext/GovernedContextVersion
(+ContextAssetLink); ReviewTask/ReviewDecision; EvaluationCase/EvaluationRun;
EvidenceRecord. hy-gh-43 adds the Git-owned pair ContextSource/
ContextSnapshot, which is a separate family from every observation table:
Git owns v0 domain meaning (ADR 0012), sources are observed evidence.

Design notes:

- IDs are app-generated prefixed strings (`conn-...`, `oa-...`, ...),
  matching the convention `hyperset.trust.builder.new_evidence_id` already
  uses (`ev-...`) rather than introducing a second ID scheme.
- `Connection` never has a plaintext secret column at all — only
  `config_encrypted` (an app-encrypted blob) and `config_ref` (a pointer
  into an external secret manager). There is nothing for "normal
  serialization" to accidentally expose (Required entities #1).
- `*_versions` tables are append-only: nothing in `hyperset.repositories`
  issues an UPDATE against a version row's content columns, only INSERT.
  The mutable "identity" tables (`observed_assets`, `governed_context`)
  hold a `current_version_id` pointer that repositories update in the same
  transaction as the new version insert.
- `search_vector` (`TSVECTOR`) columns back the tsvector full-text search
  the bead's §4 asks for; populated by repositories on write (via
  `sqlalchemy.func.to_tsvector`), not a generated column, so the exact
  fields fed into it stay visible in application code rather than buried
  in DDL.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hyperset.db.base import Base, new_id, utcnow

# --------------------------------------------------------------------------
# 1. Connection
# --------------------------------------------------------------------------

CONNECTION_HEALTH_STATUSES = ("unknown", "healthy", "unhealthy")


class Connection(Base):
    __tablename__ = "connections"
    __table_args__ = (
        CheckConstraint(
            f"health_status IN {CONNECTION_HEALTH_STATUSES!r}", name="valid_health_status"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("conn"))
    # The TENANT/WORKSPACE this connection belongs to (hq-t6nx, ADR-0037). Additive
    # and fail-closed: NOT NULL default 'default', so existing rows and a
    # single-tenant estate are the one implicit workspace. Admin reads/manage are
    # filtered by the caller's workspace, so no tenant sees another's connections.
    workspace_id: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    connector_type: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    # Never a plaintext config column -- see module docstring.
    config_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    config_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    health_status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    health_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# --------------------------------------------------------------------------
# 2. SyncRun / ConnectorCheckpoint
# --------------------------------------------------------------------------

SYNC_MODES = ("full", "incremental", "fixture_import")

# Which upstream contract a read used, and the word the rest of the code
# already uses for it: `ConnectorSnapshot.transport`, `SyncResult.transport`,
# `checkpoint["transport"]`. Enumerated because change detection compares
# within a transport (hy-6t4), so a value that differs only by case or by a
# stray space forks an asset's lineage silently -- the observation is then
# compared against nothing, appends a version, and reports first-sight
# forever, which looks exactly like the defect that rule exists to fix.
TRANSPORTS = ("rest", "graphql", "export_bundle")
SYNC_STATUSES = ("running", "succeeded", "failed")


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        CheckConstraint(f"mode IN {SYNC_MODES!r}", name="valid_mode"),
        CheckConstraint(f"status IN {SYNC_STATUSES!r}", name="valid_status"),
        CheckConstraint(
            f"transport IS NULL OR transport IN {TRANSPORTS!r}", name="valid_transport"
        ),
        Index("ix_sync_runs_connection_started", "connection_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("sync"))
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # The transport this run read with, which `mode` cannot say: `mode` is
    # "full" or "fixture_import" and is derived from the transport, so REST and
    # GraphQL are the same value there. A reader asking which lineage this
    # run's "unchanged" counts were measured against needs this (hy-6t4).
    transport: Mapped[str | None] = mapped_column(String)
    checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class ConnectorCheckpoint(Base):
    """The latest resumable checkpoint per connection -- distinct from
    `SyncRun.checkpoint`, which is the checkpoint *as of one past run*.
    Incremental syncs resume from here, not by scanning sync_runs."""

    __tablename__ = "connector_checkpoints"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("chk"))
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("connections.id"), nullable=False, unique=True
    )
    checkpoint: Mapped[dict] = mapped_column(JSONB, nullable=False)
    last_sync_run_id: Mapped[str | None] = mapped_column(ForeignKey("sync_runs.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# --------------------------------------------------------------------------
# 3. ObservedAsset / ObservedAssetVersion / AssetRelationship
# --------------------------------------------------------------------------


class ObservedAsset(Base):
    """Current identity row: one per (connection, external_id, asset_type).
    Points at the latest `ObservedAssetVersion`; the version rows carry the
    actual (immutable) content."""

    __tablename__ = "observed_assets"
    __table_args__ = (
        UniqueConstraint("connection_id", "external_id", "asset_type", name="uq_asset_identity"),
        Index("ix_observed_assets_type", "asset_type"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("oa"))
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    asset_type: Mapped[str] = mapped_column(String, nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("observed_asset_versions.id", use_alter=True, name="fk_oa_current_version")
    )
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    versions: Mapped[list[ObservedAssetVersion]] = relationship(
        back_populates="asset",
        foreign_keys="ObservedAssetVersion.asset_id",
        order_by="ObservedAssetVersion.version",
    )


class ObservedAssetVersion(Base):
    """Immutable: one row per real change, never updated after insert."""

    __tablename__ = "observed_asset_versions"
    __table_args__ = (
        UniqueConstraint("asset_id", "version", name="uq_asset_version"),
        CheckConstraint(
            f"transport IS NULL OR transport IN {TRANSPORTS!r}", name="valid_version_transport"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("oav"))
    asset_id: Mapped[str] = mapped_column(ForeignKey("observed_assets.id"), nullable=False)
    sync_run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    normalized: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    # Which projection of `raw_payload` `content_hash` covers, as a rule this
    # row can be replayed against (`hyperset.repositories.hash_basis`). `{}`
    # means the hash covers the payload whole; NULL means the row predates
    # this column and its basis was never recorded (hy-y8g finding 2).
    hash_basis: Mapped[dict | None] = mapped_column(JSONB)
    # Which upstream contract observed this version. Change detection compares
    # an observation against the most recent version from the SAME transport
    # (hy-6t4), because two transports of one source carry different amounts of
    # the server's own bookkeeping and hash differently for an asset nobody
    # edited. NULL means the row predates this column, the convention
    # `hash_basis` already uses.
    transport: Mapped[str | None] = mapped_column(String)
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    asset: Mapped[ObservedAsset] = relationship(back_populates="versions", foreign_keys=[asset_id])


Index(
    "ix_observed_asset_versions_search",
    ObservedAssetVersion.search_vector,
    postgresql_using="gin",
)


class AssetRelationship(Base):
    """One reference the source itself declared between two observed assets,
    resolved to internal ids: a chart queries a dataset, a dashboard contains
    a chart, a dataset belongs to a database, a DataHub dataset is derived
    from an upstream one.

    A projection of the latest observation of the *source* asset, not an
    immutable version chain: `ObservedAssetRepository.replace_relationships`
    rewrites one asset's outgoing rows to exactly the set that asset's newest
    payload declared, so a reference the source dropped stops being claimed
    here (hy-d7xh). Row identity is stable across a resync that declares the
    same set, so nothing downstream sees churn from re-observation alone.

    Rows survive their endpoints being soft-deleted, because the reference was
    really observed while both assets existed, so presence here never means
    "still live" on its own -- `list_relationships(include_deleted=False)`
    joins `observed_assets.deleted_at` for the consumer that wants that
    (hy-z21y).

    Nothing derived is stored: no counts, no ranking, no display names. What
    counting these rows may be used for belongs to the reader (hy-gh-124)."""

    __tablename__ = "asset_relationships"
    __table_args__ = (
        # The projection holds one row per declared reference, so re-observing
        # an unchanged source cannot append a duplicate -- and a count over
        # these rows means "references declared", not "times synced".
        UniqueConstraint("from_asset_id", "to_asset_id", "relation", name="uq_asset_relationship"),
        # The one access path a reference count has: everything pointing at
        # one asset, optionally narrowed to one kind of reference.
        Index("ix_asset_relationships_to_relation", "to_asset_id", "relation"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("arel"))
    from_asset_id: Mapped[str] = mapped_column(ForeignKey("observed_assets.id"), nullable=False)
    to_asset_id: Mapped[str] = mapped_column(ForeignKey("observed_assets.id"), nullable=False)
    relation: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB)


CONNECTOR_CHANGE_TYPES = ("created", "updated", "deleted", "restored")


class ConnectorChange(Base):
    """What one sync run observed happening to one asset (hy-gh-27): the
    connector-owned change event the processor reads later, instead of
    re-diffing version history itself.

    Written in the same transaction as the `ObservedAssetVersion` it
    describes, so "one real source change produces one immutable version
    and one connector change" cannot come apart. `uq_connector_change_version`
    makes the second half of that a database invariant: a version can be
    announced downstream at most once, no matter how the sync was driven.

    A `deleted` change means the asset was absent from a non-partial
    snapshot (soft delete); `to_version_id` is NULL because absence
    produces no new observed content, and NULL is exempt from the unique
    constraint -- an asset really can be deleted, reappear, and be deleted
    again.

    A `restored` change means a soft-deleted asset was observed again
    (hy-y8g.1). It is emitted even when the content is byte-identical to
    the version that was deleted, because otherwise the stream alone says
    the asset is still gone and a consumer would have to join
    `observed_assets.deleted_at` to learn otherwise. `to_version_id` is
    NULL exactly when the reappearance produced no new version."""

    __tablename__ = "connector_changes"
    __table_args__ = (
        CheckConstraint(f"change_type IN {CONNECTOR_CHANGE_TYPES!r}", name="valid_change_type"),
        UniqueConstraint("to_version_id", name="uq_connector_change_version"),
        Index("ix_connector_changes_connection_detected", "connection_id", "detected_at"),
        # The repository's only two reads: one run's changes, one asset's
        # history -- both ordered by `detected_at` (hy-y8g finding 5).
        Index("ix_connector_changes_run_detected", "sync_run_id", "detected_at"),
        Index("ix_connector_changes_asset_detected", "asset_id", "detected_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("cc"))
    connection_id: Mapped[str] = mapped_column(ForeignKey("connections.id"), nullable=False)
    sync_run_id: Mapped[str] = mapped_column(ForeignKey("sync_runs.id"), nullable=False)
    asset_id: Mapped[str] = mapped_column(ForeignKey("observed_assets.id"), nullable=False)
    change_type: Mapped[str] = mapped_column(String, nullable=False)
    from_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("observed_asset_versions.id", name="fk_cc_from_version")
    )
    to_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("observed_asset_versions.id", name="fk_cc_to_version")
    )
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# 3b. ContextSource / ContextSnapshot (hy-gh-43, ADR 0012)
# --------------------------------------------------------------------------

CONTEXT_SYNC_STATUSES = ("never_synced", "synced", "unchanged", "failed")


class ContextSource(Base):
    """One configured Git repository/ref/path: the authoritative source of
    v0 domain meaning (ADR 0012). Mutable operational state only -- which
    commit is current and how the last read went. Nothing here is business
    meaning, and no column can be edited into a new authoritative version;
    only a Git commit produces one.

    Deliberately not a `Connection`: a connection is a source of
    observations, and Git context is not an observation. Sharing the table
    would make `connections`/`sync_runs` read as if Git context were
    connected metadata."""

    __tablename__ = "context_sources"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "repository", "ref", "path", name="uq_context_source_identity"
        ),
        CheckConstraint(
            f"last_attempt_status IN {CONTEXT_SYNC_STATUSES!r}", name="valid_last_attempt_status"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ctxsrc"))
    # The TENANT/WORKSPACE this Git context source belongs to (hq-t6nx, ADR-0037).
    # Part of the source IDENTITY, so two tenants may track the same repo/ref/path.
    # Additive: NOT NULL default 'default' backfills existing rows.
    workspace_id: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    repository: Mapped[str] = mapped_column(String, nullable=False)
    ref: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The Git checkpoint that survives restart: which immutable snapshot is
    # current, and what the last read attempt saw.
    current_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("context_snapshots.id", use_alter=True, name="fk_ctxsrc_current_snapshot")
    )
    last_attempt_status: Mapped[str] = mapped_column(String, nullable=False, default="never_synced")
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempted_commit_sha: Mapped[str | None] = mapped_column(String)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ContextSnapshot(Base):
    """One immutable read of the configured context at an exact commit.

    Identity is (source, commit SHA), so re-reading an unchanged ref is a
    no-op and a new commit always produces a new row -- prior snapshots stay
    replayable. `files` preserves the original Git content; `normalized` is
    the deterministic runtime projection, and every field in it remains
    traceable to this commit. `evidence_refs` records which observed asset
    each declared ref resolved to at sync time, so a link is evidence rather
    than a name match; `evidence_findings` records the declared refs that
    resolved to nothing, because a missing observation is a fact about the
    connected systems and never a reason to withhold what Git says
    (ADR 0017)."""

    __tablename__ = "context_snapshots"
    __table_args__ = (
        UniqueConstraint("source_id", "commit_sha", name="uq_context_snapshot_commit"),
        Index("ix_context_snapshots_source_synced", "source_id", "synced_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ctxsnap"))
    source_id: Mapped[str] = mapped_column(ForeignKey("context_sources.id"), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String, nullable=False)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    domain: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False, default="")
    files: Mapped[dict] = mapped_column(JSONB, nullable=False)
    normalized: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    owner_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    evidence_findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# 4. GovernedContext / GovernedContextVersion / ContextAssetLink
# --------------------------------------------------------------------------

GOVERNED_CONTEXT_LIFECYCLES = ("candidate", "in_review", "approved", "deprecated")


class GovernedContext(Base):
    """Current identity row, keyed by (workspace, context_type, domain, name)."""

    __tablename__ = "governed_context"
    __table_args__ = (
        UniqueConstraint(
            "workspace", "context_type", "domain", "name", name="uq_governed_context_identity"
        ),
        CheckConstraint(f"lifecycle IN {GOVERNED_CONTEXT_LIFECYCLES!r}", name="valid_lifecycle"),
        Index("ix_governed_context_domain_type", "domain", "context_type"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("gc"))
    workspace: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    context_type: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("governed_context_versions.id", use_alter=True, name="fk_gc_current_version")
    )
    lifecycle: Mapped[str] = mapped_column(String, nullable=False, default="candidate")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    versions: Mapped[list[GovernedContextVersion]] = relationship(
        back_populates="context",
        foreign_keys="GovernedContextVersion.context_id",
        order_by="GovernedContextVersion.version",
    )


class GovernedContextVersion(Base):
    """Immutable. `definition` holds the whole proposed definition as one
    document -- a free-form dict at this layer, whatever `propose_version`'s
    caller passes; the other JSONB columns duplicate the subset of it named
    here (approved_assets/joins/filters/dimensions/warnings/freshness_policy/
    validation_guidance/conflicts/deprecations) so they're independently
    queryable without unpacking `definition` -- `definition` remains the
    single round-trippable source, these are a denormalized read
    convenience, not a second authority. Which `definition` key feeds which
    column, and which two columns are placeholders for an unbuilt writer, is
    in `repositories/postgres/governed_context.py::_version_columns`."""

    __tablename__ = "governed_context_versions"
    __table_args__ = (UniqueConstraint("context_id", "version", name="uq_context_version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("gcv"))
    context_id: Mapped[str] = mapped_column(ForeignKey("governed_context.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False, default="")
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    approved_assets: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    joins: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    filters: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    dimensions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    freshness_policy: Mapped[dict | None] = mapped_column(JSONB)
    validation_guidance: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    conflicts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    deprecations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    reviewer: Mapped[str | None] = mapped_column(String)
    approval_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_decisions.id", use_alter=True, name="fk_gcv_approval_decision")
    )
    review_interval_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String)
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR)

    context: Mapped[GovernedContext] = relationship(
        back_populates="versions", foreign_keys=[context_id]
    )


Index(
    "ix_governed_context_versions_search",
    GovernedContextVersion.search_vector,
    postgresql_using="gin",
)


class ContextAssetLink(Base):
    __tablename__ = "context_asset_links"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("cal"))
    context_version_id: Mapped[str] = mapped_column(
        ForeignKey("governed_context_versions.id"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(ForeignKey("observed_assets.id"), nullable=False)
    relation: Mapped[str] = mapped_column(String, nullable=False)


# --------------------------------------------------------------------------
# 5. ReviewTask / ReviewDecision
# --------------------------------------------------------------------------

REVIEW_TASK_STATUSES = ("open", "in_progress", "resolved", "dismissed")
REVIEW_DECISIONS = ("approve", "edit", "reject")


class ReviewTask(Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        CheckConstraint(f"status IN {REVIEW_TASK_STATUSES!r}", name="valid_status"),
        UniqueConstraint("workspace", "idempotency_key", name="uq_review_task_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rt"))
    # Review tasks are tenant-owned audit/workflow rows. The id itself is opaque and
    # random, but every lookup and idempotency check still carries the verified workspace
    # so a caller cannot use a task id from another tenant to read or mutate it.
    workspace: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    affected_asset_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    affected_context_id: Mapped[str | None] = mapped_column(ForeignKey("governed_context.id"))
    proposal_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    processor_evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    evaluation_impact: Mapped[dict | None] = mapped_column(JSONB)
    assignee: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    # Optimistic concurrency for human edits (hy-gh-26 required constraint):
    # bumped on every mutation; `approve`/`reject` reject a stale
    # `expected_version` with OptimisticConcurrencyError instead of
    # silently overwriting a concurrent reviewer's decision.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # A proposal writer has a remote side effect. Reserve the task in a short local
    # transaction before invoking it, and make approve/reject/edit/assign refuse while
    # the reservation is held. This closes the status/version TOCTOU window without
    # inventing a new public task status.
    proposal_in_flight: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # A crashed writer cannot clear `proposal_in_flight` on its way out. The lease makes the
    # reservation reclaimable after a bounded interval, and the opaque id prevents that stale
    # writer from clearing or overwriting a newer attempt after reclamation.
    proposal_lease_id: Mapped[str | None] = mapped_column(String)
    proposal_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (CheckConstraint(f"decision IN {REVIEW_DECISIONS!r}", name="valid_decision"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("rd"))
    review_task_id: Mapped[str] = mapped_column(ForeignKey("review_tasks.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    decided_by: Mapped[str] = mapped_column(String, nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resulting_context_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("governed_context_versions.id")
    )
    notes: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------
# 6. EvaluationCase / EvaluationRun
# --------------------------------------------------------------------------


class EvaluationCase(Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_evaluation_case_version"),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ec"))
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    expected: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    domain: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("er"))
    case_id: Mapped[str] = mapped_column(ForeignKey("evaluation_cases.id"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    scorecard: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    passed: Mapped[bool | None] = mapped_column(Boolean)
    context_versions_used: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------------------------
# 7. EvidenceRecord
# --------------------------------------------------------------------------


class EvidenceRecordRow(Base):
    """Reserved provenance storage; public shape waits for ContextBundle."""

    __tablename__ = "evidence_records"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    source_tier: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    context_version: Mapped[str] = mapped_column(String, nullable=False)
    freshness_status: Mapped[str] = mapped_column(String, nullable=False, default="unknown")
    max_data_date: Mapped[str | None] = mapped_column(String)
    sql_ref: Mapped[str | None] = mapped_column(String)
    result_ref: Mapped[str | None] = mapped_column(String)
    skill_version: Mapped[str | None] = mapped_column(String)
    analysis_ref: Mapped[str | None] = mapped_column(String)
    trace_ref: Mapped[str | None] = mapped_column(String)
    created_by: Mapped[str | None] = mapped_column(String)
    metrics_used: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    datasets_used: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    validations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    business_context: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    assumptions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    unresolved_ambiguity: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    review_state: Mapped[str] = mapped_column(String, nullable=False, default="unreviewed")
    fallback: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String, nullable=False, default="complete")
    missing_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


# --------------------------------------------------------------------------
# 8. ProcessorRun / Finding (hy-gh-38)
# --------------------------------------------------------------------------

PROCESSOR_TRIGGER_TYPES = ("sync", "freshness", "evaluation", "manual")
PROCESSOR_RUN_STATUSES = ("running", "succeeded", "failed")


class ProcessorRun(Base):
    """One offline-processor pass. `trigger_ref` is the id of whatever
    triggered it (a `SyncRun.id` for trigger_type="sync", None for a
    freshness scan). `rule_versions` pins exactly which rule version ran
    each Finding this run produced, so a later rule change doesn't
    retroactively change what an old run is understood to have checked."""

    __tablename__ = "processor_runs"
    __table_args__ = (
        CheckConstraint(f"trigger_type IN {PROCESSOR_TRIGGER_TYPES!r}", name="valid_trigger_type"),
        CheckConstraint(f"status IN {PROCESSOR_RUN_STATUSES!r}", name="valid_status"),
        # Transactional claiming (hy-gh-38): a plain DB-enforced uniqueness
        # constraint, not an advisory lock -- Postgres itself rejects a
        # second "running" row for the same trigger, so "concurrent
        # workers cannot process the same job simultaneously" holds
        # regardless of process/session boundaries. COALESCE(trigger_ref,
        # '') because Postgres treats NULL <> NULL in uniqueness checks by
        # default, which would otherwise let two freshness scans
        # (trigger_ref IS NULL) run "running" at once.
        Index(
            "uq_processor_runs_active_trigger",
            "trigger_type",
            text("COALESCE(trigger_ref, '')"),
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pr"))
    trigger_type: Mapped[str] = mapped_column(String, nullable=False)
    trigger_ref: Mapped[str | None] = mapped_column(String)
    rule_versions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    counters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


FINDING_SEVERITIES = ("info", "warning", "error", "critical")
FINDING_STATES = ("current", "superseded", "resolved")


class Finding(Base):
    """One deterministic-rule finding. `state` moves current -> resolved
    when a human decision addresses it (or the underlying condition stops
    reproducing on a rerun), or current -> superseded when a newer Finding
    from a later run replaces it for the same rule+subject. Findings for
    the same concept/resolution may share one `ReviewTask`
    (`review_task_id`); unrelated findings never do."""

    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(f"severity IN {FINDING_SEVERITIES!r}", name="valid_severity"),
        CheckConstraint(f"state IN {FINDING_STATES!r}", name="valid_state"),
        # Deduplication as a database invariant, not a convention two workers
        # have to agree on: one rule can hold at most one *current* finding
        # about one asset under one Git commit. Reruns and concurrent runs
        # therefore cannot fan the same disagreement out into a queue of
        # identical review items.
        Index(
            "uq_findings_current_subject",
            "finding_type",
            "affected_asset_id",
            "affected_context_snapshot_id",
            unique=True,
            postgresql_where=text("state = 'current'"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("fnd"))
    finding_type: Mapped[str] = mapped_column(String, nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    processor_run_id: Mapped[str] = mapped_column(ForeignKey("processor_runs.id"), nullable=False)
    affected_asset_id: Mapped[str | None] = mapped_column(ForeignKey("observed_assets.id"))
    # The exact Git commit the finding was judged against (ADR 0012). The
    # older `affected_context_id` points at `governed_context`, which the
    # pivot left as compatibility persistence rather than v0 authority -- a
    # v0 finding about business meaning has to pin the commit, not a
    # Hyperset-local row.
    affected_context_snapshot_id: Mapped[str | None] = mapped_column(
        ForeignKey("context_snapshots.id")
    )
    affected_context_id: Mapped[str | None] = mapped_column(ForeignKey("governed_context.id"))
    severity: Mapped[str] = mapped_column(String, nullable=False, default="warning")
    confidence: Mapped[float | None] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    proposed_reviewer: Mapped[str | None] = mapped_column(String)
    proposed_action: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(String, nullable=False, default="current")
    review_task_id: Mapped[str | None] = mapped_column(ForeignKey("review_tasks.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# hy-jrpm: the durable miss-log. An OPERATIONAL-ONLY record of a resolve
# outcome worth revisiting -- no_match, observed_only, or any warning the served
# bundle carried. Written at the transport boundary (transport/operations.py),
# never by the pure resolver, so the resolver stays deterministic and
# side-effect-free. It is NOT a system of record for meaning (ADR 0012, ADR 0020
# decision 5): nothing here is governed context and nothing reads it to decide
# authority. The flywheel consumers (hy-ghwo, hy-jg2v) read it only to PROPOSE,
# as assist, a definition a human still has to approve in Git.
#
# The four statuses a served bundle can carry. Mirrors
# `hyperset.bundle.schema.RESOLUTION_STATUSES`; the db layer does not import the
# bundle layer, so the two are bound by a test rather than a shared symbol.
RESOLUTION_STATUSES = ("governed", "mixed", "observed_only", "no_match")


class ResolveMiss(Base):
    """One logged resolve outcome worth revisiting. Operational only."""

    __tablename__ = "resolve_miss"
    __table_args__ = (
        CheckConstraint(f"status IN {RESOLUTION_STATUSES!r}", name="valid_status"),
        Index("ix_resolve_miss_status_created", "status", "created_at"),
        Index("ix_resolve_miss_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("miss"))
    # What was asked and what the caller named to retrieve it. Operational
    # input, not governed meaning: the log records the question so the flywheel
    # can propose where governance was silent, never to store it as authority.
    query: Mapped[str] = mapped_column(Text, nullable=False)
    directive: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # The resolution status and the warning codes the served bundle carried.
    status: Mapped[str] = mapped_column(String, nullable=False)
    warning_codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # The deterministic id of the bundle this outcome served, so a miss ties
    # back to the exact answer without storing the answer.
    bundle_id: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# The three outcomes one traced MCP tool call can record (hy-oqevj). `hit` and
# `miss` are the search/resolve answer having found governed context or not;
# `denied` is the authz gate refusing the call before it ran. A denied row
# carries the CORRELATION and the status, never the protected content it was
# denied -- see McpInteractionTrace.
INTERACTION_TRACE_STATUSES = ("hit", "miss", "denied")


class McpInteractionTrace(Base):
    """One durable row per traced MCP tool call, so a local Claude/MCP session
    can be RECONSTRUCTED (hy-oqevj, epic hy-01442 slice 2). Operational audit
    only -- like ResolveMiss, it stores what was asked and what came back, never
    governed meaning and never authority (ADR 0012).

    The chain is reassembled from three linkage ids: `session_id` groups a whole
    session, `turn_id` a turn within it, and `correlation_id` ties one turn's
    search to the resolve/answer that follows. `principal_identity` and
    `workspace` are SERVER-DERIVED at the transport boundary (never caller
    free-text), the same discipline as set_review_assignee.

    NON-DISCLOSING BY CONSTRUCTION: `query`/`intent` are REDACTED before they
    land here, `hit_ids` are opaque location ids (source:path:line), never the
    matched bytes, and a `denied` row stores the status + correlation only --
    the protected content it named is never read into this table.
    """

    __tablename__ = "mcp_interaction_trace"
    __table_args__ = (
        CheckConstraint(
            f"status IN {INTERACTION_TRACE_STATUSES!r}", name="valid_interaction_status"
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="nonnegative_interaction_duration"
        ),
        # Reassemble one session's turns in order, and tie a search to its resolve.
        Index("ix_mcp_trace_session_created", "session_id", "created_at"),
        Index("ix_mcp_trace_correlation", "correlation_id"),
        Index("ix_mcp_trace_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("trace"))
    # Server-derived identity (hy-oqevj): the caller's tenant and their opaque
    # `subject@issuer` (or 'anonymous' when the authz gate is off). Never a
    # caller-supplied field.
    workspace: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    principal_identity: Mapped[str] = mapped_column(String, nullable=False)
    # Linkage ids, opaque trace tokens carried as transport metadata (not tool
    # arguments, so no served input schema moves). Nullable: a stdio/direct call
    # carries none, and a row with only a correlation id is still a valid trace.
    session_id: Mapped[str | None] = mapped_column(String)
    turn_id: Mapped[str | None] = mapped_column(String)
    tool_call_id: Mapped[str | None] = mapped_column(String)
    # Always present: minted server-side when the caller supplies none, so every
    # traced call is correlatable even if it links to nothing before it.
    correlation_id: Mapped[str] = mapped_column(String, nullable=False)
    # The caller's declared intent and query -- REDACTED before persistence.
    intent: Mapped[str | None] = mapped_column(Text)
    query: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(String, nullable=False)
    search_mode: Mapped[str | None] = mapped_column(String)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Opaque ids of what the call returned (source:path:line, or a bundle id),
    # never the matched content. Empty for a miss or a denial.
    hit_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Complete audit envelope (hy-8f2r4): elapsed boundary time, the narrow
    # staleness metadata actually served, and an explicit account of what a
    # miss searched. Existing rows predate these fields, so duration is
    # nullable; every new traced call supplies it.
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    source_staleness: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    miss: Mapped[dict | None] = mapped_column(JSONB)
    # Explicit answer/decision/feedback linkage. The lists contain opaque ids
    # only; the feedback repository appends its id in the same transaction as
    # the feedback row.
    answer_bundle_id: Mapped[str | None] = mapped_column(String)
    decision_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    feedback_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


ANSWER_FEEDBACK_OUTCOMES = (
    "accept",
    "reject",
    "include",
    "ignore",
    "correct",
    "needs_review",
)


class AnswerFeedback(Base):
    """One append-only decision on a traced hit or answer (hy-8f2r4).

    Operational audit only: it confers no authority and advances no review
    state. `workspace` and `principal_identity` are server-derived; the trace
    link is verified before insert; refs and notes are redacted before they
    reach this table.
    """

    __tablename__ = "answer_feedback"
    __table_args__ = (
        CheckConstraint(
            f"outcome IN {ANSWER_FEEDBACK_OUTCOMES!r}", name="valid_answer_feedback_outcome"
        ),
        Index("ix_answer_feedback_session", "workspace", "session_id", "created_at"),
        Index("ix_answer_feedback_correlation", "workspace", "correlation_id"),
        Index("ix_answer_feedback_source", "workspace", "source_ref"),
        Index("ix_answer_feedback_review_task", "workspace", "review_task_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("afb"))
    workspace: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    principal_identity: Mapped[str] = mapped_column(String, nullable=False)
    trace_id: Mapped[str] = mapped_column(ForeignKey("mcp_interaction_trace.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    bundle_id: Mapped[str | None] = mapped_column(String)
    source_ref: Mapped[str | None] = mapped_column(String)
    review_task_id: Mapped[str | None] = mapped_column(ForeignKey("review_tasks.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# The two kinds of citation a governed answer draws on (hy-cpkvu). A `provenance`
# citation is a `provenance_refs` entry (the Git-owned context that authorized the
# answer); an `approved_source` citation is an `instructions.approved_sources` ref
# (the dataset the answer names). Both are ALREADY served on the ContextBundle -- the
# internal store only MIRRORS them keyed by correlation_id, adding no served key.
CITATION_KINDS = ("provenance", "approved_source")


class AnswerCitation(Base):
    """One durable citation<->answer link (hy-cpkvu, epic hy-01442 slice 3).

    When a governed answer is produced, record WHICH citations supplied it, keyed
    by the #503 interaction-trace `correlation_id` and the answer's `bundle_id`, so
    for any answer you can enumerate its exact citations and for any citation/source
    you can find the answers it supported. Operational audit only (ADR 0012): it
    mirrors fields the ContextBundle already serves, decides no authority, and adds
    no key to any served response.
    """

    __tablename__ = "answer_citations"
    __table_args__ = (
        CheckConstraint(f"citation_kind IN {CITATION_KINDS!r}", name="valid_citation_kind"),
        # Idempotent: re-recording the same answer's same citation is a no-op.
        UniqueConstraint(
            "workspace",
            "bundle_id",
            "correlation_id",
            "citation_ref",
            "citation_kind",
            name="uq_answer_citation",
        ),
        Index("ix_answer_citation_bundle", "bundle_id"),
        Index("ix_answer_citation_correlation", "correlation_id"),
        Index("ix_answer_citation_ref", "citation_ref"),
        Index("ix_answer_citation_source", "source_ref"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("acite"))
    workspace: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    # Ties this citation to the #503 interaction trace and to the served answer.
    correlation_id: Mapped[str] = mapped_column(String, nullable=False)
    bundle_id: Mapped[str] = mapped_column(String, nullable=False)
    # The citation itself: a provenance_ref or an approved_source ref, plus the
    # asset/source ref when one is known. Opaque ids only -- never a snippet.
    citation_ref: Mapped[str] = mapped_column(String, nullable=False)
    citation_kind: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# The four decisions a human can record on a citation (hy-cpkvu). include/exclude
# select a citation for an answer; approve/reject rule on a proposed one. A decision
# is task METADATA + audit -- it approves, merges, or writes no governed row (ADR 0012).
CITATION_DECISIONS = ("include", "exclude", "approve", "reject")


class CitationDecision(Base):
    """One durable human decision on a citation (hy-cpkvu, epic hy-01442 slice 3).

    Records not just what was proposed but what a human decided: an include/exclude/
    approve/reject on a citation or hit, linked to the SERVER-DERIVED principal, the
    citation/source, the #502 review task, and the #503 correlation_id. Operational
    audit only (ADR 0012): it advances no governed status and confers no authority.

    Idempotent by SUPERSEDE (latest-wins): re-submitting a decision for the same
    (workspace, review_task, citation, principal) marks the prior LIVE row superseded
    and inserts a new one. The CURRENT decision is the single row with
    `superseded_by IS NULL`, enforced by a partial unique index so two live decisions
    for one item can never coexist.
    """

    __tablename__ = "citation_decisions"
    __table_args__ = (
        CheckConstraint(f"decision IN {CITATION_DECISIONS!r}", name="valid_citation_decision"),
        # Exactly one LIVE decision per (workspace, review_task, citation, principal):
        # a partial unique index over the not-yet-superseded rows. review_task_id is
        # NULLABLE (a bare-citation decision has no task), and Postgres treats NULLs as
        # DISTINCT -- so a plain column index would let bare-citation decisions have MANY
        # live rows (hy-cpkvu blocker 2). COALESCE-normalize the nullable key to a fixed
        # empty sentinel so two live bare-citation decisions for one item collide.
        Index(
            "uq_citation_decision_live",
            "workspace",
            text("coalesce(review_task_id, '')"),
            "citation_ref",
            "principal_identity",
            unique=True,
            postgresql_where=text("superseded_by IS NULL"),
        ),
        Index("ix_citation_decision_task", "review_task_id"),
        Index("ix_citation_decision_ref", "citation_ref"),
        Index("ix_citation_decision_correlation", "correlation_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("cdec"))
    workspace: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    # Server-derived identity of the deciding human (subject@issuer), never caller text.
    principal_identity: Mapped[str] = mapped_column(String, nullable=False)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    # What the decision is ABOUT: the citation ref (+ asset/source ref), the #502 review
    # task it belongs to, and the #503 answer correlation. review_task_id/correlation are
    # nullable so a decision on a bare citation (no task/answer yet) is still recordable.
    citation_ref: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String)
    review_task_id: Mapped[str | None] = mapped_column(ForeignKey("review_tasks.id"))
    correlation_id: Mapped[str | None] = mapped_column(String)
    # Redacted free text; never a snippet or secret.
    notes: Mapped[str | None] = mapped_column(Text)
    # Latest-wins supersede: the id of the decision that replaced this one, or NULL if
    # this is the current decision.
    superseded_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# The id of the ONE backward-compatible default write-back target. Before
# hq-1h1z this was the only row (a PK singleton); the phase-2 multi-target
# migration keeps this exact row as the `is_default` target so a single-target
# estate keeps routing exactly as before.
WRITEBACK_SINGLETON_ID = "writeback"


class WritebackConfig(Base):
    """A proposal-only Git-PR write-back TARGET (hy-8o8m, hq-1h1z).

    One config row PER write-back target, like `context_sources` is a config
    row: the TARGET repository (a URL or a LOCAL PATH), the base ref, and the
    manifest path the writer merges the draft into. Phase 2 (hq-1h1z) makes this
    a set of targets rather than one row -- a `routing_key` selects which target
    a proposal for a given domain routes to, `is_default` marks the single
    catch-all target a null-key lookup falls back to, and `enabled` soft-disables
    a target without deleting it. It configures a target and nothing else -- it
    confers no authority, and no field here approves, merges, or advances
    governed context (ADR 0012).
    """

    __tablename__ = "context_writeback_config"
    __table_args__ = (
        # Routing keys are unique PER WORKSPACE (hq-t6nx, ADR-0037), so two tenants
        # may each key a target to the same domain. Postgres allows many NULLs, so
        # a workspace's null-key default target is not constrained here.
        UniqueConstraint(
            "workspace_id", "routing_key", name="uq_context_writeback_config_routing_key"
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("wbtgt"))
    # The TENANT/WORKSPACE this write-back target belongs to (hq-t6nx, ADR-0037).
    # get_by_routing is filtered by the proposing caller's workspace, so a proposal
    # never routes across tenants. Additive: NOT NULL default 'default'.
    workspace_id: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    # The routing key a proposal's DOMAIN is matched against to pick this target
    # (hq-1h1z). Null on the default/catch-all target; unique among keyed targets
    # WITHIN a workspace so a domain routes to exactly one target and never fans
    # out. Postgres allows many NULLs under the unique index, so multiple estates
    # keep a null default.
    routing_key: Mapped[str | None] = mapped_column(String, nullable=True)
    # The single catch-all target a null-key or unmatched lookup falls back to.
    # Exactly one row carries True (the migrated legacy singleton), enforced by
    # the repository; fail-closed if no keyed match and no default exists.
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Soft-disable: a disabled target is skipped by routing (fail-closed) without
    # losing its stored config/secret refs, so an operator can pause a target.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # The last non-secret connectivity/test result for this target (a short human
    # string), or null if never tested. Carries no secret value.
    test_result: Mapped[str | None] = mapped_column(String, nullable=True)
    # The reviewer handle(s) a proposal routed to THIS target should go to
    # (hq-1rq7), a comma-separated list. Null/empty is the honest FAIL-CLOSED
    # default: a proposal to a target with no reviewer routing is recorded as an
    # explicit needs-routing state, never silently dropped or auto-approved. It
    # confers no authority -- authority stays a human Git merge (ADR 0012).
    reviewer_routing: Mapped[str | None] = mapped_column(String, nullable=True)
    repository: Mapped[str] = mapped_column(String, nullable=False)
    base_ref: Mapped[str] = mapped_column(String, nullable=False)
    manifest_path: Mapped[str] = mapped_column(String, nullable=False)
    # The NAME of a server-side secret (an env var / secret-store key), never the
    # token value (hy-eji4). Only a URL target uses it; a local path leaves it
    # null. The value is read from the environment at propose time and is never
    # persisted here.
    token_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    # The token-source mode (hy-up4k, ADR 0026): 'env_ref' reads the token from
    # the environment by `token_ref`; 'encrypted' decrypts `token_ciphertext`
    # (AES-256-GCM, KEK from the environment) at propose time. The KEK is NEVER
    # stored here -- a DB dump alone cannot decrypt the token. Null = env_ref.
    token_source: Mapped[str | None] = mapped_column(String, nullable=True)
    token_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    token_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # GitHub App write-back auth (hy-bdhg, ADR 0027): the enterprise default, a
    # third token_source ('github_app'). The App `app_id`, and the App PRIVATE
    # KEY encrypted at rest via secret_box (AES-256-GCM, the same KEK). At propose
    # time the server decrypts the key, signs a <10-minute JWT, and mints a
    # per-op installation token -- the installation token is NEVER stored here.
    # The private key is the only stored secret and is never returned to a client.
    app_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    app_key_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    app_key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AdminAuditLog(Base):
    """An append-only record of an admin CONFIG action (hy-gh-75): who did what to which
    target, when, and whether it succeeded. It exists so an operator can answer "who changed
    this connection/source/config, and when" -- a monitoring surface, never an authority.

    APPEND-ONLY by contract: the repository exposes append + list and no update or delete, so
    the trail cannot be rewritten from the application. It stores NO secret value -- `detail`
    is a short, non-secret human string (a target id, a status), never a credential."""

    __tablename__ = "admin_audit_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("audit"))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )
    actor: Mapped[str] = mapped_column(String, nullable=False)
    actor_issuer: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[str] = mapped_column(String, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The tenant this action happened in (hq-hnrf, ADR-0037): the audit trail is
    # workspace-scoped like the rest, so one tenant's admin can never READ another
    # tenant's actions. NOT NULL server_default 'default' backfills every existing row
    # into the single implicit workspace, so a single-tenant estate is unchanged.
    workspace_id: Mapped[str] = mapped_column(
        String, nullable=False, default="default", server_default="default"
    )
    # The id of the request that performed this action (hy-w9ntg): a per-request correlation
    # id an operator can tie back to the response (returned as `X-Correlation-Id`). Nullable --
    # a row written before correlation ids, or off any request, has none (never a stale id).
    correlation_id: Mapped[str | None] = mapped_column(String, nullable=True)
