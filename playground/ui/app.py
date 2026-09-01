from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from collections import deque
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit

from hyperset.config.playground_settings import (
    playground_agents_json,
    playground_default_agent,
    playground_default_model,
    playground_models_json,
    playground_upstream_base_url,
)
from hyperset.config.provider_settings import (
    openai_api_key,
    openai_base_url,
    openai_max_completion_tokens,
    openai_reasoning_effort,
)
from hyperset.embedding.openai_embeddings import is_safe_openai_endpoint
from hyperset.observability.interaction import (
    CORRELATION_HEADER,
    INTENT_HEADER,
    SESSION_HEADERS,
    TOOL_CALL_HEADER,
    TURN_HEADER,
    opaque_token,
)
from hyperset.security.redaction import redact_free_text_userinfo

try:
    import psycopg
except ImportError:  # pragma: no cover - the project dependency supplies this
    psycopg = None

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional for direct system-Python launches
    load_dotenv = None

ROOT = Path(__file__).resolve().parent
BASE_DIR = ROOT

if load_dotenv:
    load_dotenv(ROOT.parent.parent / ".env", override=False)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_HYPERSET_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_SUPERSET_BASE_URL = "http://127.0.0.1:8088"
DEFAULT_DATAHUB_BASE_URL = "http://127.0.0.1:8090"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_OPENAI_REASONING_EFFORT = "medium"
# The completion-token cap for an OpenAI-COMPATIBLE gateway. It counts invisible
# reasoning tokens too, so the old 1024 let a reasoning model spend the whole
# budget thinking and return an empty answer (GitHub #325). This larger default
# applies ONLY to the gateway branch; the real OpenAI Responses path keeps its
# vendor-default 1024 unchanged. Configurable via
# HYPERSET_OPENAI_MAX_COMPLETION_TOKENS.
DEFAULT_OPENAI_MAX_COMPLETION_TOKENS = 4096
# The real OpenAI Responses path's cap, kept byte-identical to the pre-#325 code.
DEFAULT_OPENAI_RESPONSES_MAX_TOKENS = 1024
DEFAULT_SUPERSET_VERSION = "6.1.0"
DEFAULT_DATAHUB_VERSION = "v1.6.0"
MAX_DEMO_SQL_BYTES = 32_000
MAX_DEMO_ROWS = 100
AGENT_MAX_TURNS = 20
AGENT_TIMEOUT_SECONDS = 300


def _configured_agent_profiles() -> dict[str, dict[str, str]]:
    """Load only the agent profiles explicitly supplied by the deployment."""
    raw = playground_agents_json().strip()
    if not raw:
        return {}
    try:
        configured = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(configured, list):
        configured = {
            str(item.get("value") or "").strip(): item
            for item in configured
            if isinstance(item, dict) and str(item.get("value") or "").strip()
        }
    if not isinstance(configured, dict):
        return {}
    profiles = {}
    for value, item in configured.items():
        if not isinstance(item, dict):
            continue
        key = str(value).strip()
        if not key:
            continue
        profiles[key] = {
            "label": str(item.get("label") or key),
            "description": str(item.get("description") or "Configured Hyperset agent."),
            "instruction": str(
                item.get("instruction") or "Answer using the available governed context."
            ),
        }
    return profiles


def _configured_models() -> list[dict[str, str]]:
    """Load selectable OpenAI models from config without exposing provider secrets.

    The playground is the customer-facing MVP path. Non-OpenAI entries from an
    old local-demo config are ignored rather than left selectable, so a browser
    request cannot silently route a turn to a second runtime.
    """
    raw = playground_models_json().strip()
    if raw:
        try:
            configured = json.loads(raw)
        except json.JSONDecodeError:
            configured = None
        if isinstance(configured, list):
            models = []
            for item in configured:
                if not isinstance(item, dict) or not item.get("value") or not item.get("provider"):
                    continue
                value = str(item["value"]).strip()
                provider = str(item["provider"]).strip().lower()
                # The customer-facing MVP has one deliberate hosted runtime. Keep stale
                # local-demo entries from becoming an accidental second serving path.
                if provider != "openai" or value != DEFAULT_OPENAI_MODEL:
                    continue
                models.append(
                    {
                        "value": value,
                        "label": str(item.get("label") or f"{value} · {provider}"),
                        "provider": provider,
                    }
                )
            if models:
                return models
    return [
        {
            "value": DEFAULT_OPENAI_MODEL,
            "label": f"{DEFAULT_OPENAI_MODEL} · openai",
            "provider": "openai",
        }
    ]


def _playground_runtime_config() -> dict:
    """Return the non-secret agent/model choices for the browser workspace."""
    profiles = _configured_agent_profiles()
    models = _configured_models()
    default_agent = playground_default_agent().strip()
    default_model = playground_default_model().strip()
    agent_values = list(profiles)
    model_values = [item["value"] for item in models]
    resolved_default_agent = (
        default_agent
        if default_agent in agent_values
        else (agent_values[0] if agent_values else "")
    )
    return {
        "agents": [
            {"value": value, "label": profile["label"], "detail": profile["description"]}
            for value, profile in profiles.items()
        ],
        "models": models,
        "default_agent": resolved_default_agent,
        "default_model": default_model
        if default_model in model_values
        else (model_values[0] if model_values else ""),
        "connections": [
            {"value": "superset", "label": "Superset metadata"},
            {"value": "datahub", "label": "DataHub lineage"},
            {"value": "analytics_db", "label": "Analytics warehouse"},
        ],
        "tools": [
            {"value": "run_read_only_sql", "label": "Read-only SQL"},
        ],
    }


def _configured_default_model(provider: str) -> str:
    models = _configured_models()
    matching = next((item["value"] for item in models if item["provider"] == provider), None)
    return matching or (models[0]["value"] if models else "")


class PlaygroundResolutionError(RuntimeError):
    """A structured, recoverable failure to resolve governed context."""

    def __init__(self, error_payload: dict):
        self.error_payload = error_payload
        super().__init__(error_payload.get("message") or "context resolution failed")


class PlaygroundProviderError(RuntimeError):
    """A model/provider-call fault on the discovery call, BEFORE any directive
    exists. Distinct from PlaygroundResolutionError so a provider or credential
    misconfiguration is not mislabeled as an empty catalog and does not silently
    downgrade a governed answer under a corpus-sounding cause (GitHub #344)."""

    def __init__(self, error_payload: dict):
        self.error_payload = error_payload
        super().__init__(error_payload.get("message") or "context discovery provider fault")


def _provider_error_payload(provider: str, model: str, exc: Exception) -> dict:
    """Attribute a discovery-call failure to the model provider/config, not the
    catalog. The downgrade to an ungoverned answer stays permitted (ADR 0019
    labels each answer by its provenance), but its CAUSE must read as a provider
    fault so an operator is not pointed at their corpus (GitHub #344)."""
    return {
        "code": "context_discovery_provider_error",
        "message": (
            f"The {provider} model provider failed the context-discovery call, so no governed "
            "context could be requested. This is a model/provider configuration fault, not an "
            "empty catalog."
        ),
        "recovery": (
            "Check the model provider configuration (credentials, base URL, and model name) "
            "for the discovery call, then retry."
        ),
        # SERVER-BOUNDARY redaction (hy-yts5j #447 r2): a provider fault against a
        # MISCONFIGURED base_url can echo a credential-bearing `scheme://user:token@host`
        # in the client's exception text. Strip URL userinfo here with the canonical
        # detector -- parity with the admin surface's `_redact_free_text_userinfo` -- so
        # BOTH render sites (the provider-fault view and the run-details disclosure) are
        # covered; sanitising only one paragraph would not close it.
        "detail": redact_free_text_userinfo(str(exc)),
        "provider": provider,
        "model": model,
    }


_FORBIDDEN_SQL = re.compile(
    r"\b(?:insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do)\b",
    re.IGNORECASE,
)
_SOURCE_RULES_QUESTION = re.compile(
    r"\b(which|what)\b.*\b(source|sources|rules|definition|definitions|filter|filters|join|joins|evidence)\b",
    re.IGNORECASE,
)
_DATA_QUESTION = re.compile(
    r"\b(how much|what was|what is|calculate|count|sum|total|amount|"
    r"rows?|records?|values?|breakdown)\b",
    re.IGNORECASE,
)


def _base_url(name: str, default: str) -> str:
    return os.environ.get(name, default).rstrip("/")


def _openai_base_url() -> str:
    """The hosted-OpenAI base URL from the settings object (else the legacy env), defaulted
    and trailing-slash-trimmed exactly as `_base_url` did (hy-7m5yg -- one read path)."""
    return (openai_base_url() or DEFAULT_OPENAI_BASE_URL).rstrip("/")


def _upstream_target() -> str:
    """The UI proxy's upstream Hyperset API target, VERBATIM: the settings object (else the
    legacy HYPERSET_HTTP_BASE_URL), with the default applied ONLY when the value is ABSENT
    (None). A present-empty legacy value stays empty, matching the prior
    `os.environ.get(name, default)`. NO trailing-slash trim -- see `_upstream_base_url`."""
    value = playground_upstream_base_url()
    return DEFAULT_HYPERSET_BASE_URL if value is None else value


def _upstream_base_url() -> str:
    """The proxy request target: `_upstream_target()` trailing-slash-trimmed, exactly as the
    old `_base_url` did (hy-lk83s -- one read path). Used where the value is CONCATENATED with a
    request path; the diagnostic print uses the un-trimmed `_upstream_target()` so the startup
    log stays byte-identical to the pre-migration os.environ.get output (hy-lk83s adversary)."""
    return _upstream_target().rstrip("/")


