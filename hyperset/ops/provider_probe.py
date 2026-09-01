"""Bounded LIVE reachability probes for the model/provider components (hy-ng8o7, V1 gap Admin/5).

The admin readiness overview reports only CONFIG PRESENCE for these components; this actively
checks, right now and within a strict timeout, whether each is REACHABLE. It mirrors
`connection_probe`: every probe returns a typed `ProviderProbe` and NEVER raises -- any
resolution or reachability failure becomes a `blocked` status with a reason, so the caller
always has a result to serve. Secrets never enter a result: the probe reads a key from the
server environment to AUTHENTICATE the check and drops it; the `reason` is free text the caller
redacts at the serving boundary (like `probe_connection`).

Bounding: each network call carries `PROBE_TIMEOUT`, and an unconfigured component makes NO
network call at all. The analytics-DB probe is CONNECT-ONLY -- a TCP reachability check, never
a query -- because Hyperset executes no warehouse SQL in v0 (ADR 0012).

The network primitives are INJECTABLE (`http`, `tcp`), so a test drives reachable / unreachable
/ timeout without a real OpenAI endpoint or database.
"""

from __future__ import annotations

import os
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from hyperset.config.db_settings import analytics_db_url
from hyperset.config.provider_settings import (
    embedding_api_key,
    embedding_base_url,
    embedding_provider,
    model_provider,
    openai_api_key,
    openai_base_url,
)

# Model/provider components this probes, in display order. Bound to ONE list so a
# component silently dropped reddens the coverage test rather than shrinking the surface.
PROVIDER_COMPONENTS = ("openai", "analytics_db", "embedding", "runtime")

# Every probe is bounded by this. Short, because an admin waiting on a diagnostic wants a
# fast honest "unreachable" over a long hang; an unreachable host fails within it.
PROBE_TIMEOUT = 3.0

# Config env, in one auditable place (mirrors ops/readiness.py + candidates/service.py).
OPENAI_BASE_URL_ENV = "HYPERSET_OPENAI_BASE_URL"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
ANALYTICS_DB_URL_ENV = "HYPERSET_ANALYTICS_DB_URL"
EMBEDDING_PROVIDER_ENV = "HYPERSET_EMBEDDING_PROVIDER"
EMBEDDING_BASE_URL_ENV = "HYPERSET_EMBEDDING_BASE_URL"
EMBEDDING_API_KEY_ENV = "HYPERSET_EMBEDDING_API_KEY"
MODEL_PROVIDER_ENV = "HYPERSET_MODEL_PROVIDER"
# (impact, recovery) per component -- what a degraded/blocked component costs and how to fix it.
_META = {
    "openai": (
        "the hosted OpenAI-compatible provider cannot be reached or authenticated; model and "
        "assist paths are unavailable",
        f"set {OPENAI_BASE_URL_ENV} and a valid {OPENAI_API_KEY_ENV} in the server environment",
    ),
    "analytics_db": (
        "the analytics database is unreachable; features that read it cannot run (Hyperset runs "
        "no warehouse SQL in v0, so this is a connectivity check only)",
        f"check {ANALYTICS_DB_URL_ENV} host/port and that the database accepts connections",
    ),
    "embedding": (
        "the embedding provider cannot be reached; semantic candidate discovery falls back or "
        "fails",
        f"set {EMBEDDING_PROVIDER_ENV} and its endpoint ({EMBEDDING_BASE_URL_ENV}), or use "
        "'deterministic' offline",
    ),
    "runtime": (
        "the configured model runtime cannot be reached; assist drafting is unavailable",
        f"set {MODEL_PROVIDER_ENV}=openai, {OPENAI_BASE_URL_ENV}, and {OPENAI_API_KEY_ENV}",
    ),
}


@dataclass(frozen=True)
class ProviderProbe:
    """One provider component's live status. `status`: 'ready' (configured + reachable, or a
    local in-process provider), 'blocked' (configured but unreachable), 'unknown' (not
    configured, or configured with no probeable endpoint -- absence is not a proven failure).
    `reachable` is None when there is no remote endpoint to probe (a local provider). `reason`
    is free text the caller REDACTS before serving."""

    component: str
    status: str
    configured: bool
    reachable: bool | None
    reason: str
    impact: str
    recovery: str

    def as_dict(self) -> dict:
        return asdict(self)


HttpProbe = Callable[[str], "tuple[bool, str]"]
TcpProbe = Callable[[str, int], "tuple[bool, str]"]


