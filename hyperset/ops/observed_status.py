"""A read-only, per-source status OVERVIEW of every configured observed-evidence
source (hq-hnrf area 2).

Enterprise deployments configure MORE THAN ONE observed/integration source (several
Superset/DataHub/warehouse connections), and an operator needs to see, at a glance and
per DISTINCT source, whether each is CONFIGURED, REACHABLE, and FRESH -- with the impact
and the recovery action. This overview reports what the store already RECORDED (the
connection's last health probe + its last finished sync); it opens NO fresh network
connection, so listing the whole estate costs no live probe (GREEN LIVENESS IS NOT
READINESS -- the same discipline as `admin_readiness`). The deep, live per-source check
is the existing on-demand `probe_connection`; this is the recorded at-a-glance rollup,
using the SAME status vocabulary (ready / degraded / blocked / unknown) and freshness
window so the two cannot disagree on what the words mean.

Every entry keeps the SOURCE IDENTITY (connection id, connector type, display name); the
observed evidence a source contributes is never Git authority -- this surface reports
integration health, never governed meaning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hyperset.db.base import utcnow
from hyperset.ops.connection_probe import freshness_from_run
from hyperset.repositories.postgres import PostgresConnectionRepository, PostgresSyncRepository
from hyperset.repositories.scope import _AllWorkspaces

# The rollup vocabulary, shared with the live `ConnectionProbe`: blocked (not configured
# or unreachable), degraded (reachable but stale/unsynced), ready (configured + reachable +
# fresh), and -- unique to the RECORDED overview -- unknown (never probed, so reachability
# is not yet known; a live probe has never run).
READY = "ready"
DEGRADED = "degraded"
BLOCKED = "blocked"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class ObservedSourceStatus:
    """One configured observed source's recorded status. `reachable` is None when the
    source has never been probed (reachability unknown), True/False once a probe recorded
    health. `reason` names the current state; `impact` and `recovery` are the operator's
    'what it costs' and 'what to do'. `reason`/`recovery` are non-secret, but the caller
    still redacts free text at the serving boundary."""

    connection_id: str
    connector_type: str
    display_name: str
    enabled: bool
    workspace_id: str
    status: str
    configured: bool
    reachable: bool | None
    fresh: bool
    last_health_status: str | None
    last_health_at: datetime | None
    last_sync_status: str | None
    last_sync_finished_at: datetime | None
    reason: str
    impact: str
    recovery: str

    def as_dict(self) -> dict:
        from dataclasses import asdict

        return asdict(self)


def read_observed_source_status(
    session_factory, *, workspace: str | _AllWorkspaces, now: datetime | None = None
) -> list[ObservedSourceStatus]:
    """Every configured observed source's recorded status, WITHIN `workspace` (hq-t6nx):
    a tenant's admin sees only its own sources. `workspace` is required and fail-closed;
    a system caller passes `ALL_WORKSPACES`. Read-only: no live probe, no mutation."""
    connections = PostgresConnectionRepository(session_factory).list(workspace=workspace)
    syncs = PostgresSyncRepository(session_factory)
    moment = now or utcnow()
    return [
        _status(connection, syncs.latest_finished_run(connection.id), moment)
        for connection in connections
    ]


def _status(record, last_sync, now: datetime) -> ObservedSourceStatus:
    configured = bool(record.config_ref)
    # RECORDED reachability: a live probe writes 'healthy'/'unhealthy'; None (or an
    # unrecognised value) means no probe has run yet, so reachability is UNKNOWN -- never
    # silently reported as reachable.
    if record.health_status == "healthy":
        reachable: bool | None = True
    elif record.health_status == "unhealthy":
        reachable = False
    else:
        reachable = None
    # The SAME freshness rule the live probe uses (freshness_from_run): a recent FAILED
    # sync is stale, not fresh -- so the recorded overview and the live probe agree.
    fresh = freshness_from_run(last_sync, now)

    impact = (
        f"the {record.connector_type} observed-evidence source {record.display_name!r} "
        "contributes no fresh corroboration; governed Git context still serves, but "
        "evidence-backed findings from this source may be missing or stale"
    )

    def _build(status, *, reason, recovery):
        return ObservedSourceStatus(
            connection_id=record.id,
            connector_type=record.connector_type,
            display_name=record.display_name,
            enabled=record.enabled,
            workspace_id=record.workspace_id,
            status=status,
            configured=configured,
            reachable=reachable,
            fresh=fresh,
            last_health_status=record.health_status,
            last_health_at=record.health_checked_at,
            last_sync_status=last_sync.status if last_sync is not None else None,
            last_sync_finished_at=last_sync.finished_at if last_sync is not None else None,
            reason=reason,
            impact=impact,
            recovery=recovery,
        )

    if not configured:
        return _build(
            BLOCKED,
            reason="no source is configured (the connection has no base URL or bundle path)",
            recovery="set the connection's base URL or bundle path in Admin > Connections",
        )
    if reachable is False:
        return _build(
            BLOCKED,
            reason="the last probe found the source unreachable",
            recovery="check the base URL is reachable and the server-side credential is valid, "
            "then re-probe",
        )
    if reachable is None:
        return _build(
            UNKNOWN,
            reason="the source has never been probed, so its reachability is not yet known",
            recovery="probe this connection to record whether it is reachable",
        )
    if not fresh:
        return _build(
            DEGRADED,
            reason="reachable, but no successful sync within the freshness window -- the "
            "observed evidence it serves may be stale",
            recovery="run a sync for this connection to refresh its observed evidence",
        )
    return _build(READY, reason="configured, reachable, and recently synced", recovery="")