def _openai_max_completion_tokens() -> int:
    """The OpenAI completion-token budget, large enough that a reasoning model's
    invisible reasoning tokens do not consume the whole cap and leave the answer
    empty (GitHub #325). A non-positive or non-integer override falls back.

    Reads through the settings accessor (loaded config, else the legacy env) -- the loaded
    value is already a validated positive int; a raw env string is parsed here, leniently."""
    raw = openai_max_completion_tokens()
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return DEFAULT_OPENAI_MAX_COMPLETION_TOKENS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_OPENAI_MAX_COMPLETION_TOKENS
    return value if value > 0 else DEFAULT_OPENAI_MAX_COMPLETION_TOKENS


_VENDOR_OPENAI_HOST = urlsplit(DEFAULT_OPENAI_BASE_URL).hostname or "api.openai.com"


def _is_vendor_openai_base_url(base_url: str) -> bool:
    """True when `base_url` addresses the VENDOR OpenAI endpoint (api.openai.com), for ANY
    vendor-equivalent spelling (hy-yts5j r2).

    A textual `== DEFAULT_OPENAI_BASE_URL` let vendor-equivalent forms slip past: an
    explicit default port `https://api.openai.com:443/v1`, or a case variant
    `https://API.OPENAI.COM/v1`, do not match the literal, so they select the gateway
    branch and let a Luna model reach the vendor host anyway -- a fail-OPEN that
    reintroduces the P1. Compare the ORIGIN canonically instead: case-insensitive https
    scheme + host, and the default https port (443) whether implicit or explicit. A real
    gateway has a different host, so it is never mistaken for the vendor."""
    parts = urlsplit(base_url)
    # Strip a single trailing DNS dot ("api.openai.com." is the same host as
    # "api.openai.com"), so a fully-qualified spelling cannot bypass the guard (hy-yts5j).
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme.lower() != "https" or host != _VENDOR_OPENAI_HOST:
        return False
    try:
        port = parts.port
    except ValueError:
        return False
    return port is None or port == 443


def _openai_uses_vendor_path() -> bool:
    """True when the OpenAI provider resolves to the real OpenAI endpoint (the
    Responses branch of `_build_agent_model`) rather than a compatible gateway.
    The two branches cap differently, so callers that report a budget must know
    which one is live (GitHub #325)."""
    return _is_vendor_openai_base_url(_openai_base_url())


# The set of model ids the vendor OpenAI endpoint reports from GET /models, cached per
# base_url so a chat does not re-probe. Only a POSITIVE list is cached; a failed probe is
# never cached. Caching the positive is load-bearing (hy-ubd6t r2): once a model has been
# verified hosted, a later TRANSIENT /models failure returns the cached list rather than
# None, so a blip cannot reject a setup already proven to work.
_VENDOR_MODELS_CACHE: dict[str, set[str]] = {}


def _vendor_hosted_model_ids(base_url: str, api_key: str) -> set[str] | None:
    """The set of model ids the configured provider's GET /models actually reports, or
    None when the list cannot be determined (network/auth/parse failure).

    This replaces the old hard-coded family marker (hy-ubd6t): whether the vendor OpenAI
    endpoint hosts a model is an account fact, not a name pattern -- some accounts DO host
    `gpt-5.6-luna` on `api.openai.com`, so a marker-based reject was a false reject. The
    caller requires a DEFINITIVE POSITIVE result before the vendor path: a definitive list
    that omits the model fails closed (not hosted), and a None (could-not-verify) ALSO fails
    closed rather than proceeding -- a fail-OPEN on unknown let an unreachable/unauth /models
    send the model to the provider anyway (hy-ubd6t r2, dual-block). A successful list is
    cached, so a transient failure AFTER a verified success returns the cached list, not
    None; a failure is never cached, so a first-call blip does not poison later calls."""
    cached = _VENDOR_MODELS_CACHE.get(base_url)
    if cached is not None:
        return cached
    req = request.Request(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"})
    try:
        with request.urlopen(req, timeout=5) as response:
            if not 200 <= response.status < 300:
                return None
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 -- a failed probe is 'could not verify', never cached
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    ids = {str(entry.get("id")) for entry in data if isinstance(entry, dict) and entry.get("id")}
    _VENDOR_MODELS_CACHE[base_url] = ids
    return ids


def _effective_output_cap(settings) -> int:
    """The completion-token cap actually applied to this run, read off the built
    `ModelSettings` so it always matches the branch `_build_agent_model` chose
    (GitHub #325). The vendor Responses path carries it as `max_tokens`; the
    compatible gateway carries it as `extra_body['max_completion_tokens']`.
    Reading the built settings avoids re-deriving -- and mis-deriving -- the cap:
    the vendor path really sends 1024, not the larger configurable gateway cap."""
    max_tokens = getattr(settings, "max_tokens", None)
    if max_tokens:
        return int(max_tokens)
    extra_body = getattr(settings, "extra_body", None) or {}
    cap = extra_body.get("max_completion_tokens")
    return int(cap) if cap else 0


def _budget_exhausted(final, budget: int) -> bool:
    """True when a run returned no visible text after the FINAL completion request
    spent its whole output budget -- typically a reasoning model whose invisible
    reasoning tokens ate the cap (GitHub #325).

    The cap is per completion request, and `RunResult.raw_responses` holds one
    `ModelResponse` per agent turn, so only the last response's usage is compared
    to the budget; summing across turns would flag a normal multi-turn run whose
    final response never hit its cap."""
    if budget <= 0:
        return False
    responses = getattr(final, "raw_responses", None) or []
    if not responses:
        return False
    usage = getattr(responses[-1], "usage", None)
    spent = int(getattr(usage, "output_tokens", 0) or 0)
    return spent >= budget


def _budget_remediation() -> str:
    """Endpoint-specific hint for an exhausted completion budget, since the cap and
    the env var that controls it differ per branch (GitHub #325). The vendor
    Responses path caps at a fixed 1024 that NO env var raises, so it points only
    at lowering the reasoning effort; a compatible gateway's cap is configurable."""
    if _openai_uses_vendor_path():
        # HYPERSET_OPENAI_MAX_COMPLETION_TOKENS does not move the vendor cap.
        return "Lower HYPERSET_OPENAI_REASONING_EFFORT, then retry."
    return (
        "Raise HYPERSET_OPENAI_MAX_COMPLETION_TOKENS or lower "
        "HYPERSET_OPENAI_REASONING_EFFORT, then retry."
    )


def _analytics_db_url() -> str:
    explicit = os.environ.get("HYPERSET_ANALYTICS_DB_URL")
    if explicit:
        return explicit
    port = os.environ.get("ANALYTICS_DB_PORT_HOST", "55434")
    user = os.environ.get("ANALYTICS_DB_USER", "analytics")
    password = os.environ.get("ANALYTICS_DB_PASSWORD", "analytics-local-dev")
    name = os.environ.get("ANALYTICS_DB_NAME", "analytics")
    return f"postgresql://{user}:{password}@127.0.0.1:{port}/{name}"


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    timeout: int = 8,
    headers: dict | None = None,
):
    body = json.dumps(payload).encode() if payload is not None else None
    request_headers: dict[str, str] = (
        {"Content-Type": "application/json"} if body is not None else {}
    )
    if headers:
        request_headers.update(headers)
    req = request.Request(
        url,
        data=body,
        headers=request_headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            try:
                value = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                value = {"text": raw}
            return response.status, value
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            value = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            value = {"text": raw}
        return exc.code, value
    except Exception as exc:  # noqa: BLE001 - status should show unavailable, not crash the UI
        return None, {"error": str(exc)}


def _service_probe(name: str, base_url: str, *, version: str | None = None) -> dict:
    status, payload = _http_json(f"{base_url}/health")
    result = {
        "name": name,
        "endpoint": base_url,
        "status": "connected" if status and 200 <= status < 300 else "offline",
        "http_status": status,
        "version": version,
    }
    if isinstance(payload, dict) and payload.get("error"):
        result["detail"] = payload["error"]
    return result


def _openai_status() -> dict:
    # Report the model the serving path will actually use. A legacy env override must not
    # make the readiness card claim that an arbitrary model is live when the MVP is pinned.
    model = DEFAULT_OPENAI_MODEL
    base_url = _openai_base_url()
    provider = "OpenRouter" if "openrouter.ai" in base_url else "OpenAI"
    reasoning_effort = openai_reasoning_effort() or DEFAULT_OPENAI_REASONING_EFFORT
    api_key = openai_api_key()
    authenticated = False
    if api_key:
        auth_path = "/auth/key" if provider == "OpenRouter" else "/models"
        auth_request = request.Request(
            f"{base_url}{auth_path}", headers={"Authorization": f"Bearer {api_key}"}
        )
        try:
            with request.urlopen(auth_request, timeout=5) as response:
                authenticated = 200 <= response.status < 300
        except Exception:  # noqa: BLE001 -- status reports refusal without leaking provider detail
            authenticated = False
    return {
        "name": "Hosted model API",
        "provider": provider,
        "endpoint": f"{base_url}/responses",
        "status": "connected" if authenticated else "offline",
        "version": model,
        "reasoning_effort": reasoning_effort,
        "detail": (
            f"{provider} key authenticated server-side · reasoning {reasoning_effort}"
            if authenticated
            else (
                f"{provider} rejected the server-side key"
                if api_key
                else "set OPENAI_API_KEY for the default model path"
            )
        ),
    }


def _analytics_schema() -> dict:
    if psycopg is None:
        return {"status": "unavailable", "tables": [], "detail": "psycopg is not installed"}
    try:
        with psycopg.connect(_analytics_db_url(), connect_timeout=3) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name, column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'public' "
                    "ORDER BY table_name, ordinal_position"
                )
                tables: dict[str, list[dict]] = {}
                for table_name, column_name, data_type in cursor.fetchall():
                    tables.setdefault(table_name, []).append(
                        {"name": column_name, "type": data_type}
                    )
                cursor.execute("SELECT current_database(), version()")
                database, engine_version = cursor.fetchone()
        return {
            "status": "connected",
            "database": database,
            "version": engine_version,
            "tables": [{"name": name, "columns": columns} for name, columns in tables.items()],
        }
    except Exception as exc:  # noqa: BLE001 - the UI needs a visible source failure
        return {"status": "offline", "tables": [], "detail": str(exc)}


