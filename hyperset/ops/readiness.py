"""The admin readiness OVERVIEW (hy-gh-75, first slice).

One bounded, read-only snapshot of whether each part of a Hyperset deployment is
operational, for a protected Admin view. Each component reports a six-state
`status` (`ready` / `disabled` / `not_configured` / `degraded` / `blocked` /
`unknown`), when it was last checked,
who owns it, what breaks when it is not ready, and the recovery action.

WHAT IT READS, and what it deliberately does NOT do:

- DB-RECORDED health is authoritative. Superset/DataHub readiness is the health the
  connector already recorded (`ConnectionRecord.health_status/health_checked_at`),
  and Git-context readiness is the pin + last sync attempt (`read_git_context`). The
  overview reports what the store knows; it does NOT open a fresh network connection
  to Superset/DataHub/the warehouse on every page load (that would let an admin page
  hammer -- or be blocked by -- external services, and would make the view slow and
  non-deterministic). Live per-service re-probing is a named follow-on.
- CONFIG presence, never secret VALUES. model/embeddings/analytics-db/write-back/
  notifications report whether they are CONFIGURED (a provider chosen, a URL or a
  credential-reference NAME set) -- never a secret value, which is read server-side
  only. A configured component with no recorded liveness is `ready` (configured) or,
  where a required credential reference is missing, `degraded`; an unconfigured optional
  component is `not_configured`, while an unconfigured required component is `unknown`.
- READ-ONLY: like the rest of `hyperset/ops`, this constructs and calls no repository
  writer (enforced by tests/unit/ops/test_read_only_guard.py). The DB read that backs
  it also doubles as the `database` reachability probe: if it raises, `database` is
  `blocked` and the DB-derived components are `unknown`.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from hyperset.config.db_settings import analytics_db_url
from hyperset.config.provider_settings import embedding_provider, model_provider
from hyperset.ops.status import read_git_context
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresWritebackConfigRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES
from hyperset.security.redaction import redact_pointer

READY = "ready"
DEGRADED = "degraded"
BLOCKED = "blocked"
UNKNOWN = "unknown"
DISABLED = "disabled"
NOT_CONFIGURED = "not_configured"

# The overview's overall status is the WORST component status; this orders them so an
# informational optional state never makes a ready deployment look unhealthy, while an
# unknown required state never masks a proven failure (blocked is worst; ready is best).
_SEVERITY = {
    READY: 0,
    DISABLED: 0,
    NOT_CONFIGURED: 0,
    UNKNOWN: 1,
    DEGRADED: 2,
    BLOCKED: 3,
}

# Env names read for CONFIG-presence (never values): the provider/endpoint knobs a
# deployment sets. Kept here so the mapping is auditable in one place.
MODEL_PROVIDER_ENV = "HYPERSET_MODEL_PROVIDER"
EMBEDDING_PROVIDER_ENV = "HYPERSET_EMBEDDING_PROVIDER"
ANALYTICS_DB_URL_ENV = "HYPERSET_ANALYTICS_DB_URL"
NOTIFICATIONS_WEBHOOK_ENV = "HYPERSET_NOTIFICATIONS_WEBHOOK_URL"

# The exact components the overview reports, in display order (hy-gh-75). Named as a
# constant so the HTTP route and the tests bind to ONE list -- a component silently
# dropped or added reddens the coverage test rather than shrinking the view unnoticed.
COMPONENTS = (
    "api",
    "database",
    "superset",
    "datahub",
    "git_context",
    "model",
    "embeddings",
    "analytics_db",
    "writeback",
    "notifications",
)


@dataclass(frozen=True)
class ComponentReadiness:
    """One component's readiness line for the overview.

    `checked_at` is an ISO-8601 UTC instant or `None` when the component has never been
    checked (a connection whose health was never probed). `detail` is a short,
    non-secret human string; it never carries a credential value.
    """

    component: str
    status: str
    checked_at: str | None
    owner: str
    impact: str
    recovery: str
    detail: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _worst(statuses: list[str]) -> str:
    """The worst status in the list (empty -> unknown)."""
    return max(statuses, key=lambda s: _SEVERITY.get(s, _SEVERITY[UNKNOWN]), default=UNKNOWN)


def _overall(components: list[ComponentReadiness], env: dict) -> str:
    """Roll up required components plus PROVEN optional failures.

    Optional components retain their honest per-component `unknown` when no liveness fact
    exists, but absence of a failure fact does not make the deployment unready. An unknown
    FUTURE value still defaults to UNKNOWN rather than being silently treated as ready.
    Superset and DataHub are required evidence sources for the v0 walking skeleton; a
    required source that is absent or disabled is therefore unknown overall, not ready.
    """
    required = {
        "api",
        "database",
        "superset",
        "datahub",
        "git_context",
        "model",
        "embeddings",
    } | ({"ollama"} if "ollama" in {model_provider(env), embedding_provider(env)} else set())
    known_optional_nonfailures = {READY, DISABLED, NOT_CONFIGURED, UNKNOWN}
    statuses = []
    for component in components:
        if component.component in required:
            statuses.append(
                UNKNOWN if component.status in {DISABLED, NOT_CONFIGURED} else component.status
            )
        elif component.status in {DEGRADED, BLOCKED}:
            statuses.append(component.status)
        elif component.status in known_optional_nonfailures:
            statuses.append(READY)
        else:
            statuses.append(UNKNOWN)
    return _worst(statuses)


# --- component builders ------------------------------------------------------


def _api(now: datetime) -> ComponentReadiness:
    # If this code is running, the API is serving -- it is answering this very request.
    return ComponentReadiness(
        component="api",
        status=READY,
        checked_at=_iso(now),
        owner="Platform / on-call",
        impact="Agents and the admin UI cannot reach governed context or operations",
        recovery="Check the api container logs; restart the service if it is not answering",
    )


def _database(connections_ok: bool, error: str | None, now: datetime) -> ComponentReadiness:
    return ComponentReadiness(
        component="database",
        status=READY if connections_ok else BLOCKED,
        checked_at=_iso(now),
        owner="Platform / DBA",
        impact="No governed context, sync state, review tasks, or config can be read or written",
        recovery="Check Postgres (HYPERSET_DATABASE_URL) connectivity and the api logs",
        detail=None if connections_ok else f"the database read failed ({error})",
    )


_CONNECTION_HEALTH_TO_STATUS = {"healthy": READY, "unhealthy": BLOCKED, "unknown": UNKNOWN}


def _connector(component: str, connections: list, now: datetime) -> ComponentReadiness:
    """Aggregate readiness for all registered connections of one connector type, from
    the health the connector already RECORDED (not a fresh probe)."""
    owner_impact = {
        "superset": (
            "Analytics / connector admin",
            "Superset assets and lineage go stale; sync cannot refresh observations",
            "Verify the Superset connection URL and credentials in Admin > Connections, "
            "then re-run sync",
        ),
        "datahub": (
            "Data platform / connector admin",
            "DataHub evidence and lineage go stale",
            "Verify the DataHub connection and token in Admin > Connections, then re-run sync",
        ),
    }[component]
    if not connections:
        return ComponentReadiness(
            component=component,
            status=NOT_CONFIGURED,
            checked_at=None,
            owner=owner_impact[0],
            impact=owner_impact[1],
            recovery=owner_impact[2],
            detail=f"no {component} connection is configured",
        )
    enabled = [connection for connection in connections if connection.enabled]
    if not enabled:
        return ComponentReadiness(
            component=component,
            status=DISABLED,
            checked_at=None,
            owner=owner_impact[0],
            impact=owner_impact[1],
            recovery=owner_impact[2],
            detail=f"all {len(connections)} {component} connection(s) are disabled",
        )
    statuses = [_CONNECTION_HEALTH_TO_STATUS.get(c.health_status, UNKNOWN) for c in enabled]
    checked = [c.health_checked_at for c in enabled if c.health_checked_at is not None]
    healthy = sum(1 for s in statuses if s == READY)
    disabled = len(connections) - len(enabled)
    disabled_detail = f"; {disabled} disabled" if disabled else ""
    return ComponentReadiness(
        component=component,
        status=_worst(statuses),
        checked_at=_iso(max(checked)) if checked else None,
        owner=owner_impact[0],
        impact=owner_impact[1],
        recovery=owner_impact[2],
        detail=f"{healthy}/{len(enabled)} enabled connection(s) healthy{disabled_detail}",
    )


def _git_context(rows: list, now: datetime) -> ComponentReadiness:
    """Readiness of the Git-owned context authority: the pin, and the last sync attempt.
    Aggregated worst-across-sources from `read_git_context` (DB, no remote probe)."""
    owner = "Governance / Git owner"
    impact = "Governed domain meaning cannot refresh from Git; the served pin may be stale"
    recovery = "Check the configured repo/ref/path and the last sync error; re-run context sync"
    if not rows:
        return ComponentReadiness(
            component="git_context",
            status=UNKNOWN,
            checked_at=None,
            owner=owner,
            impact=impact,
            recovery=recovery,
            detail="no Git context source is configured",
        )
    statuses: list[str] = []
    for row in rows:
        if row.pinned:
            # A snapshot is serving; a failed LAST attempt means it is stale, not down.
            statuses.append(DEGRADED if row.last_attempt_status == "failed" else READY)
        elif row.last_attempt_status == "failed":
            statuses.append(BLOCKED)  # never pinned and failing -> nothing serves
        else:
            statuses.append(UNKNOWN)  # added but never synced yet
    checked = [r.last_attempt_at for r in rows if r.last_attempt_at is not None]
    pinned = sum(1 for r in rows if r.pinned)
    return ComponentReadiness(
        component="git_context",
        status=_worst(statuses),
        checked_at=_iso(max(checked)) if checked else None,
        owner=owner,
        impact=impact,
        recovery=recovery,
        detail=f"{pinned}/{len(rows)} source(s) pinned to a commit",
    )


def _configured_component(
    *,
    component: str,
    configured: bool,
    owner: str,
    impact: str,
    recovery: str,
    now: datetime,
    detail_on: str,
    detail_off: str,
    status_off: str = UNKNOWN,
) -> ComponentReadiness:
    """A config-presence component: READY when configured, `status_off` when not. It is NOT
    live-probed here, so it is never `blocked`/`degraded` on this path -- absent config
    is not a proven failure, and liveness is a named follow-on."""
    return ComponentReadiness(
        component=component,
        status=READY if configured else status_off,
        checked_at=_iso(now),
        owner=owner,
        impact=impact,
        recovery=recovery,
        detail=detail_on if configured else detail_off,
    )


def _writeback_needs_secret(target, env: dict) -> bool:
    """A URL target in env_ref mode needs its NAMED server-side secret present -- checked by
    PRESENCE ONLY, never the value. A local-path target needs none, regardless of a stale
    token-source field. Mirrors `resolve_writeback_token`, which returns None for local paths."""
    repository = str(target.repository or "").strip().lower()
    remote = repository.startswith(("http://", "https://", "git://", "ssh://", "git@"))
    return bool(
        target.enabled
        and remote
        and target.token_source == "env_ref"
        and target.token_ref
        and not str(env.get(target.token_ref, "")).strip()
    )


def _writeback_summary(targets: list) -> str:
    """One non-secret line enumerating every configured target and its state. The repository
    is userinfo-REDACTED (a target repo can be a credential-bearing URL, and this overview
    never carries a secret value)."""
    parts = []
    for target in targets:
        label = "default" if target.is_default else (target.routing_key or "unrouted")
        repo = redact_pointer(target.repository) if target.repository else target.repository
        flags = ["enabled" if target.enabled else "disabled"]
        if target.test_result:
            flags.append(f"test {target.test_result}")
        parts.append(f"{label} {repo!r} [{', '.join(flags)}]")
    enabled_n = sum(1 for target in targets if target.enabled)
    return f"{len(targets)} write-back target(s) configured ({enabled_n} enabled): " + "; ".join(
        parts
    )


def _writeback(session_factory, db_ok: bool, env: dict, now: datetime) -> ComponentReadiness:
    """The write-back component, reconciled with the LIVE MULTI-TARGET model (hy-lotg3): it
    reports every configured target and its enabled/test/credential state, not a single legacy
    default. It reads the whole estate's targets (`list`), mirroring the ALL_WORKSPACES posture
    the readiness overview already takes -- a keyed target the router (`get_by_routing`) would
    serve no longer reads as 'no target'."""
    owner = "Governance / Git owner"
    impact = "Review proposals cannot be opened as Git pull requests"
    recovery = "Configure a write-back target and its credential reference in Admin > Settings"

    def _line(status, detail, *, checked=True):
        return ComponentReadiness(
            component="writeback",
            status=status,
            checked_at=_iso(now) if checked else None,
            owner=owner,
            impact=impact,
            recovery=recovery,
            detail=detail,
        )

    if not db_ok:
        return _line(
            UNKNOWN,
            "the database is unreachable, so the write-back config could not be read",
            checked=False,
        )
    targets = PostgresWritebackConfigRepository(session_factory).list(workspace=None)
    if not targets:
        return _line(NOT_CONFIGURED, "no write-back target configured (Propose to Git is disabled)")

    summary = _writeback_summary(targets)
    enabled = [target for target in targets if target.enabled]
    if not enabled:
        # Configured but every target is disabled: routing fails closed, so Propose is off.
        return _line(DISABLED, f"{summary}; NONE enabled, so Propose to Git is off until one is")
    missing = [target for target in enabled if _writeback_needs_secret(target, env)]
    if missing:
        refs = ", ".join(sorted({str(target.token_ref) for target in missing}))
        return _line(
            DEGRADED,
            f"{summary}; credential reference missing for {refs} "
            "(set the secret server-side; the value is read server-side, never stored or shown)",
        )
    return _line(READY, summary)


# --- the overview ------------------------------------------------------------


def admin_readiness(
    session_factory, *, env: dict | None = None, now: datetime | None = None
) -> dict:
    """The bounded, read-only readiness overview: one line per component, plus the worst
    `overall` status and the `generated_at` instant. Never returns a secret value."""
    env = os.environ if env is None else env
    now = now or datetime.now(tz=UTC)

    # The DB read is also the `database` reachability probe.
    try:
        # A deployment readiness overview spans every tenant (hq-t6nx): explicit
        # system opt-in. (The DB read is also the `database` reachability probe.)
        connections = list(
            PostgresConnectionRepository(session_factory).list(workspace=ALL_WORKSPACES)
        )
        db_ok, db_error = True, None
    except Exception as exc:  # noqa: BLE001 -- any DB failure means "blocked", reported not raised
        connections, db_ok, db_error = [], False, type(exc).__name__

    components: list[ComponentReadiness] = [_api(now), _database(db_ok, db_error, now)]

    if db_ok:
        by_type: dict[str, list] = {}
        for connection in connections:
            by_type.setdefault(connection.connector_type, []).append(connection)
        components.append(_connector("superset", by_type.get("superset", []), now))
        components.append(_connector("datahub", by_type.get("datahub", []), now))
        try:
            git_rows = list(read_git_context(session_factory))
        except Exception:  # noqa: BLE001 -- the connection read succeeded; a git read fault is unknown
            git_rows = []
        components.append(_git_context(git_rows, now))
    else:
        for component in ("superset", "datahub", "git_context"):
            components.append(
                ComponentReadiness(
                    component=component,
                    status=UNKNOWN,
                    checked_at=None,
                    owner="Platform / DBA",
                    impact="status is unknown because the database is unreachable",
                    recovery="restore the database, then reload",
                    detail="not read: the database is unreachable",
                )
            )

    components.append(
        _configured_component(
            component="model",
            configured=bool(model_provider(env)),
            owner="Platform / ML",
            now=now,
            impact="The planner cannot generate context directives",
            recovery="Set HYPERSET_MODEL_PROVIDER=openai and the OpenAI endpoint/credential",
            detail_on=f"provider {model_provider(env)!r}",
            detail_off="no model provider configured",
        )
    )
    components.append(
        _configured_component(
            component="embeddings",
            configured=bool(embedding_provider(env)),
            owner="Platform / ML",
            now=now,
            impact="Assist discover ranking is unavailable (discover returns 500)",
            recovery="Set HYPERSET_EMBEDDING_PROVIDER and its endpoint",
            detail_on=f"provider {embedding_provider(env)!r}",
            detail_off="no embedding provider configured",
        )
    )
    components.append(
        _configured_component(
            component="analytics_db",
            configured=bool(analytics_db_url(env)),
            owner="Analytics / DBA",
            now=now,
            impact="Plan validation and analytics context lack live warehouse metadata",
            recovery="Set HYPERSET_ANALYTICS_DB_URL to a reachable warehouse",
            detail_on="warehouse URL configured (not live-probed in this overview)",
            detail_off="no analytics warehouse URL configured",
            status_off=NOT_CONFIGURED,
        )
    )
    components.append(_writeback(session_factory, db_ok, env, now))
    components.append(
        _configured_component(
            component="notifications",
            configured=bool(str(env.get(NOTIFICATIONS_WEBHOOK_ENV, "")).strip()),
            owner="Platform / on-call",
            now=now,
            impact="Failure and review events are not delivered to a channel",
            recovery="Configure a notification channel (delivery wiring is a follow-on)",
            detail_on="a notification webhook is configured",
            detail_off="no notification channel configured",
            status_off=NOT_CONFIGURED,
        )
    )

    overall = _overall(components, env)
    return {
        "generated_at": _iso(now),
        "overall": overall,
        "components": [c.as_dict() for c in components],
    }
