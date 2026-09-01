"""A bounded, LIVE probe of one observed-evidence connection (hq-jedd).

GREEN LIVENESS IS NOT READINESS. The admin readiness overview reports the health a
connector already RECORDED (a cached fact); this probe actively BUILDS the connector and
calls `test_connection()` to report, right now, whether the source is CONFIGURED,
REACHABLE, and FRESH (recently synced) -- and surfaces a degraded or blocked source
honestly rather than as absence. It REUSES the shared connector builder and the connector's
own `test_connection`; it does not reimplement a connector. Secrets stay in the server
environment (the builder reads them and drops them); nothing here holds or returns a
credential. The returned `reason` is free text the caller redacts at the serving boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from hyperset.connectors.build import build_connector

# A connection whose last successful sync is older than this reports FRESH=false: it may
# still be reachable, but the observed evidence it serves is stale -- a degraded state the
# probe names rather than hiding behind a green liveness check.
FRESHNESS_WINDOW = timedelta(hours=24)

# The live probe runs on an INTERACTIVE admin request, so it carries its OWN short, explicit
# deadline into the connector build -- it must NOT inherit the connector's 30s SYNC default (a
# batch bound). For a source that accepts the connection then stalls, the sync default would
# hang the request ~60s (Superset does login + list = two sequential 30s calls). Mirrors
# provider_probe.PROBE_TIMEOUT: a fast honest 'unreachable' beats a long hang. The SYNC path
# passes no override and keeps the 30s default unchanged.
CONNECTION_PROBE_TIMEOUT = 3.0


def freshness_from_run(run, now: datetime) -> bool:
    """The ONE freshness rule, shared by the LIVE probe (`probe_connection`) and the
    RECORDED overview (`read_observed_source_status`) so the two can never disagree
    (hq-hnrf area 2, adversary round 1). A source is FRESH only when its latest FINISHED
    sync SUCCEEDED and finished within the window: a recent FAILED sync did NOT refresh the
    observed evidence, so it is stale, not fresh -- counting its timestamp as fresh was the
    divergence. `run` is the latest finished `SyncRunRecord` (or None)."""
    return (
        run is not None
        and run.status == "succeeded"
        and run.finished_at is not None
        and (now - run.finished_at) <= FRESHNESS_WINDOW
    )


@dataclass(frozen=True)
class ConnectionProbe:
    """One connection's live status. `status` is the honest rollup (blocked worst):
    'blocked' (not configured or unreachable), 'degraded' (reachable but stale/unsynced),
    'ready' (configured, reachable, fresh). `health_status` maps to the RECORDED vocabulary
    (healthy/unhealthy/unknown) the caller persists. `reason` is free text (may name the
    source error) the caller redacts before returning it."""

    connection_id: str
    status: str
    configured: bool
    reachable: bool
    fresh: bool
    reason: str
    impact: str
    recovery: str
    health_status: str


def probe_connection(
    record,
    *,
    latest_finished_run=None,
    now: datetime | None = None,
    build=None,
) -> ConnectionProbe:
    """Probe one connection LIVE. `build` is injectable so a test drives a fake connector
    without a real Superset/DataHub; when None it resolves the module-level `build_connector`
    at CALL time (so a test can monkeypatch it). Never raises: ANY build or reachability
    failure becomes a blocked status with a reason, so the caller records health and responds
    rather than leaving an unhandled probe request."""
    build = build or build_connector
    now = now or datetime.now(tz=UTC)
    impact = (
        f"the {record.connector_type} observed-evidence source {record.display_name!r} "
        "contributes no fresh corroboration; governed Git context still serves, but "
        "evidence-backed findings from this source may be missing or stale"
    )

    def _result(status, *, configured, reachable, fresh, reason, recovery, health):
        return ConnectionProbe(
            connection_id=record.id,
            status=status,
            configured=configured,
            reachable=reachable,
            fresh=fresh,
            reason=reason,
            impact=impact,
            recovery=recovery,
            health_status=health,
        )

    if not record.config_ref:
        return _result(
            "blocked",
            configured=False,
            reachable=False,
            fresh=False,
            reason="no source is configured (the connection has no base URL or bundle path)",
            recovery="set the connection's base URL or bundle path in Admin > Connections",
            health="unknown",
        )

    try:
        connector = build(
            record.connector_type, record.config_ref, timeout=CONNECTION_PROBE_TIMEOUT
        )
    except Exception as exc:  # noqa: BLE001 -- ANY build failure is a blocked probe, never an
        # unhandled request: a ValueError (unsupported type / missing server-side credential)
        # AND any other construction error (a client library raising RuntimeError) both land
        # here so the caller always gets a result to record + audit (hq-jedd round 2, adversary).
        return _result(
            "blocked",
            configured=True,
            reachable=False,
            fresh=False,
            reason=str(exc) or "the connector could not be built",
            recovery=(
                "set the required credential in the server environment "
                "(never on the connection), or correct the connector type/URL"
            ),
            health="unhealthy",
        )

    try:
        test = connector.test_connection()
    except Exception as exc:  # noqa: BLE001 -- any connector/transport failure is 'unreachable'
        return _result(
            "blocked",
            configured=True,
            reachable=False,
            fresh=False,
            reason=f"the source could not be reached: {exc}",
            recovery="check the base URL is reachable and the server-side credential is valid",
            health="unhealthy",
        )

    if not test.ok:
        return _result(
            "blocked",
            configured=True,
            reachable=False,
            fresh=False,
            reason=test.detail or "the source rejected the connection",
            recovery="check the base URL is reachable and the server-side credential is valid",
            health="unhealthy",
        )

    fresh = freshness_from_run(latest_finished_run, now)
    if not fresh:
        return _result(
            "degraded",
            configured=True,
            reachable=True,
            fresh=False,
            reason=(
                "reachable, but no successful sync within the freshness window -- the "
                "observed evidence it serves may be stale"
            ),
            recovery="run a sync for this connection to refresh its observed evidence",
            health="healthy",
        )

    return _result(
        "ready",
        configured=True,
        reachable=True,
        fresh=True,
        reason="configured, reachable, and recently synced",
        recovery="",
        health="healthy",
    )