def _validate_demo_sql(sql: str) -> str:
    sql = str(sql or "").strip()
    if len(sql.encode()) > MAX_DEMO_SQL_BYTES:
        raise ValueError(f"SQL exceeds the {MAX_DEMO_SQL_BYTES}-byte demo limit")
    if sql.endswith(";"):
        sql = sql[:-1].rstrip()
    if not sql or ";" in sql or not re.match(r"^(?:select|with)\b", sql, re.IGNORECASE):
        raise ValueError("only one SELECT or WITH statement is allowed")
    if "--" in sql or "/*" in sql or "*/" in sql or _FORBIDDEN_SQL.search(sql):
        raise ValueError("only read-only SELECT/WITH SQL is allowed")
    return sql


def _question_requests_sql(question: str) -> bool:
    """Only expose SQL for explicit data-result questions, not source/rules questions."""
    if _SOURCE_RULES_QUESTION.search(question):
        return False
    return bool(_DATA_QUESTION.search(question))


def _run_demo_sql(sql: str) -> dict:
    sql = _validate_demo_sql(sql)
    if psycopg is None:
        raise RuntimeError("psycopg is not installed")
    with psycopg.connect(_analytics_db_url(), connect_timeout=3) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '5s'")
                cursor.execute(sql)
                columns = [item.name for item in cursor.description or []]
                rows = [
                    {column: _json_value(value) for column, value in zip(columns, row, strict=True)}
                    for row in cursor.fetchmany(MAX_DEMO_ROWS + 1)
                ]
    return {
        "sql": sql,
        "columns": columns,
        "rows": rows[:MAX_DEMO_ROWS],
        "row_count": len(rows[:MAX_DEMO_ROWS]),
        "truncated": len(rows) > MAX_DEMO_ROWS,
    }


def _demo_status() -> dict:
    hyperset_base = _upstream_base_url()
    _, health = _http_json(f"{hyperset_base}/v0/health")
    _, operator = _http_json(f"{hyperset_base}/v0/playground/status")

    superset = _service_probe(
        "Superset",
        _base_url("HYPERSET_SUPERSET_BASE_URL", DEFAULT_SUPERSET_BASE_URL),
        version=os.environ.get("HYPERSET_SUPERSET_VERSION", DEFAULT_SUPERSET_VERSION),
    )
    _, openapi = _http_json(f"{superset['endpoint']}/api/v1/_openapi")
    if isinstance(openapi, dict):
        superset["api_version"] = (openapi.get("info") or {}).get("version")

    datahub = _service_probe(
        "DataHub",
        _base_url("HYPERSET_DATAHUB_BASE_URL", DEFAULT_DATAHUB_BASE_URL),
        version=os.environ.get("HYPERSET_DATAHUB_VERSION", DEFAULT_DATAHUB_VERSION),
    )
    _, datahub_version = _http_json(
        f"{datahub['endpoint']}/api/graphql",
        method="POST",
        payload={"query": "{ appConfig { appVersion } }"},
    )
    actual_version = (
        ((datahub_version.get("data") or {}).get("appConfig") or {}).get("appVersion")
        if isinstance(datahub_version, dict)
        else None
    )
    if actual_version:
        datahub["version"] = actual_version

    connector_rows = (
        {
            row.get("connector_type"): row
            for row in (operator.get("connectors") or [])
            if isinstance(row, dict)
        }
        if isinstance(operator, dict)
        else {}
    )
    for service, connector_type in ((superset, "superset"), (datahub, "datahub")):
        service["hyperset_connection"] = connector_rows.get(connector_type)

    return {
        "hyperset": {
            "status": "connected" if health.get("status") == "ok" else "offline",
            "version": (operator.get("hyperset") or {}).get("version")
            if isinstance(operator, dict)
            else None,
            "schema_version": health.get("schema_version"),
            "endpoint": hyperset_base,
            "operator": operator,
        },
        "superset": superset,
        "datahub": datahub,
        "openai": _openai_status(),
        "analytics_db": _analytics_schema(),
        "playground": _playground_runtime_config(),
    }


def _context_catalog() -> list[dict]:
    """Return the exact governed catalog available to the playground selector."""
    base_url = _upstream_base_url()
    status, payload = _http_json(
        f"{base_url}/v0/list_context_catalog",
        method="POST",
        payload={"limit": 50, "offset": 0},
    )
    if not status or status >= 400:
        detail = payload.get("error") if isinstance(payload, dict) else None
        raise RuntimeError(detail or f"context catalog returned HTTP {status or 'no response'}")
    domains = payload.get("domains") if isinstance(payload, dict) else None
    if not isinstance(domains, list):
        raise RuntimeError("context catalog response did not include domains")
    return [entry for entry in domains if isinstance(entry, dict) and entry.get("domain")]


def _build_agent_model(provider: str, model: str):
    """Build the Agents SDK model for the one supported MVP runtime: OpenAI Luna."""
    from agents import (
        AsyncOpenAI,
        ModelSettings,
        OpenAIChatCompletionsModel,
        OpenAIResponsesModel,
    )
    from openai.types.shared import Reasoning

    provider = str(provider or "").strip().lower()
    if provider != "openai":
        raise ValueError("the MVP runtime supports OpenAI only; configure an OpenAI model")
    if model != DEFAULT_OPENAI_MODEL:
        raise ValueError(
            f"the MVP runtime only supports {DEFAULT_OPENAI_MODEL}; configure that model"
        )
    allowed_models = {item["value"] for item in _configured_models()}
    if model not in allowed_models:
        raise ValueError(
            f"model {model!r} is not configured; select one of the published OpenAI models"
        )

    if provider == "openai":
        api_key = openai_api_key()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured for the OpenAI model path")
        base_url = _openai_base_url()
        if not is_safe_openai_endpoint(base_url):
            raise RuntimeError(
                "the OpenAI runtime refuses local or Ollama-compatible endpoints; "
                "use an HTTPS OpenAI-compatible endpoint"
            )
        # On the vendor OpenAI endpoint, require a DEFINITIVE POSITIVE capability result
        # before any provider call (hy-ubd6t): confirm the model against the provider's real
        # GET /models rather than a hard-coded family marker (which false-rejected
        # `gpt-5.6-luna` for accounts whose vendor endpoint DOES host it). Fail closed BEFORE
        # constructing the client in BOTH failure cases, so no request ever leaves the box on
        # an unverified model: a definitive list that OMITS the model (not hosted), and a
        # could-not-verify result (unreachable/unauth/malformed /models) -- proceeding on the
        # latter was a fail-OPEN that let the model reach the provider anyway (hy-ubd6t r2,
        # dual-block). A verified-hosted model is cached, so a transient /models blip after a
        # success does not flip to could-not-verify. The two messages are DISTINCT and
        # actionable (not a 6s opaque 401/404); the caller wraps them into a redacted
        # `context_discovery_provider_error` for the UI.
        if _is_vendor_openai_base_url(base_url):
            hosted = _vendor_hosted_model_ids(base_url, api_key)
            if hosted is None:
                raise RuntimeError(
                    f"could not verify {model!r} is hosted at {base_url}: the vendor's model "
                    f"list (GET /models) was unreachable, unauthorized, or unreadable. Retry, "
                    f"check the endpoint and OPENAI_API_KEY, or set HYPERSET_OPENAI_BASE_URL "
                    f"to your gateway."
                )
            if model not in hosted:
                raise RuntimeError(
                    f"model {model!r} is not hosted by the vendor OpenAI endpoint "
                    f"({DEFAULT_OPENAI_BASE_URL}) -- its GET /models does not list it. Set "
                    f"HYPERSET_OPENAI_BASE_URL to a gateway that serves {model!r}, or choose "
                    f"a vendor-hosted model."
                )
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        # `reasoning=` is an OpenAI Responses-API setting. Only the real OpenAI
        # endpoint gets the Responses model AND a `reasoning=` setting; any other
        # base_url is an OpenAI-COMPATIBLE gateway that speaks chat completions,
        # which rejects the `reasoning=` param and then returns no answer for a
        # reasoning model (GitHub #325). Branch the SETTINGS on base_url exactly as
        # the model class already is, so the two never mismatch -- and with them
        # the token cap's WIRE FIELD: the Responses model maps `max_tokens` to
        # `max_output_tokens`, while chat completions must send
        # `max_completion_tokens` (plain `max_tokens` is rejected for a reasoning
        # model on that endpoint, and covers only visible output on a plain one).
        #
        # The branch is by URL, not model capability: this path assumes the
        # default OpenAI URL fronts a reasoning-capable model (it takes the
        # Responses model + `reasoning=`), and the gateway URL fronts whatever the
        # operator wired -- reasoning or not -- which is why the gateway branch
        # sends no `reasoning=` and only the neutral `max_completion_tokens`. A
        # non-reasoning model behind the default OpenAI URL would still receive
        # `reasoning=`; keep that URL pointed only at a reasoning-capable model.
        if _is_vendor_openai_base_url(base_url):
            # Vendor-default path: kept byte-identical to the pre-#325 code, so the
            # cap here stays 1024 and is NOT the larger gateway budget.
            sdk_model = OpenAIResponsesModel(model=model, openai_client=client)
            settings = ModelSettings(
                reasoning=Reasoning(
                    effort=openai_reasoning_effort() or DEFAULT_OPENAI_REASONING_EFFORT
                ),
                max_tokens=DEFAULT_OPENAI_RESPONSES_MAX_TOKENS,
            )
        else:
            sdk_model = OpenAIChatCompletionsModel(model=model, openai_client=client)
            # No `max_tokens` and no `reasoning`: send `max_completion_tokens`
            # through the request body so the wire field is right whether the
            # gateway fronts a reasoning or a non-reasoning model. The cap counts
            # invisible reasoning tokens, so it is the larger configurable budget,
            # not the old 1024 that a reasoning model spent entirely on thinking.
            settings = ModelSettings(
                extra_body={"max_completion_tokens": _openai_max_completion_tokens()}
            )
        return sdk_model, settings
    raise AssertionError("unreachable: the OpenAI branch is the only supported runtime")


