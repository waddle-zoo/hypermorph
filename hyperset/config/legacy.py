"""Supported runtime and legacy HYPERSET_* env vars folded into layered config
(hy-tc4o, config slice 3, proposal section 9).

The wiring migrates each `os.environ.get` read to the settings object DOMAIN BY DOMAIN. A
deployment that still sets an env var keeps working: the loader folds each mapped var into the
config it validates -- an allowlisted override (layer 3). Legacy names warn ONCE; supported
runtime names fold without a false deprecation warning.

A SECRET var (a DSN, a token) is folded as a `${env:VAR}` REFERENCE, never its plaintext value:
the config field is secret-typed and rejects a plaintext, and slice-2 secret resolution reads
the reference back to the value, so a credential never sits in the config, a log, or an echo.

This is the first, behaviour-preserving half of the STAGED migration. The LATER, separately-
gated bead (hy-nm4mp) makes an unknown HYPERSET_* fatal at startup and drops the mapped vars
from Compose (both deployment-breaking until every domain is migrated) -- NOT this slice.

Only MIGRATED domains appear here; each domain slice adds its rows.
"""

from __future__ import annotations

import sys

from hyperset.config.feature_settings import PII_GUARD_ENGAGED_VALUES

# Env values folded into layered config. Most are legacy compatibility names; runtime names in
# CANONICAL_RUNTIME_ENV remain the supported Compose contract and fold without a false
# deprecation warning. A secret var is folded as a `${env:VAR}` reference so its plaintext never
# enters the config.
LEGACY_ENV_MAP: dict[str, tuple[tuple[str, ...], bool]] = {
    # context (hy-tc4o), PLAIN.
    "HYPERSET_CONTEXT_CACHE_DIR": (("context", "cache_dir"), False),
    "HYPERSET_CONTEXT_MAX_FILES": (("context", "max_files"), False),
    # DB (hy-2562h), SECRET -- the whole URL embeds a password.
    "HYPERSET_DATABASE_URL": (("connections", "system_db", "url"), True),
    "HYPERSET_ANALYTICS_DB_URL": (("connections", "analytics_db", "url"), True),
    # models / providers (hy-7m5yg). PLAIN, except the provider API keys.
    "HYPERSET_MODEL_PROVIDER": (("models", "provider"), False),
    "HYPERSET_OLLAMA_BASE_URL": (("providers", "ollama", "base_url"), False),
    "HYPERSET_OLLAMA_MODEL": (("providers", "ollama", "model"), False),
    "HYPERSET_OPENAI_BASE_URL": (("providers", "openai", "base_url"), False),
    "HYPERSET_OPENAI_MODEL": (("providers", "openai", "model"), False),
    "HYPERSET_OPENAI_REASONING_EFFORT": (("providers", "openai", "reasoning_effort"), False),
    "HYPERSET_OPENAI_API_KEY": (("providers", "openai", "api_key"), True),
    "HYPERSET_EMBEDDING_PROVIDER": (("providers", "embedding", "provider"), False),
    "HYPERSET_EMBEDDING_BASE_URL": (("providers", "embedding", "base_url"), False),
    "HYPERSET_EMBEDDING_MODEL": (("providers", "embedding", "model"), False),
    "HYPERSET_EMBEDDING_API_KEY": (("providers", "embedding", "api_key"), True),
    # PLAIN. A brand-new var (no prior env read), so unlike the ollama/openai max_tokens ints
    # this CAN fold: there is no lenient legacy behaviour to break -- the schema's Int(minimum=1)
    # is the ONLY bound, and a bad value is a loud startup error rather than a silent default.
    "HYPERSET_EMBEDDING_DIMENSIONS": (("providers", "embedding", "dimensions"), False),
    # playground (hy-lk83s). PLAIN. `agents`/`models` are JSON blobs folded verbatim (Raw);
    # `upstream_base_url` maps the UI proxy target HYPERSET_HTTP_BASE_URL (proposal section 9).
    "HYPERSET_PLAYGROUND_AGENTS_JSON": (("playground", "agents"), False),
    "HYPERSET_PLAYGROUND_MODELS_JSON": (("playground", "models"), False),
    "HYPERSET_PLAYGROUND_DEFAULT_AGENT": (("playground", "default_agent"), False),
    "HYPERSET_PLAYGROUND_DEFAULT_MODEL": (("playground", "default_model"), False),
    "HYPERSET_HTTP_BASE_URL": (("playground", "upstream_base_url"), False),
    # connections (hy-py62a). ALL SECRET -- the schema types these paths as Ref, so each folds
    # as a ${env:VAR} reference and is revealed at the read, never inlined as plaintext.
    "HYPERSET_SUPERSET_USERNAME": (("connections", "superset", "username"), True),
    "HYPERSET_SUPERSET_PASSWORD": (("connections", "superset", "password"), True),
    "HYPERSET_DATAHUB_TOKEN": (("connections", "datahub", "token"), True),
    # features / PII (hy-e16vx). PLAIN. `pii_guard` is folded SEPARATELY (Bool, below).
    "HYPERSET_PII_ACTION": (("features", "pii", "action"), False),
    "HYPERSET_PII_ENTITIES": (("features", "pii", "entities"), False),
    "HYPERSET_PII_SPACY_MODEL": (("features", "pii", "spacy_model"), False),
    # NOTE: HYPERSET_PLAYGROUND_ENABLED and HYPERSET_PII_GUARD are folded SEPARATELY, via
    # LEGACY_BOOL_ENV_MAP below -- their schema home is Bool, so a lenient value must be
    # NORMALIZED to 'true'/'false' before validation.
    # NOTE: HYPERSET_OPENAI_MAX_COMPLETION_TOKENS and HYPERSET_OLLAMA_MAX_TOKENS are NOT
    # folded here. Their schema homes are Int(minimum=1) -- STRICTER than the legacy reads,
    # which fall back to a default on a non-integer or non-positive value. Folding them
    # would turn that lenient fallback into a fatal ConfigError, so the migration is not
    # behaviour-preserving. They are read through the settings accessor (loaded config, else
    # the exact prior lenient env read) instead, and gain a config home only via YAML, where
    # Int validation is the operator's explicit opt-in (hy-7m5yg critic; the max_files lesson,
    # a-migration-must-match-the-old-bound-exactly, applied in the other direction).
}

