"""The models / providers domain, read from the loaded config or the legacy env (hy-7m5yg).

Third domain migrated off scattered `os.environ.get` (config slice 3c). One accessor per read
so every call site -- the embedding provider construction, the review runtime, the admin
readiness overview, and the provider probe -- reads the SAME resolved value and cannot
disagree. Behaviour-preserving: once startup ran the settings object is authoritative (a
legacy var was folded into it); otherwise it is the exact prior env read. The embedding API
key is SECRET -- it travels as a `${env:...}`/`${secret:...}` reference and is revealed here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from hyperset.config.runtime import active_settings
from hyperset.config.secrets import reveal_secret

MODEL_PROVIDER_ENV = "HYPERSET_MODEL_PROVIDER"
OLLAMA_BASE_URL_ENV = "HYPERSET_OLLAMA_BASE_URL"
OLLAMA_MODEL_ENV = "HYPERSET_OLLAMA_MODEL"
OLLAMA_MAX_TOKENS_ENV = "HYPERSET_OLLAMA_MAX_TOKENS"
OPENAI_BASE_URL_ENV = "HYPERSET_OPENAI_BASE_URL"
OPENAI_MODEL_ENV = "HYPERSET_OPENAI_MODEL"
OPENAI_REASONING_EFFORT_ENV = "HYPERSET_OPENAI_REASONING_EFFORT"
OPENAI_MAX_COMPLETION_TOKENS_ENV = "HYPERSET_OPENAI_MAX_COMPLETION_TOKENS"
# The hosted-OpenAI credential is the VENDOR SDK's own var (no HYPERSET_ prefix); it is the
# behaviour-preserving legacy source for providers.openai.api_key. A deployment moving to the
# config sets HYPERSET_OPENAI_API_KEY (folded as a ${env:...} reference, slice-2 SECRET).
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
EMBEDDING_PROVIDER_ENV = "HYPERSET_EMBEDDING_PROVIDER"
EMBEDDING_BASE_URL_ENV = "HYPERSET_EMBEDDING_BASE_URL"
EMBEDDING_MODEL_ENV = "HYPERSET_EMBEDDING_MODEL"
EMBEDDING_API_KEY_ENV = "HYPERSET_EMBEDDING_API_KEY"
EMBEDDING_DIMENSIONS_ENV = "HYPERSET_EMBEDDING_DIMENSIONS"


def _settings_get(*path):
    """A value at a nested settings path, or None -- only when startup has loaded a config."""
    settings = active_settings()
    if settings is None:
        return None
    node = settings
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
        if node is None:
            return None
    return node


def _env(source: Mapping[str, str] | None):
    return os.environ if source is None else source


def model_provider(env: Mapping[str, str] | None = None) -> str:
    """The model-provider selection (`models.provider`), else the legacy env, else ''."""
    configured = _settings_get("models", "provider")
    if configured is not None:
        return str(configured)
    return str(_env(env).get(MODEL_PROVIDER_ENV, "")).strip()


def embedding_provider(env: Mapping[str, str] | None = None) -> str:
    configured = _settings_get("providers", "embedding", "provider")
    if configured is not None:
        return str(configured)
    return str(_env(env).get(EMBEDDING_PROVIDER_ENV, "")).strip()


def embedding_base_url(env: Mapping[str, str] | None = None) -> str | None:
    configured = _settings_get("providers", "embedding", "base_url")
    if configured is not None:
        return str(configured)
    raw = str(_env(env).get(EMBEDDING_BASE_URL_ENV, "")).strip()
    return raw or None


def embedding_model(env: Mapping[str, str] | None = None) -> str | None:
    configured = _settings_get("providers", "embedding", "model")
    if configured is not None:
        return str(configured)
    raw = str(_env(env).get(EMBEDDING_MODEL_ENV, "")).strip()
    return raw or None


def embedding_dimensions(env: Mapping[str, str] | None = None) -> int | None:
    """The embedding width the adapter requests: the loaded `providers.embedding.dimensions`
    (an int, schema-validated `>= 1`), else the legacy env parsed as an int, else None. None
    means no served embedding provider can be constructed. A non-integer env on the pre-startup
    path is treated as unset (the loaded-config path already fails loudly via the schema); a
    startup-loaded deployment never reaches the lenient branch.
    """
    configured = _settings_get("providers", "embedding", "dimensions")
    if configured is not None:
        return int(configured)
    raw = str(_env(env).get(EMBEDDING_DIMENSIONS_ENV, "")).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def embedding_api_key(env: Mapping[str, str] | None = None) -> str | None:
    """The embedding provider API key, revealed from the resolved `Secret`, else the legacy
    env, else None. SECRET: never a plaintext in the config or a log."""
    configured = _settings_get("providers", "embedding", "api_key")
    if configured is not None:
        return reveal_secret(configured)
    raw = str(_env(env).get(EMBEDDING_API_KEY_ENV, "")).strip()
    return raw or None


def ollama_base_url(env: Mapping[str, str] | None = None) -> str | None:
    configured = _settings_get("providers", "ollama", "base_url")
    if configured is not None:
        return str(configured)
    raw = str(_env(env).get(OLLAMA_BASE_URL_ENV, "")).strip()
    return raw or None


def ollama_model(env: Mapping[str, str] | None = None) -> str | None:
    configured = _settings_get("providers", "ollama", "model")
    if configured is not None:
        return str(configured)
    raw = str(_env(env).get(OLLAMA_MODEL_ENV, "")).strip()
    return raw or None


def ollama_max_tokens(env: Mapping[str, str] | None = None) -> int | str | None:
    """The Ollama completion cap: the loaded `providers.ollama.max_tokens` (an int, already
    validated `>= 1`), else the RAW legacy env value (a string the caller parses leniently,
    falling back on a non-integer/non-positive value), else None. NOT folded through the
    legacy shim -- the schema bound is stricter than the lenient env read (see legacy.py)."""
    configured = _settings_get("providers", "ollama", "max_tokens")
    if configured is not None:
        return configured
    raw = str(_env(env).get(OLLAMA_MAX_TOKENS_ENV, "")).strip()
    return raw or None


def openai_base_url(env: Mapping[str, str] | None = None) -> str | None:
    configured = _settings_get("providers", "openai", "base_url")
    if configured is not None:
        return str(configured)
    raw = str(_env(env).get(OPENAI_BASE_URL_ENV, "")).strip()
    return raw or None


def openai_model(env: Mapping[str, str] | None = None) -> str | None:
    configured = _settings_get("providers", "openai", "model")
    if configured is not None:
        return str(configured)
    raw = str(_env(env).get(OPENAI_MODEL_ENV, "")).strip()
    return raw or None


def openai_reasoning_effort(env: Mapping[str, str] | None = None) -> str | None:
    configured = _settings_get("providers", "openai", "reasoning_effort")
    if configured is not None:
        return str(configured)
    raw = str(_env(env).get(OPENAI_REASONING_EFFORT_ENV, "")).strip()
    return raw or None


def openai_max_completion_tokens(env: Mapping[str, str] | None = None) -> int | str | None:
    """The OpenAI gateway completion budget: the loaded `providers.openai.max_completion_tokens`
    (an int, validated `>= 1`), else the RAW legacy env value (a string the caller parses
    leniently), else None. NOT folded through the legacy shim (see `ollama_max_tokens`)."""
    configured = _settings_get("providers", "openai", "max_completion_tokens")
    if configured is not None:
        return configured
    raw = str(_env(env).get(OPENAI_MAX_COMPLETION_TOKENS_ENV, "")).strip()
    return raw or None


def openai_api_key(env: Mapping[str, str] | None = None) -> str | None:
    """The hosted-OpenAI API key, revealed from the resolved `Secret`, else the VENDOR
    `OPENAI_API_KEY` env (the exact prior read), else None. SECRET: never a plaintext in the
    config or a log; a deployment on the config sets HYPERSET_OPENAI_API_KEY as a reference."""
    configured = _settings_get("providers", "openai", "api_key")
    if configured is not None:
        return reveal_secret(configured)
    raw = str(_env(env).get(OPENAI_API_KEY_ENV, "")).strip()
    return raw or None