async def _close_agent_client(agent) -> None:
    client = getattr(getattr(agent, "model", None), "_client", None)
    if client is not None:
        await client.close()


def _run_agent_sync(agent, prompt: str, *, max_turns: int):
    """Run one non-streaming agent and close its client before its loop."""
    from agents import Runner

    async def run():
        try:
            return await Runner.run(agent, prompt, max_turns=max_turns)
        finally:
            await _close_agent_client(agent)

    return asyncio.run(run())


def _run_agent_with_budget(agent, prompt: str, *, on_event=None):
    from agents import Runner

    async def run():
        try:
            if on_event is None:
                return await asyncio.wait_for(
                    Runner.run(agent, prompt, max_turns=AGENT_MAX_TURNS),
                    timeout=AGENT_TIMEOUT_SECONDS,
                )
            streamed = Runner.run_streamed(agent, prompt, max_turns=AGENT_MAX_TURNS)
            try:
                async for event in streamed.stream_events():
                    on_event(event)
            except BaseException:
                # The client disconnected (on_event's emit raised) or the run was
                # cancelled. Cancel the run and yield so the SDK's internal tasks
                # process the cancellation and close the upstream connection before
                # this loop is torn down.
                streamed.cancel()
                await asyncio.sleep(0)
                raise
            return streamed
        finally:
            await _close_agent_client(agent)

    try:
        return asyncio.run(run())
    except TimeoutError as exc:
        raise TimeoutError(
            f"agent harness exceeded its {AGENT_TIMEOUT_SECONDS}-second execution timeout"
        ) from exc


def _run_context_selector(payload: dict) -> dict:
    """Use the calling model to map a question to exact catalog directive values."""
    from agents import Agent, set_tracing_disabled
    from pydantic import BaseModel, Field

    provider = str(payload.get("provider") or "openai").strip().lower()
    default_model = _configured_default_model(provider)
    model = str(payload.get("model") or default_model).strip()
    question = str(payload.get("question") or "").strip()
    if not question:
        raise ValueError("question is required")
    catalog = _context_catalog()

    class ContextSelection(BaseModel):
        domains: list[str] = Field(default_factory=list)
        concepts: list[str] = Field(default_factory=list)
        asset_refs: list[str] = Field(default_factory=list)
        rationale: str = ""

    set_tracing_disabled(True)
    try:
        # A missing/invalid credential or unsupported config raises here before any
        # model call; it is a provider/config fault, not a catalog outcome (#344).
        sdk_model, settings = _build_agent_model(provider, model)
    except Exception as exc:  # noqa: BLE001 -- classified as a provider/config fault
        raise PlaygroundProviderError(_provider_error_payload(provider, model, exc)) from exc
    selector = Agent(
        name="hyperset-playground-context-selector",
        model=sdk_model,
        model_settings=settings,
        output_type=ContextSelection,
        instructions=(
            "Interpret the user's analytics question and select the governed context needed "
            "to answer it. Return only exact values from the supplied catalog; never invent a "
            "domain, concept, or asset ref. Select every matching domain needed, but prefer "
            "the smallest useful set. Return empty arrays when the catalog does not cover the "
            "question. Semantic interpretation belongs here in the calling agent; the governed "
            "context API will receive your exact structured directive next."
        ),
    )
    catalog_for_model = [
        {
            "domain": entry.get("domain"),
            "title": entry.get("title"),
            "concepts": entry.get("concepts") or [],
            "approved_source_refs": entry.get("approved_source_refs") or [],
            "prohibited_source_refs": entry.get("prohibited_source_refs") or [],
            "evidence_refs": entry.get("evidence_refs") or [],
        }
        for entry in catalog
    ]
    prompt = (
        f"Question: {question}\n\nAvailable governed context catalog:\n"
        f"{json.dumps(catalog_for_model, indent=2)}"
    )
    started = time.perf_counter()
    try:
        # The provider/model call itself: a 4xx (bad key, unsupported param, unknown
        # model), an auth or connectivity failure, or a non-converging run is a
        # provider/model fault, NOT the catalog failing to yield a directive (#344).
        result = _run_agent_sync(selector, prompt, max_turns=2)
    except PlaygroundProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 -- classified as a provider/model fault
        raise PlaygroundProviderError(_provider_error_payload(provider, model, exc)) from exc
    output = result.final_output
    selection = output.model_dump() if hasattr(output, "model_dump") else {}
    allowed_domains = {entry["domain"] for entry in catalog_for_model}
    allowed_concepts = {concept for entry in catalog_for_model for concept in entry["concepts"]}
    allowed_assets = {ref for entry in catalog_for_model for ref in entry["evidence_refs"]}
    domains = [value for value in selection.get("domains", []) if value in allowed_domains]
    concepts = [value for value in selection.get("concepts", []) if value in allowed_concepts]
    asset_refs = [value for value in selection.get("asset_refs", []) if value in allowed_assets]
    for entry in catalog:
        if entry.get("domain") not in domains and any(
            concept in concepts for concept in entry.get("concepts") or []
        ):
            domains.append(entry["domain"])
    return {
        "provider": provider,
        "model": model,
        "question": question,
        "mode": "select",
        "directive": {"domains": domains, "concepts": concepts, "asset_refs": asset_refs},
        "rationale": str(selection.get("rationale") or ""),
        "catalog_domains": [entry["domain"] for entry in catalog_for_model],
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }


def _normalise_agent_config(payload: dict, profile: dict, known_domains: set[str]) -> dict:
    """Turn a builder draft into a bounded policy the harness can enforce."""
    raw = payload.get("agent_config")
    if not isinstance(raw, dict):
        raw = {}

    def values(key: str, allowed: set[str]) -> list[str]:
        candidate = raw.get(key)
        if not isinstance(candidate, list):
            return []
        return sorted({str(value).strip() for value in candidate if str(value).strip() in allowed})

    system_prompt = str(raw.get("system_prompt") or profile.get("instruction") or "").strip()
    return {
        "key": str(raw.get("key") or "custom-agent").strip()[:80],
        "label": str(raw.get("label") or "Custom agent").strip()[:120],
        "system_prompt": system_prompt[:6000],
        "allowed_connections": values(
            "allowed_connections", {"superset", "datahub", "analytics_db"}
        ),
        "denied_connections": values("denied_connections", {"superset", "datahub", "analytics_db"}),
        "allowed_tools": values("allowed_tools", {"run_read_only_sql"}),
        "denied_tools": values("denied_tools", {"run_read_only_sql"}),
        "allowed_domains": values("allowed_domains", known_domains),
        "denied_domains": values("denied_domains", known_domains),
        "favored_domains": values("favored_domains", known_domains),
    }