def _http_reachable(url: str, *, headers: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """A bounded HTTP GET reachability check. Any 2xx/3xx/4xx RESPONSE means the endpoint is
    reachable (a 401 proves the server answered); only a transport failure or timeout is
    unreachable. NEVER raises."""
    try:
        request = urllib.request.Request(url, headers=dict(headers or {}), method="GET")
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        # The server answered (e.g. 401/404) -- it is REACHABLE, just not this path/auth.
        return True, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 -- any transport/timeout failure is 'unreachable'
        return False, str(exc) or "the endpoint could not be reached"


def _tcp_reachable(host: str, port: int) -> tuple[bool, str]:
    """A bounded TCP connect (CONNECT-ONLY, no query -- ADR 0012). NEVER raises."""
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            return True, f"connected to {host}:{port}"
    except Exception as exc:  # noqa: BLE001 -- any connect failure/timeout is 'unreachable'
        return False, str(exc) or f"could not connect to {host}:{port}"


def _ready(component, *, configured, reachable, reason) -> ProviderProbe:
    impact, recovery = _META[component]
    status = "ready" if (reachable is None or reachable) else "blocked"
    if not configured:
        status = "unknown"
    return ProviderProbe(
        component=component,
        status=status,
        configured=configured,
        reachable=reachable,
        reason=reason,
        impact=impact,
        recovery=recovery,
    )


def _unconfigured(component, *, reason) -> ProviderProbe:
    return _ready(component, configured=False, reachable=False, reason=reason)


def _http_component(component, url, http, *, headers=None) -> ProviderProbe:
    ok, detail = http(url, headers=headers) if headers is not None else http(url)
    return _ready(component, configured=True, reachable=ok, reason=detail)


def _openai(env, http, *, component="openai", provider_selected=False) -> ProviderProbe:
    key = openai_api_key(env) or ""
    base = openai_base_url(env) or ""
    if not key:
        if provider_selected:
            return _ready(
                component,
                configured=True,
                reachable=False,
                reason=f"provider 'openai' but {OPENAI_API_KEY_ENV} is not set",
            )
        return _unconfigured(component, reason=f"{OPENAI_API_KEY_ENV} is not set")
    if not base:
        return _ready(
            component,
            configured=True,
            reachable=False,
            reason=f"a key is set but {OPENAI_BASE_URL_ENV} is not, so there is no endpoint",
        )
    # The key AUTHENTICATES the reachability check and is dropped here -- it never enters the
    # ProviderProbe or the served response.
    return _http_component(
        component, f"{base.rstrip('/')}/models", http, headers={"Authorization": f"Bearer {key}"}
    )


def _analytics_db(env, tcp) -> ProviderProbe:
    url = analytics_db_url(env) or ""
    if not url:
        return _unconfigured("analytics_db", reason=f"{ANALYTICS_DB_URL_ENV} is not set")
    split = urlsplit(url)
    host = split.hostname
    if not host:
        return _ready(
            "analytics_db",
            configured=True,
            reachable=False,
            reason="the analytics DB URL names no host",
        )
    port = split.port or 5432
    ok, detail = tcp(host, port)
    return _ready("analytics_db", configured=True, reachable=ok, reason=detail)


def _embedding(env, http) -> ProviderProbe:
    provider = embedding_provider(env).lower()
    if not provider:
        return _unconfigured("embedding", reason=f"{EMBEDDING_PROVIDER_ENV} is not set")
    if provider == "deterministic":
        return _ready(
            "embedding",
            configured=True,
            reachable=None,
            reason="deterministic offline provider -- no network endpoint to probe",
        )
    base = embedding_base_url(env) or openai_base_url(env) or ""
    if not base:
        return _ready(
            "embedding",
            configured=True,
            reachable=False,
            reason=f"provider {provider!r} but {EMBEDDING_BASE_URL_ENV} is not set",
        )
    key = embedding_api_key(env) or ""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return _http_component("embedding", f"{base.rstrip('/')}/models", http, headers=headers)


def _runtime(env, http) -> ProviderProbe:
    provider = model_provider(env).lower()
    if not provider:
        return _unconfigured("runtime", reason=f"{MODEL_PROVIDER_ENV} is not set")
    if provider in ("openai", "frontier"):
        return _openai(env, http, component="runtime", provider_selected=True)
    return _ready(
        "runtime",
        configured=True,
        reachable=False,
        reason=f"provider {provider!r} is unsupported; the deployed runtime requires 'openai'",
    )


def probe_provider(
    component: str,
    *,
    env: Mapping[str, str] | None = None,
    http: HttpProbe | None = None,
    tcp: TcpProbe | None = None,
) -> ProviderProbe:
    """Probe ONE provider component LIVE. `http`/`tcp` are injectable so a test drives
    reachable/unreachable/timeout without a real endpoint; when None the real bounded
    primitives are used. Never raises."""
    env = os.environ if env is None else env
    http = http or (lambda url, headers=None: _http_reachable(url, headers=headers))
    tcp = tcp or _tcp_reachable
    if component == "openai":
        return _openai(env, http)
    if component == "analytics_db":
        return _analytics_db(env, tcp)
    if component == "embedding":
        return _embedding(env, http)
    if component == "runtime":
        return _runtime(env, http)
    raise ValueError(f"unknown provider component {component!r}")


def probe_providers(
    *,
    env: Mapping[str, str] | None = None,
    http: HttpProbe | None = None,
    tcp: TcpProbe | None = None,
) -> list[ProviderProbe]:
    """Every provider component, live, in display order. An unconfigured component makes no
    network call, so the real latency is bounded by (configured components x PROBE_TIMEOUT)."""
    return [probe_provider(name, env=env, http=http, tcp=tcp) for name in PROVIDER_COMPONENTS]