# Supported runtime contract used by Compose and model/chat paths. These share the safe folding
# implementation above, but are not deprecated. OpenAI uses the vendor key plus
# HYPERSET_OPENAI_* settings; probes must not invent a HYPERSET_FRONTIER_* contract (hy-7zk8a).
CANONICAL_RUNTIME_ENV = frozenset(
    {
        "HYPERSET_DATABASE_URL",
        "HYPERSET_ANALYTICS_DB_URL",
        "HYPERSET_MODEL_PROVIDER",
        "HYPERSET_OLLAMA_BASE_URL",
        "HYPERSET_OLLAMA_MODEL",
        "HYPERSET_OPENAI_BASE_URL",
        "HYPERSET_OPENAI_MODEL",
        "HYPERSET_OPENAI_REASONING_EFFORT",
        "HYPERSET_OPENAI_API_KEY",
        "HYPERSET_EMBEDDING_PROVIDER",
        "HYPERSET_EMBEDDING_BASE_URL",
        "HYPERSET_EMBEDDING_MODEL",
        "HYPERSET_EMBEDDING_API_KEY",
        "HYPERSET_EMBEDDING_DIMENSIONS",
    }
)

# Legacy vars whose schema home is Bool (only 'true'/'false') but whose legacy read is LENIENT.
# Each -> (its config path, the set of case-folded values that mean TRUE). Folded as a NORMALIZED
# 'true'/'false' so (a) the strict Bool schema accepts the override and (b) it WINS over a base
# default -- config/base.yaml sets `playground.enabled: false` AND `features.pii_guard: false`, so
# a non-folded env could never turn either on once startup loaded the config (hy-lk83s adversary;
# the value-not-just-the-key must reach the config). The PII guard's TRUE set includes 'on', so
# it carries its own from feature_settings rather than sharing the playground set.
LEGACY_BOOL_ENV_MAP: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "HYPERSET_PLAYGROUND_ENABLED": (("playground", "enabled"), frozenset({"1", "true", "yes"})),
    "HYPERSET_PII_GUARD": (("features", "pii_guard"), PII_GUARD_ENGAGED_VALUES),
}

_warned: set[str] = set()


def _set_path(overlay: dict, path: tuple[str, ...], value: str) -> None:
    node = overlay
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def legacy_env_overlay(env: dict) -> dict:
    """A partial config overlay built from any MIGRATED legacy vars set in `env`, warning once
    per var per process. A SECRET var maps to a `${env:VAR}` reference (resolved by slice-2),
    a PLAIN var to its raw value. Empty when none are set, so a config that uses only the new
    paths is unchanged."""
    overlay: dict = {}
    for var, (path, is_secret) in LEGACY_ENV_MAP.items():
        raw = env.get(var)
        if raw is None or not str(raw).strip():
            continue
        if var not in _warned and var not in CANONICAL_RUNTIME_ENV:
            ref_hint = " as a ${env:...}/${secret:...} reference" if is_secret else ""
            print(
                f"WARNING: {var} is deprecated; set `{'.'.join(path)}` in your config overlay "
                f"(HYPERSET_CONFIG){ref_hint} instead. It is still honored for now.",
                file=sys.stderr,
            )
            _warned.add(var)
        # A SECRET var is referenced, never inlined: its plaintext must not enter the config.
        _set_path(overlay, path, f"${{env:{var}}}" if is_secret else str(raw))
    for var, (path, truth_values) in LEGACY_BOOL_ENV_MAP.items():
        raw = env.get(var)
        if raw is None or not str(raw).strip():
            continue
        if var not in _warned:
            print(
                f"WARNING: {var} is deprecated; set `{'.'.join(path)}` in your config overlay "
                f"(HYPERSET_CONFIG) instead. It is still honored for now.",
                file=sys.stderr,
            )
            _warned.add(var)
        # Normalize the lenient legacy value to the strict Bool the schema accepts, so the
        # override both validates and wins over the base default.
        _set_path(overlay, path, "true" if str(raw).strip().lower() in truth_values else "false")
    return overlay