def _run_agent_harness(payload: dict, *, stream_callback=None) -> dict:
    """Run the demo through the OpenAI Agents SDK and one read-only SQL tool."""
    from agents import Agent, FunctionTool, set_tracing_disabled

    # SDK tracing would send the governed bundle to a second endpoint. The
    # browser already receives the complete local harness result, so that
    # duplicate disclosure is both unnecessary and surprising.
    set_tracing_disabled(True)
    provider = str(payload.get("provider") or "openai").strip().lower()
    default_model = _configured_default_model(provider)
    model = str(payload.get("model") or default_model).strip()
    question = str(payload.get("question") or "").strip()
    profiles = _configured_agent_profiles()
    runtime_config = _playground_runtime_config()
    default_agent = runtime_config.get("default_agent") or ""
    agent_key = str(payload.get("agent") or default_agent).strip().lower()
    profile = profiles.get(agent_key)
    if profile is None and isinstance(payload.get("agent_config"), dict):
        profile = {
            "label": "Custom agent",
            "description": "Built from the current evaluator draft.",
            "instruction": "Answer using the available governed context.",
        }
        agent_key = agent_key or "custom-agent"
    if profile is None:
        raise ValueError(
            "no playground agent is configured; set HYPERSET_PLAYGROUND_AGENTS_JSON "
            "for this deployment"
        )
    governed_only = bool(payload.get("governed_only"))
    pinned_facts = _attachment_facts(_normalise_attachments(payload.get("attachments")))
    requested_mode = str(payload.get("mode") or "raw").strip().lower()
    if requested_mode == "select":
        return _run_context_selector(payload)
    mode = "governed" if requested_mode == "governed" else "raw"
    if not question:
        raise ValueError("question is required")
    source_snapshot = _demo_status()
    schema = source_snapshot["analytics_db"]
    source_catalog = {
        "superset": {
            key: source_snapshot["superset"].get(key)
            for key in ("status", "version", "endpoint", "api_version")
        },
        "datahub": {
            key: source_snapshot["datahub"].get(key) for key in ("status", "version", "endpoint")
        },
        "analytics_db": {
            "status": schema.get("status"),
            "database": schema.get("database"),
            "tables": schema.get("tables", []),
        },
    }
    original_bundle = payload.get("bundle") if requested_mode == "governed" else None
    catalog_domains = {
        str(domain).strip()
        for domain in (payload.get("catalog_domains") or [])
        if str(domain).strip()
    }
    if not catalog_domains and isinstance(original_bundle, dict):
        catalog_domains = {
            str(domain).strip()
            for domain in ((original_bundle.get("request") or {}).get("directive") or {}).get(
                "domains"
            )
            or []
            if str(domain).strip()
        }
    agent_config = _normalise_agent_config(payload, profile, catalog_domains)
    if not payload.get("agent_config"):
        agent_config["key"] = agent_key
        agent_config["label"] = profile["label"]
        agent_config["system_prompt"] = profile.get("instruction") or ""
        agent_config["allowed_connections"] = ["analytics_db", "datahub", "superset"]
        agent_config["allowed_tools"] = ["run_read_only_sql"]

    bundle = original_bundle
    selection_trace = (
        payload.get("selection_trace") if requested_mode in {"governed", "discover"} else None
    )
    resolution_error = payload.get("resolution_error")
    context_source = "provided" if bundle else "none"
    if requested_mode == "discover" and bundle is None:
        context_source = "agent_discovered"
        try:
            discovery = _run_context_selector({**payload, "agent_config": agent_config})
            selection_trace = discovery
            directive = dict(discovery.get("directive") or {})
            discovered_domains = [
                str(value).strip() for value in directive.get("domains") or [] if str(value).strip()
            ]
            if agent_config["allowed_domains"]:
                discovered_domains = [
                    value
                    for value in discovered_domains
                    if value in set(agent_config["allowed_domains"])
                ]
            discovered_domains = [
                value
                for value in discovered_domains
                if value not in set(agent_config["denied_domains"])
            ]
            directive["domains"] = discovered_domains
            selection_trace["directive"] = directive
            bundle = _resolve_playground_bundle(
                question, directive, trace=_playground_trace(payload, question)
            )
        except PlaygroundProviderError as exc:
            # Provider/model-call fault on the discovery call: attribute to the
            # provider, not the corpus, and do not enter the downgrade under a
            # governance-sounding label (GitHub #344).
            context_source = "discovery_provider_error"
            resolution_error = exc.error_payload
        except PlaygroundResolutionError as exc:
            context_source = "discovery_failed"
            resolution_error = exc.error_payload
        except Exception as exc:  # noqa: BLE001 - discovery failure must remain evaluable
            context_source = "discovery_failed"
            resolution_error = {
                "code": "context_discovery_failed",
                "message": "The agent could not discover a usable governed context bundle.",
                "recovery": (
                    "The agent will continue without governed context and label the answer "
                    "accordingly."
                ),
                "detail": str(exc),
            }
        mode = "governed" if bundle else "raw"
    resolved_domains = {
        str(domain).strip()
        for domain in (((bundle or {}).get("request") or {}).get("directive") or {}).get("domains")
        or []
        if str(domain).strip()
    }
    context_policy = "allowed"
    allowed_domains = set(agent_config["allowed_domains"])
    denied_domains = set(agent_config["denied_domains"])
    if bundle and (
        (allowed_domains and not resolved_domains.issubset(allowed_domains))
        or resolved_domains & denied_domains
    ):
        bundle = None
        context_policy = "denied_by_agent_context_policy"
    elif not bundle and (mode == "governed" or requested_mode == "discover"):
        context_policy = "no_bundle_resolved"

    # The streaming path resolves context in a first stage, then calls this harness
    # with mode="governed" and the discovery outcome passed in. A provider fault
    # there arrives as a resolution_error, but re-derivation above would relabel the
    # source "none" and drop the attribution. Honor the passed provider-fault code so
    # the terminal result keeps naming the provider, not the corpus (GitHub #344).
    if (
        bundle is None
        and isinstance(resolution_error, dict)
        and resolution_error.get("code") == "context_discovery_provider_error"
    ):
        context_source = "discovery_provider_error"

    allowed_connections = set(agent_config["allowed_connections"]) or {
        "superset",
        "datahub",
        "analytics_db",
    }
    denied_connections = set(agent_config["denied_connections"])
    visible_connections = allowed_connections - denied_connections
    source_catalog = {
        key: value for key, value in source_catalog.items() if key in visible_connections
    }
    allowed_tools = set(agent_config["allowed_tools"]) or {"run_read_only_sql"}
    allowed_tools -= set(agent_config["denied_tools"])
    if "analytics_db" not in visible_connections:
        # The only current SQL tool executes against the analytics warehouse;
        # hiding that connection also removes the dependent capability.
        allowed_tools.discard("run_read_only_sql")
    model_bundle = None
    if bundle:
        model_bundle = {
            "bundle_id": bundle.get("bundle_id"),
            "resolution": {
                "status": (bundle.get("resolution") or {}).get("status"),
                "summary": (bundle.get("resolution") or {}).get("summary"),
                "warnings": [
                    warning.get("code")
                    for warning in (bundle.get("resolution") or {}).get("warnings", [])
                    if isinstance(warning, dict) and warning.get("code")
                ],
            },
            "context_authority": {
                key: (bundle.get("context_authority") or {}).get(key)
                for key in ("type", "path", "commit_sha", "context_snapshot_id")
            },
            "instructions": bundle.get("instructions") or {},
            "provenance_refs": bundle.get("provenance_refs") or [],
            "deprecations": (bundle.get("linked_evidence") or {}).get("deprecations") or [],
        }
    policy_context = {
        "context_policy": context_policy,
        "resolved_domains": sorted(resolved_domains),
        "allowed_domains": agent_config["allowed_domains"],
        "denied_domains": agent_config["denied_domains"],
        "favored_domains": agent_config["favored_domains"],
        "visible_connections": sorted(visible_connections),
        "allowed_tools": sorted(allowed_tools),
    }
    context = (
        "Governed Hyperset ContextBundle follows. Use its definitions, approved and prohibited "
        "sources, filters, joins, grain, caveats, and validations as the authority.\n"
        + json.dumps(model_bundle, indent=2)
        if bundle
        else (
            (
                "A resolved ContextBundle was withheld by this agent's context policy. "
                if context_policy == "denied_by_agent_context_policy"
                else "No Hyperset ContextBundle was resolved for this question. "
            )
            + "Use only the raw source "
            "metadata and the SQL result; do not invent governed meaning. Say explicitly that "
            "the answer is not backed by a governed bundle. Resolution details follow:\n"
            + json.dumps(payload.get("resolution_error") or {}, indent=2)
        )
    )
    started = time.perf_counter()
    tool_calls: list[dict] = []

    async def run_read_only_sql(_tool_context, arguments: str) -> str:
        params = json.loads(arguments or "{}")
        sql = params.get("sql")
        try:
            result = _run_demo_sql(sql)
            if isinstance(result, dict) and "sql" not in result:
                result = {"sql": sql, **result}
        except Exception as exc:  # the SDK returns this result so the agent can repair its call
            result = {"sql": sql, "error": str(exc)}
        tool_calls.append(result)
        if stream_callback is not None:
            stream_callback({"type": "sql", "result": result})
        return json.dumps(result, default=str)

    sql_tool = FunctionTool(
        name="run_read_only_sql",
        description=(
            "Run one read-only SELECT or WITH query against the demo analytics database. "
            "Use only exact tables and columns from the supplied source catalog."
        ),
        params_json_schema={
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
            "additionalProperties": False,
        },
        on_invoke_tool=run_read_only_sql,
    )
    # A provider/config fault can surface only now (e.g. a missing credential that
    # _build_agent_model rejects). It must not crash the streamed turn: capture it,
    # skip the model call, and answer with a labelled provider-fault result so the
    # terminal `done` still carries the right source (GitHub #344).
    answer_provider_error = None
    sdk_model = settings = None
    try:
        sdk_model, settings = _build_agent_model(provider, model)
    except Exception as exc:  # noqa: BLE001 -- a provider/config fault must not crash the turn
        answer_provider_error = _provider_error_payload(provider, model, exc)
    sql_requested = _question_requests_sql(question)
    if settings is not None and sql_requested and "run_read_only_sql" in allowed_tools:
        # Ask compatible providers to call the only available tool for data
        # questions; weaker providers may still treat this as advisory.
        settings.tool_choice = "required"

    final = None
    if answer_provider_error is None:
        tools = [sql_tool] if "run_read_only_sql" in allowed_tools else []
        agent = Agent(
            name=f"hyperset-playground-{agent_key}",
            model=sdk_model,
            model_settings=settings,
            instructions=(
                "Answer analytics questions using the supplied source catalog and ContextBundle. "
                + (
                    "The read-only SQL tool is available on every run when policy allows it. "
                    if tools
                    else "No SQL tool is available on this run. "
                )
                + "For explicit numeric, total, "
                "count, breakdown, or other data-result questions, call run_read_only_sql before "
                "answering; if the first SQL fails, correct it and retry. For questions asking "
                "which source, rules, definitions, filters, joins, or evidence to use, do not call "
                "the SQL tool and do not include SQL syntax or a SQL code block; answer from the "
                "governed context in plain language. DataHub is metadata-only and does not execute "
                "SQL. Use exact catalog table and column names. State the SQL and "
                "important filters "
                "briefly only after a data-result query. Distinguish governed rules "
                "from raw observed "
                "data. The playground harness, not Hyperset, executes SQL; never claim Hyperset "
                "executed or validated the result."
                + (
                    "Governed-only mode: use only the governed ContextBundle and its "
                    "approved sources; "
                    "do not use raw source metadata outside the bundle. "
                    if governed_only
                    else ""
                )
                + f" Agent policy: {json.dumps(policy_context, sort_keys=True)}\n"
                f" Builder system prompt: {agent_config['system_prompt']}\n"
                f" {profile['instruction']}"
            ),
            tools=tools,
        )
        history = payload.get("history") or []
        prior_turns = [
            {
                "role": item.get("role"),
                "content": str(item.get("content") or "")[:4000],
            }
            for item in history[-8:]
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"}
        ]
        conversation = (
            "Prior conversation (use it only to resolve references in the latest question):\n"
            f"{json.dumps(prior_turns, indent=2)}\n\n"
            if prior_turns
            else ""
        )
        pinned_block = (
            "User-pinned governed context that MUST be referenced in the answer:\n"
            + "\n".join(pinned_facts)
            + "\n\n"
            if pinned_facts
            else ""
        )
        catalog_for_prompt = {} if governed_only else source_catalog
        prompt = (
            f"{conversation}{pinned_block}Question: {question}\n\nSource status and catalog:\n"
            f"{json.dumps(catalog_for_prompt, indent=2)}\n\n{context}"
        )
        try:
            final = _run_agent_with_budget(
                agent,
                prompt,
                on_event=(
                    (lambda event: stream_callback({"type": "agent_event", "event": event}))
                    if stream_callback is not None
                    else None
                ),
            )
        except Exception as exc:  # noqa: BLE001 -- a provider/model fault must not crash the turn
            answer_provider_error = _provider_error_payload(provider, model, exc)
    if answer_provider_error is not None:
        # The answer-phase model call faulted. Degrade to a labelled, non-crashing
        # result and attribute the cause to the provider, not the corpus (#344).
        answer = (
            f"The {provider} model provider failed the answer call, so no answer was produced. "
            + str(answer_provider_error.get("recovery") or "")
        ).strip()
        if not isinstance(resolution_error, dict):
            resolution_error = answer_provider_error
        if context_source != "discovery_provider_error":
            context_source = "answer_provider_error"
    else:
        # Read the cap off the settings actually built, so the vendor Responses path's
        # real 1024 is used here rather than the larger gateway budget (GitHub #325).
        budget = _effective_output_cap(settings)
        if final.final_output:
            answer = str(final.final_output)
        elif _budget_exhausted(final, budget):
            answer = (
                f"The model spent its entire completion-token budget ({budget} tokens) "
                "on reasoning and returned no visible answer. " + _budget_remediation()
            )
        else:
            answer = "The model returned no answer."
    successful_calls = [call for call in tool_calls if not call.get("error")]
    last_query = successful_calls[-1] if successful_calls else None
    trace = []
    if isinstance(resolution_error, dict):
        trace.append(
            {
                "kind": "resolution",
                "label": "UNRESOLVED",
                "title": "No governed bundle resolved",
                "detail": resolution_error.get("message")
                or "Context resolution did not return a bundle.",
                "data": resolution_error,
            }
        )
    if isinstance(selection_trace, dict):
        selected = selection_trace.get("directive") or {}
        trace.append(
            {
                "kind": "assist",
                "label": "ASSIST",
                "title": "Question mapped to governed context",
                "detail": selection_trace.get("rationale")
                or "The calling model selected exact catalog values before resolution.",
                "data": {
                    "domains": selected.get("domains") or [],
                    "concepts": selected.get("concepts") or [],
                    "asset_refs": selected.get("asset_refs") or [],
                    "model": selection_trace.get("model"),
                    "duration_ms": selection_trace.get("duration_ms"),
                },
            }
        )
    if bundle:
        request_data = bundle.get("request") or {}
        directive = request_data.get("directive") or {}
        authority = bundle.get("context_authority") or {}
        instructions = bundle.get("instructions") or {}
        evidence = bundle.get("linked_evidence") or {}
        trace.append(
            {
                "kind": "governed",
                "label": "GOVERNED",
                "title": "Pinned context bundle loaded",
                "detail": (
                    f"{bundle.get('bundle_id') or 'bundle'} · "
                    f"{authority.get('path') or 'Git context'} at "
                    f"{str(authority.get('commit_sha') or 'unknown')[:12]} · "
                    f"resolution {((bundle.get('resolution') or {}).get('status') or 'unknown')}"
                ),
                "data": {
                    "directive": directive,
                    "context_authority": authority,
                    "provenance_refs": bundle.get("provenance_refs") or [],
                },
            }
        )
        trace.append(
            {
                "kind": "governed",
                "label": "GOVERNED",
                "title": "Rules supplied to the agent",
                "detail": (
                    f"{len(instructions.get('definitions') or [])} definitions · "
                    f"{len(instructions.get('approved_sources') or [])} approved sources · "
                    f"{len(instructions.get('filters') or [])} filters · "
                    f"{len(instructions.get('joins') or [])} joins · "
                    f"{len(instructions.get('validations') or [])} validations"
                ),
                "data": {
                    "definitions": instructions.get("definitions") or [],
                    "approved_sources": instructions.get("approved_sources") or [],
                    "filters": instructions.get("filters") or [],
                    "joins": instructions.get("joins") or [],
                    "grain": instructions.get("grain"),
                    "validations": instructions.get("validations") or [],
                    "prohibited_sources": instructions.get("prohibited_sources") or [],
                },
            }
        )
        trace.append(
            {
                "kind": "observed",
                "label": "OBSERVED",
                "title": "Observed evidence and qualifiers reviewed",
                "detail": (
                    f"{len(evidence.get('observed_assets') or [])} observed assets · "
                    f"{len(evidence.get('freshness') or [])} freshness records · "
                    f"{len(evidence.get('conflicts') or [])} conflicts · "
                    f"{len(evidence.get('deprecations') or [])} deprecations"
                ),
                "data": {
                    "observed_assets": evidence.get("observed_assets") or [],
                    "freshness": evidence.get("freshness") or [],
                    "conflicts": evidence.get("conflicts") or [],
                    "deprecations": evidence.get("deprecations") or [],
                    "warnings": (bundle.get("resolution") or {}).get("warnings") or [],
                },
            }
        )
    else:
        trace.append(
            {
                "kind": "observed",
                "label": "OBSERVED",
                "title": "No governed bundle supplied",
                "detail": "The raw arm used source metadata and the read-only demo SQL tool only.",
                "data": {"context_included": False},
            }
        )
    for index, call in enumerate(tool_calls, start=1):
        trace.append(
            {
                "kind": "plan",
                "label": "PLAN",
                "title": f"Read-only SQL tool call {index}",
                "detail": (
                    f"{'Succeeded' if not call.get('error') else 'Failed'} · "
                    f"{len(call.get('rows') or [])} rows returned"
                ),
                "data": {
                    "sql": call.get("sql"),
                    "row_count": call.get("row_count", len(call.get("rows") or [])),
                    "error": call.get("error"),
                },
            }
        )
    execution = (bundle.get("execution") or {}) if bundle else {}
    trace.append(
        {
            "kind": "result",
            "label": "RESULT",
            "title": "Answer assembled",
            "detail": (
                f"{len(tool_calls)} read-only tool call(s) · "
                f"Hyperset executed SQL: {bool(execution.get('performed_by_hyperset'))} · "
                f"Hyperset validated results: {bool(execution.get('result_validated_by_hyperset'))}"
            ),
            "data": {
                "provider": provider,
                "model": model,
                "tool_call_count": len(tool_calls),
                "performed_by_hyperset": bool(execution.get("performed_by_hyperset")),
                "result_validated_by_hyperset": bool(execution.get("result_validated_by_hyperset")),
            },
        }
    )
    return {
        "provider": provider,
        "mode": requested_mode,
        "model": model,
        "reasoning_effort": (
            (openai_reasoning_effort() or DEFAULT_OPENAI_REASONING_EFFORT)
            if provider == "openai"
            else None
        ),
        "question": question,
        "agent": agent_key,
        "agent_label": agent_config["label"],
        "context_included": bool(bundle),
        "context_source": context_source,
        "agent_config": {
            **agent_config,
            "policy_result": context_policy,
            "effective_connections": sorted(visible_connections),
            "effective_tools": sorted(allowed_tools),
        },
        "bundle_id": bundle.get("bundle_id") if bundle else None,
        "context_resolution": {
            "status": "resolved" if bundle else "unresolved",
            "error": resolution_error,
        },
        "tool_access": {
            "superset": source_snapshot["superset"],
            "datahub": source_snapshot["datahub"],
            "analytics_db": schema,
        },
        "planner": {
            "runtime": "openai-agents-sdk",
            "tool": "run_read_only_sql" if sql_requested else None,
            "sql_requested": sql_requested,
            "tool_call_count": len(tool_calls),
            "tool_calls": tool_calls,
        },
        "sql": last_query.get("sql") if last_query else None,
        "query": last_query,
        "answer": answer,
        "trace": trace,
        "duration_ms": round((time.perf_counter() - started) * 1000),
    }


def _stream_text_delta(event) -> str:
    """Read text deltas from either Responses or Chat Completions stream events."""
    data = getattr(event, "data", event)
    if isinstance(data, dict):
        if isinstance(data.get("delta"), str):
            return data["delta"]
        choices = data.get("choices") or []
        if choices and isinstance((choices[0].get("delta") or {}).get("content"), str):
            return choices[0]["delta"]["content"]
        return ""
    delta = getattr(data, "delta", None)
    if isinstance(delta, str):
        return delta
    choices = getattr(data, "choices", None) or []
    if choices:
        choice_delta = getattr(choices[0], "delta", None)
        content = getattr(choice_delta, "content", None)
        if isinstance(content, str):
            return content
    return ""


# The header a hyperset session id is set under. The backend reads BOTH `mcp-session-id`
# and this one (interaction.SESSION_HEADERS); the playground labels its own trace with the
# hyperset-specific token so it never collides with a Streamable-HTTP transport session.
_HYPERSET_SESSION_HEADER = "x-hyperset-session-id"


def _playground_trace(payload: dict, question: str) -> dict:
    """The MCP interaction-trace linkage for one playground turn (hy-z7bsw).

    The durable `mcp_interaction_trace` groups a turn by `session_id`/`turn_id`; without
    these the live Luna rows land blank. The browser MAY supply a stable `session_id`
    (a conversation) and `turn_id` (this question) so multiple turns reconstruct as one
    chain; when it does not, a per-turn id is minted so the row is still linked rather than
    null. Client-supplied ids are validated to the SAME strict opaque-token shape the
    backend enforces (credential-bearing junk is dropped, never forwarded). `intent` is the
    caller's question -- free text, redacted where it persists on the server. `tool_call_id`
    is minted per backend call by `_resolve_playground_bundle`."""
    session_id = opaque_token(payload.get("session_id")) or uuid.uuid4().hex
    turn_id = opaque_token(payload.get("turn_id")) or uuid.uuid4().hex
    return {"session_id": session_id, "turn_id": turn_id, "intent": question}


def _trace_headers(trace: dict | None) -> dict:
    """The x-hyperset-* headers that carry a turn's trace linkage to the backend, minting a
    fresh `tool_call_id` for this specific call. Empty when there is no trace, so a non-traced
    call is byte-for-byte unchanged."""
    if not trace:
        return {}
    headers = {
        _HYPERSET_SESSION_HEADER: trace.get("session_id"),
        TURN_HEADER: trace.get("turn_id"),
        TOOL_CALL_HEADER: trace.get("tool_call_id") or uuid.uuid4().hex,
        INTENT_HEADER: trace.get("intent"),
    }
    return {name: str(value) for name, value in headers.items() if value}


def _resolve_playground_bundle(
    question: str, directive: dict, *, trace: dict | None = None
) -> dict:
    base_url = _upstream_base_url()
    status, payload = _http_json(
        f"{base_url}/v0/resolve_analytics_context",
        method="POST",
        payload={"query": question, "directive": directive},
        timeout=30,
        headers=_trace_headers(trace),
    )
    if not status or status >= 400:
        detail = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            error_payload = {
                "code": str(detail.get("code") or "context_resolution_failed"),
                "message": str(detail.get("message") or "context resolution failed"),
                "recovery": str(detail.get("recovery") or "retry context discovery"),
            }
        else:
            error_payload = {
                "code": "context_resolution_failed",
                "message": str(
                    detail or f"context resolution returned HTTP {status or 'no response'}"
                ),
                "recovery": "retry context discovery or continue without governed context",
            }
        raise PlaygroundResolutionError(error_payload)
    return payload


def _normalise_attachments(raw) -> list[dict]:
    """Bounded, deduped user-pinned catalog references from the composer."""
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        domain = str(item.get("domain") or "").strip()
        term = str(item.get("term") or "").strip()
        if kind == "concept" and domain and term:
            key = ("concept", domain, term)
        elif kind == "domain" and domain:
            key = ("domain", domain, "")
        else:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": key[0], "domain": domain, "term": term})
        if len(out) >= 20:
            break
    return out


def _merge_attachments(directive: dict, attachments: list[dict]) -> dict:
    """Seed the selector directive with the exact catalog values the user pinned."""
    domains = list(directive.get("domains") or [])
    concepts = list(directive.get("concepts") or [])
    for item in attachments:
        if item["domain"] and item["domain"] not in domains:
            domains.append(item["domain"])
        if item["kind"] == "concept" and item["term"] and item["term"] not in concepts:
            concepts.append(item["term"])
    return {
        **directive,
        "domains": domains,
        "concepts": concepts,
        "asset_refs": directive.get("asset_refs") or [],
    }


def _attachment_facts(attachments: list[dict]) -> list[str]:
    return [
        f"- concept '{item['term']}' in domain '{item['domain']}'"
        if item["kind"] == "concept"
        else f"- domain '{item['domain']}'"
        for item in attachments
    ]


class _TurnQueue:
    """Process-wide FIFO so playground chat turns run one at a time.

    A turn calls ``acquire()`` to wait for its slot, then ``release()`` when
    done. While waiting, ``acquire()`` calls ``heartbeat(position)`` about once a
    second; if that raises -- the client's SSE connection is gone -- the waiter
    drops out of the queue instead of blocking the turns behind it. The running
    turn is cancelled the same way: its next ``emit`` raises on a dead socket,
    which unwinds to ``release()`` and lets the next turn start.
    """

    def __init__(self, poll_interval: float = 1.0) -> None:
        self._lock = threading.Lock()
        self._waiters: deque[threading.Event] = deque()
        self._busy = False
        self._poll_interval = poll_interval

    def acquire(self, *, heartbeat) -> None:
        with self._lock:
            if not self._busy:
                self._busy = True
                return
            ticket = threading.Event()
            self._waiters.append(ticket)
        promoted = False
        while not promoted:
            with self._lock:
                position = (self._waiters.index(ticket) + 1) if ticket in self._waiters else 0
            try:
                heartbeat(position)  # reports position now and probes the socket
            except Exception:
                with self._lock:
                    if ticket in self._waiters:
                        self._waiters.remove(ticket)
                        raise  # dropped cleanly before it was our turn
                # Promoted between the wait timeout and here: we hold the slot
                # now, so hand it to the next turn before bailing out.
                self.release()
                raise
            promoted = ticket.wait(timeout=self._poll_interval)
        # ticket was set by the previous holder: the slot is ours, _busy stays True.

    def release(self) -> None:
        with self._lock:
            while self._waiters:
                self._waiters.popleft().set()
                return
            self._busy = False


_CHAT_TURN_QUEUE = _TurnQueue()


def _stream_playground_chat(payload: dict, emit) -> None:
    """Serialize turns process-wide, then stream one governed agent turn.

    Turns run in arrival order. A waiting turn reports its queue position and is
    dropped if its client disconnects; the running turn is cancelled when its
    client disconnects (the next ``emit`` raises), releasing the queue.
    """
    question = str(payload.get("question") or "").strip()
    if not question:
        raise ValueError("question is required")
    _CHAT_TURN_QUEUE.acquire(
        heartbeat=lambda position: emit({"type": "queued", "position": position})
    )
    try:
        emit({"type": "start"})
        _run_playground_chat_turn(payload, emit, question)
    finally:
        _CHAT_TURN_QUEUE.release()


def _run_playground_chat_turn(payload: dict, emit, question: str) -> None:
    """Resolve context server-side, then stream the governed agent turn."""
    governed_only = bool(payload.get("governed_only"))
    attachments = _normalise_attachments(payload.get("attachments"))
    emit(
        {
            "type": "stage",
            "stage": "discovering",
            "title": "Discovering governed context",
            "detail": (
                "The agent is reading the catalog and mapping the question to the smallest "
                "useful graph."
            ),
        }
    )
    resolution_error = None
    provider_fault = False
    empty_selection = {
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        "question": question,
        "mode": "select",
        "directive": {"domains": [], "concepts": [], "asset_refs": []},
        "catalog_domains": [],
        "duration_ms": None,
    }
    try:
        selection = _run_context_selector(payload)
    except PlaygroundProviderError as exc:
        # A provider/model-call fault, not an empty catalog: attribute the
        # downgrade to the provider so it is not entered silently under a
        # corpus-sounding label (GitHub #344).
        provider_fault = True
        selection = {
            **empty_selection,
            "rationale": "The model provider failed the context-discovery call.",
        }
        resolution_error = exc.error_payload
    except Exception as exc:  # noqa: BLE001 -- no context must not block the answer
        selection = {
            **empty_selection,
            "rationale": "Context discovery did not return a usable directive.",
        }
        resolution_error = {
            "code": "context_discovery_failed",
            "message": "The agent could not produce a usable context directive.",
            "recovery": (
                "The agent will continue without governed context and label the answer accordingly."
            ),
            "detail": str(exc),
        }
    if attachments:
        selection["directive"] = _merge_attachments(selection.get("directive") or {}, attachments)
    emit({"type": "selection", "selection": selection})
    directive = selection.get("directive") or {}
    selected_scopes = [
        *[str(value) for value in directive.get("domains") or []],
        *[str(value) for value in directive.get("concepts") or []],
    ]
    emit(
        {
            "type": "stage",
            "stage": "discovering",
            "title": "Discovering governed context",
            "detail": (
                f"Catalog match: {' · '.join(selected_scopes)}."
                if selected_scopes
                else "No exact catalog domain or asset reference matched the question."
            ),
            "data": {
                "directive": directive,
                "rationale": selection.get("rationale") or "",
                "catalog_domains": selection.get("catalog_domains") or [],
                "duration_ms": selection.get("duration_ms"),
            },
        }
    )
    emit(
        {
            "type": "stage",
            "stage": "resolving",
            "title": "Resolving connected bundles",
            "detail": (
                "Hyperset is resolving the selected domain, related bundles, evidence, and Git "
                "authority."
            ),
        }
    )
    bundle = None
    if resolution_error is None:
        try:
            bundle = _resolve_playground_bundle(
                question, selection["directive"], trace=_playground_trace(payload, question)
            )
        except PlaygroundResolutionError as exc:
            resolution_error = exc.error_payload
    else:
        bundle = None
    if resolution_error:
        emit({"type": "resolution_error", "error": resolution_error})
        emit(
            {
                "type": "stage",
                "stage": "resolving",
                "status": "warning",
                # A provider fault must not read as a corpus outcome (GitHub #344).
                "title": (
                    "Model provider failed the discovery call"
                    if provider_fault
                    else "No governed context found"
                ),
                "detail": (
                    "The model provider failed the context-discovery call, so the agent will "
                    "answer without a governed bundle; this is a provider/config fault, not an "
                    "empty catalog."
                    if provider_fault
                    else (
                        "The catalog did not yield an exact directive, so the agent will answer "
                        "without a governed bundle."
                    )
                ),
                "data": {"context_bundle": None, "error": resolution_error},
            }
        )
    else:
        emit({"type": "bundle", "bundle": bundle})
        bundle_directive = (bundle.get("request") or {}).get("directive") or directive
        graph = bundle.get("domain_graph") or {}
        edges = graph.get("edges") or []
        scope = [
            *[str(value) for value in bundle_directive.get("domains") or []],
            *[str(value) for value in bundle_directive.get("concepts") or []],
        ]
        authority = bundle.get("context_authority") or {}
        authority_path = authority.get("path") or "Git context"
        emit(
            {
                "type": "stage",
                "stage": "resolving",
                "title": "Resolving connected bundles",
                "detail": (
                    f"{bundle.get('bundle_id') or 'ContextBundle'} · "
                    f"{' · '.join(scope) or 'governed context'} · "
                    f"{len(edges)} relationships · Git {authority_path}"
                ),
                "data": {
                    "bundle_id": bundle.get("bundle_id"),
                    "directive": bundle_directive,
                    "context_authority": {
                        key: authority.get(key)
                        for key in ("type", "path", "commit_sha", "context_snapshot_id")
                        if authority.get(key) is not None
                    },
                    "resolution": bundle.get("resolution") or {},
                    "domain_graph": graph,
                },
            }
        )
    if governed_only and not bundle:
        # Governed-only refuses to answer from raw metadata -- but a provider fault
        # must still carry its provider-specific terminal source, not read as a
        # corpus outcome, on this early return too (GitHub #344).
        blocked_source = "discovery_provider_error" if provider_fault else "governed_blocked"
        emit(
            {
                "type": "stage",
                "stage": "reasoning",
                "status": "warning",
                "title": "Governed context required",
                "detail": (
                    "Governed-only is on and the context-discovery call failed at the model "
                    "provider, so no governed bundle resolved. Not answering from raw metadata."
                    if provider_fault
                    else (
                        "Governed-only is on and no governed bundle resolved. Not answering from "
                        "raw metadata."
                    )
                ),
            }
        )
        emit(
            {
                "type": "done",
                "result": {
                    "governed_blocked": True,
                    "question": question,
                    "answer": (
                        "I haven't answered: governed-only is on and the context-discovery call "
                        "failed at the model provider, so no governed ContextBundle could be "
                        "resolved. This is a provider/config fault, not an empty catalog."
                        if provider_fault
                        else (
                            "I couldn't find governed metadata for this question, so I haven't "
                            "answered. Nothing here is backed by a governed ContextBundle."
                        )
                    ),
                    "context_included": False,
                    "context_source": blocked_source,
                    "context_resolution": {"status": "unresolved", "error": resolution_error},
                    "agent_label": None,
                    "trace": [],
                },
            }
        )
        return
    instructions = (bundle or {}).get("instructions") or {}
    if bundle:
        definitions = instructions.get("definitions") or []
        approved_sources = instructions.get("approved_sources") or []
        filters = instructions.get("filters") or []
        joins = instructions.get("joins") or []
        validations = instructions.get("validations") or []
        emit(
            {
                "type": "stage",
                "stage": "reasoning",
                "title": "Building agent context",
                "detail": (
                    f"Loaded {len(definitions)} definitions · "
                    f"{len(approved_sources)} approved sources · "
                    f"{len(filters)} filters · {len(joins)} joins · {len(validations)} validations."
                ),
                "data": {
                    "bundle_id": bundle.get("bundle_id"),
                    "definitions": definitions,
                    "approved_sources": approved_sources,
                    "prohibited_sources": instructions.get("prohibited_sources") or [],
                    "filters": filters,
                    "joins": joins,
                    "grain": instructions.get("grain"),
                    "validations": validations,
                },
            }
        )
    if not bundle:
        emit(
            {
                "type": "stage",
                "stage": "reasoning",
                "title": "Answering without governed context",
                "detail": (
                    "The agent has been told no bundle was resolved and must label unsupported "
                    "meaning clearly."
                ),
                "data": {"context_bundle": None, "resolution_error": resolution_error},
            }
        )

    def on_agent_event(message: dict) -> None:
        if message.get("type") == "sql":
            sql_result = message.get("result") or {}
            emit({"type": "sql", "result": sql_result})
            emit(
                {
                    "type": "stage",
                    "stage": "checking",
                    "title": "Checking the warehouse result",
                    "detail": (
                        "Read-only SQL returned "
                        f"{sql_result.get('row_count', len(sql_result.get('rows') or []))} rows."
                        if not sql_result.get("error")
                        else f"Read-only SQL failed: {sql_result.get('error')}"
                    ),
                    "data": sql_result,
                }
            )
            return
        if message.get("type") == "agent_event":
            event = message.get("event")
            delta = _stream_text_delta(event)
            if delta:
                emit({"type": "token", "delta": delta})

    result = _run_agent_harness(
        {
            **payload,
            "mode": "governed",
            "bundle": bundle,
            "selection_trace": selection,
            "resolution_error": resolution_error,
            "governed_only": governed_only,
            "attachments": attachments,
        },
        stream_callback=on_agent_event,
    )
    emit({"type": "done", "result": result})


class UIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "hyperset-ui"
    sys_version = ""

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/index.html"}:
            self._send_html((BASE_DIR / "index.html").read_text())
            return
        if self.path.startswith("/static/"):
            path = (BASE_DIR / self.path.removeprefix("/static/")).resolve()
            if path.exists() and path.is_file():
                self._send_file(path)
                return
            self._send_json(404, {"error": "not_found"})
            return
        if self.path.startswith("/api/"):
            if self.path == "/api/demo/status":
                try:
                    self._send_json(200, _demo_status())
                except Exception as exc:  # noqa: BLE001
                    self._send_json(502, {"error": str(exc)})
                return
            self._proxy_to_hyperset(method="GET")
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.startswith("/api/"):
            if self.path == "/api/demo/query":
                self._run_demo_query()
                return
            if self.path == "/api/demo/ask":
                self._run_demo_ask()
                return
            self._proxy_to_hyperset(method="POST")
            return
        self._send_json(404, {"error": "not_found"})

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_DEMO_SQL_BYTES * 4:
            raise ValueError("request body is too large")
        body = self.rfile.read(length)
        value = json.loads(body.decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _run_demo_query(self) -> None:
        try:
            result = _run_demo_sql(self._read_json_body().get("sql"))
            self._send_json(200, result)
        except Exception as exc:  # noqa: BLE001 - UI renders the source failure
            self._send_json(400, {"error": str(exc)})

    def _run_demo_ask(self) -> None:
        try:
            self._send_json(200, _run_agent_harness(self._read_json_body()))
        except Exception as exc:  # noqa: BLE001 - UI renders the harness failure
            self._send_json(502, {"error": str(exc)})

    def _forwarded_trace_headers(self) -> dict:
        """The MCP interaction-trace linkage headers the browser set, passed through to the
        backend so a proxied traced call is not orphaned (hy-z7bsw). ONLY these audit-linkage
        headers cross; every other client header (Authorization included) is dropped. The
        backend re-validates each against its opaque-token shape, so nothing crafted survives."""
        names = (*SESSION_HEADERS, TURN_HEADER, TOOL_CALL_HEADER, CORRELATION_HEADER, INTENT_HEADER)
        forwarded: dict[str, str] = {}
        for name in names:
            value = self.headers.get(name)
            if value:
                forwarded[name] = value
        return forwarded

    def _proxy_to_hyperset(self, *, method: str) -> None:
        body = b""
        if method == "POST":
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        target = self.path.removeprefix("/api")
        target = target if target.startswith("/") else f"/{target}"
        base_url = _upstream_target()  # verbatim, as the prior raw os.environ.get read
        url = f"{base_url}{target}"
        req = request.Request(
            url,
            data=body,
            # Forward the browser's MCP interaction-trace linkage so a proxied resolve/search
            # is tied to its session/turn/tool-call the same way a direct call is (hy-z7bsw);
            # without this the proxy stripped them and the durable row landed blank. Only the
            # audit-linkage headers cross -- never Authorization or an arbitrary caller header --
            # and the backend re-validates each to its opaque-token shape.
            headers={"Content-Type": "application/json", **self._forwarded_trace_headers()},
            method=method,
        )
        try:
            with request.urlopen(req, timeout=15) as resp:
                payload = resp.read().decode("utf-8")
                self._send_json(resp.status, json.loads(payload) if payload else {})
        except error.HTTPError as exc:
            payload = exc.read().decode("utf-8", "replace")
            try:
                payload_json = json.loads(payload)
            except Exception:
                payload_json = {"error": payload}
            self._send_json(exc.code, payload_json)
        except Exception as exc:  # noqa: BLE001
            self._send_json(502, {"error": str(exc)})

    def _send_html(self, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        mime = "text/plain; charset=utf-8"
        if path.suffix == ".css":
            mime = "text/css; charset=utf-8"
        elif path.suffix == ".js":
            mime = "application/javascript; charset=utf-8"
        elif path.suffix == ".svg":
            mime = "image/svg+xml"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return


def main() -> None:
    from hyperset.security.deployment import assert_loopback_only

    host = os.environ.get("HYPERSET_UI_HOST", DEFAULT_HOST)
    port = int(os.environ.get("HYPERSET_UI_PORT", DEFAULT_PORT))
    # FAIL CLOSED before binding (hy-w5ld, ADR-0035 Decision 4): this proxy authenticates
    # nothing and serves local endpoints (the SPA, /demo/*), so a non-loopback
    # HYPERSET_UI_HOST would expose them UNAUTHENTICATED. It is loopback-only -- there is
    # no auth configuration that makes a network bind acceptable here.
    assert_loopback_only(host, surface="playground ui")
    print(f"Hyperset temp UI listening on http://{host}:{port}")
    print(f"Proxying requests to {_upstream_target()}")
    ThreadingHTTPServer((host, port), UIHandler).serve_forever()


if __name__ == "__main__":
    main()
