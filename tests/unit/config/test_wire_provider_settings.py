"""The models/providers domain reads through the settings object, behaviour-preserving and
secret-safe (hy-7m5yg, config slice 3c).

One accessor per read so the embedding construction, the review runtime, the readiness
overview and the provider probe cannot disagree. The embedding API key is SECRET -- it folds
as a `${env:VAR}` reference, resolves to a write-only `Secret`, and is revealed only at the
read. These pin the SAME value across set / unset / default with and without startup.
"""

from __future__ import annotations

from hyperset.config import (
    Secret,
    embedding_api_key,
    embedding_base_url,
    embedding_dimensions,
    embedding_model,
    embedding_provider,
    legacy_env_overlay,
    load_settings,
    model_provider,
    ollama_base_url,
    ollama_max_tokens,
    ollama_model,
    openai_api_key,
    openai_base_url,
    openai_max_completion_tokens,
    openai_model,
    openai_reasoning_effort,
    redact_settings,
    resolve_secrets,
)
from hyperset.config.runtime import set_active_settings

_KEY = "HYPERSET_EMBEDDING_API_KEY"
_OPENAI_VARS = (
    "HYPERSET_OPENAI_BASE_URL",
    "HYPERSET_OPENAI_MODEL",
    "HYPERSET_OPENAI_REASONING_EFFORT",
    "HYPERSET_OPENAI_MAX_COMPLETION_TOKENS",
    "HYPERSET_OPENAI_API_KEY",
    "OPENAI_API_KEY",
    "HYPERSET_OLLAMA_MODEL",
    "HYPERSET_OLLAMA_MAX_TOKENS",
)


def test_supported_runtime_env_does_not_emit_legacy_warnings(monkeypatch, capsys):
    import hyperset.config.legacy as legacy_module

    monkeypatch.setattr(legacy_module, "_warned", set())
    legacy_env_overlay(
        {
            "HYPERSET_DATABASE_URL": "postgresql://db/hyperset",
            "HYPERSET_MODEL_PROVIDER": "openai",
            "HYPERSET_OLLAMA_BASE_URL": "http://host.docker.internal:11434",
            "HYPERSET_OLLAMA_MODEL": "qwen2.5:7b",
            "HYPERSET_OPENAI_BASE_URL": "https://gateway.example/v1",
            "HYPERSET_OPENAI_MODEL": "gpt-5.6-luna",
            "HYPERSET_EMBEDDING_PROVIDER": "openai",
            "HYPERSET_EMBEDDING_BASE_URL": "https://gateway.example/v1",
            "HYPERSET_EMBEDDING_MODEL": "text-embedding-3-small",
            "HYPERSET_EMBEDDING_API_KEY": "sk-embed",
            "HYPERSET_EMBEDDING_DIMENSIONS": "768",
        }
    )
    assert capsys.readouterr().err == ""


def test_without_startup_the_legacy_env_drives_every_provider_read(monkeypatch):
    for var in (
        "HYPERSET_MODEL_PROVIDER",
        "HYPERSET_EMBEDDING_PROVIDER",
        "HYPERSET_EMBEDDING_BASE_URL",
        "HYPERSET_EMBEDDING_MODEL",
        _KEY,
        "HYPERSET_OLLAMA_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    assert model_provider() == "" and embedding_provider() == ""
    assert embedding_base_url() is None and embedding_model() is None
    assert embedding_api_key() is None and ollama_base_url() is None

    monkeypatch.setenv("HYPERSET_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("HYPERSET_EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("HYPERSET_EMBEDDING_BASE_URL", "https://emb.example/v1")
    monkeypatch.setenv("HYPERSET_EMBEDDING_MODEL", "text-embed")
    monkeypatch.setenv(_KEY, "sk-embed")
    monkeypatch.setenv("HYPERSET_OLLAMA_BASE_URL", "http://ollama:11434/v1")
    assert model_provider() == "ollama"
    assert embedding_provider() == "openai"
    assert embedding_base_url() == "https://emb.example/v1"
    assert embedding_model() == "text-embed"
    assert embedding_api_key() == "sk-embed"
    assert ollama_base_url() == "http://ollama:11434/v1"


def test_with_startup_the_settings_object_drives_the_reads(monkeypatch):
    # A live env var must not leak past the loaded config.
    monkeypatch.setenv("HYPERSET_EMBEDDING_PROVIDER", "should-be-ignored")
    set_active_settings(
        {
            "models": {"provider": "openai"},
            "providers": {
                "ollama": {"base_url": "http://cfg-ollama/v1"},
                "embedding": {"provider": "cfg-emb", "base_url": "http://cfg-emb/v1", "model": "m"},
            },
        }
    )
    assert model_provider() == "openai"
    assert embedding_provider() == "cfg-emb"
    assert embedding_base_url() == "http://cfg-emb/v1"
    assert ollama_base_url() == "http://cfg-ollama/v1"


def test_the_embedding_api_key_is_secret_end_to_end(monkeypatch):
    monkeypatch.setenv(_KEY, "sk-SECRETEMBED")
    env = dict(__import__("os").environ)
    # The legacy secret var folds as a ${env:VAR} reference, never plaintext.
    assert (
        legacy_env_overlay(env)["providers"]["embedding"]["api_key"]
        == "${env:HYPERSET_EMBEDDING_API_KEY}"
    )
    settings = resolve_secrets(load_settings(env=env), env=env)
    assert isinstance(settings["providers"]["embedding"]["api_key"], Secret)
    assert "SECRETEMBED" not in str(redact_settings(settings))
    set_active_settings(settings)
    assert embedding_api_key() == "sk-SECRETEMBED"  # revealed only at the read


def test_embedding_dimensions_reads_env_then_config_and_folds_via_legacy(monkeypatch):
    # hy-zakwj: a new PLAIN int var. Unset -> None (adapter keeps its own default). Set on the
    # pre-startup path -> parsed int. Folded via legacy -> providers.embedding.dimensions, and a
    # loaded config wins over a live env. A non-integer on the env path is treated as unset.
    monkeypatch.delenv("HYPERSET_EMBEDDING_DIMENSIONS", raising=False)
    set_active_settings(None)
    assert embedding_dimensions() is None
    monkeypatch.setenv("HYPERSET_EMBEDDING_DIMENSIONS", "768")
    assert embedding_dimensions() == 768
    monkeypatch.setenv("HYPERSET_EMBEDDING_DIMENSIONS", "not-a-number")
    assert embedding_dimensions() is None

    monkeypatch.setenv("HYPERSET_EMBEDDING_DIMENSIONS", "512")
    env = dict(__import__("os").environ)
    assert legacy_env_overlay(env)["providers"]["embedding"]["dimensions"] == "512"
    settings = load_settings(env=env)
    assert settings["providers"]["embedding"]["dimensions"] == 512  # schema coerced to int
    set_active_settings(settings)
    assert embedding_dimensions() == 512  # config wins over a live env


def test_startup_analytics_style_unconfigured_provider_is_empty(monkeypatch):
    for var in ("HYPERSET_EMBEDDING_PROVIDER", _KEY, "HYPERSET_OLLAMA_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    set_active_settings({"providers": {}})
    assert embedding_provider() == "" and ollama_base_url() is None and embedding_api_key() is None


def test_without_startup_the_legacy_env_drives_the_openai_and_ollama_reads(monkeypatch):
    for var in _OPENAI_VARS:
        monkeypatch.delenv(var, raising=False)
    assert openai_base_url() is None and openai_model() is None
    assert openai_reasoning_effort() is None and openai_max_completion_tokens() is None
    assert openai_api_key() is None and ollama_model() is None and ollama_max_tokens() is None

    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://gw.example/v1")
    monkeypatch.setenv("HYPERSET_OPENAI_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("HYPERSET_OPENAI_REASONING_EFFORT", "high")
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "8192")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-vendor")  # vendor SDK var: the legacy key source
    monkeypatch.setenv("HYPERSET_OLLAMA_MODEL", "qwen2.5:7b")
    monkeypatch.setenv("HYPERSET_OLLAMA_MAX_TOKENS", "2048")
    assert openai_base_url() == "https://gw.example/v1"
    assert openai_model() == "gpt-5.6-luna"
    assert openai_reasoning_effort() == "high"
    assert openai_max_completion_tokens() == "8192"
    assert openai_api_key() == "sk-vendor"
    assert ollama_model() == "qwen2.5:7b"
    assert ollama_max_tokens() == "2048"


def test_with_startup_the_settings_object_drives_the_openai_and_ollama_reads(monkeypatch):
    # A live legacy env must not leak past the loaded config.
    monkeypatch.setenv("HYPERSET_OPENAI_MODEL", "should-be-ignored")
    monkeypatch.setenv("HYPERSET_OLLAMA_MAX_TOKENS", "999")
    set_active_settings(
        {
            "providers": {
                "ollama": {"model": "cfg-ollama-model", "max_tokens": 512},
                "openai": {
                    "base_url": "https://cfg-gw/v1",
                    "model": "cfg-openai-model",
                    "reasoning_effort": "low",
                    "max_completion_tokens": 4096,
                },
            }
        }
    )
    assert openai_base_url() == "https://cfg-gw/v1"
    assert openai_model() == "cfg-openai-model"
    assert openai_reasoning_effort() == "low"
    assert openai_max_completion_tokens() == 4096  # a validated int, not the env string
    assert ollama_model() == "cfg-ollama-model"
    assert ollama_max_tokens() == 512


def test_openai_api_key_is_secret_end_to_end(monkeypatch):
    for var in _OPENAI_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HYPERSET_OPENAI_API_KEY", "sk-SECRETOPENAI")
    env = dict(__import__("os").environ)
    # The legacy secret var folds as a ${env:VAR} reference, never plaintext.
    assert (
        legacy_env_overlay(env)["providers"]["openai"]["api_key"]
        == "${env:HYPERSET_OPENAI_API_KEY}"
    )
    settings = resolve_secrets(load_settings(env=env), env=env)
    assert isinstance(settings["providers"]["openai"]["api_key"], Secret)
    assert "SECRETOPENAI" not in str(redact_settings(settings))
    set_active_settings(settings)
    assert openai_api_key() == "sk-SECRETOPENAI"  # revealed only at the read


def test_the_strict_int_token_vars_are_not_folded_so_a_bad_env_stays_lenient(monkeypatch):
    # The schema homes are Int(minimum=1). A non-integer legacy value must NOT be folded (it
    # would turn a lenient fallback into a fatal ConfigError); the config still loads, and the
    # accessor hands the raw string to the caller to parse leniently.
    for var in _OPENAI_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "not-an-int")
    monkeypatch.setenv("HYPERSET_OLLAMA_MAX_TOKENS", "-5")
    env = dict(__import__("os").environ)
    overlay = legacy_env_overlay(env)
    assert "max_completion_tokens" not in overlay.get("providers", {}).get("openai", {})
    assert "max_tokens" not in overlay.get("providers", {}).get("ollama", {})
    load_settings(env=env)  # does not raise -- the bad values never reach Int validation
    assert openai_max_completion_tokens() == "not-an-int"
    assert ollama_max_tokens() == "-5"


# The ollama base_url has NO base.yaml default (hy-ojwhg critic). `serve http/mcp` ALWAYS run
# apply_startup_config, so a BARE (non-Docker) operator is on the ACTIVE path too -- a base.yaml
# host.docker.internal would regress the legacy 127.0.0.1 there (it does not resolve on bare
# metal). So bare serve with the env unset resolves to None here and the caller applies its
# 127.0.0.1 code default; Docker supplies HYPERSET_OLLAMA_BASE_URL via compose, which folds and
# wins. These pin BOTH ends so the Docker default cannot silently leak into a bare serve again.
_DOCKER_OLLAMA = "http://host.docker.internal:11434"


def test_active_path_bare_serve_ollama_falls_back_to_the_code_default(monkeypatch):
    from hyperset.config.startup import apply_startup_config
    from hyperset.evals.run import DEFAULT_BASE_URL

    monkeypatch.delenv("HYPERSET_OLLAMA_BASE_URL", raising=False)
    apply_startup_config(env={})  # bare serve: base.yaml has no ollama default, nothing folded
    assert ollama_base_url() is None  # -> the caller applies its code default
    assert DEFAULT_BASE_URL == "http://127.0.0.1:11434/v1"  # the legacy bare-metal default


def test_active_path_docker_env_serves_the_container_ollama():
    from hyperset.config.startup import apply_startup_config

    # docker-compose.yml sets HYPERSET_OLLAMA_BASE_URL, which folds over base and wins.
    apply_startup_config(env={"HYPERSET_OLLAMA_BASE_URL": _DOCKER_OLLAMA})
    assert ollama_base_url() == _DOCKER_OLLAMA


def test_no_startup_ollama_base_url_is_none_so_the_caller_applies_its_code_default(monkeypatch):
    monkeypatch.delenv("HYPERSET_OLLAMA_BASE_URL", raising=False)
    assert ollama_base_url() is None


def test_base_yaml_sets_no_provider_endpoint_that_would_leak_into_a_bare_serve():
    """base.yaml must not carry a Docker-shaped endpoint default that a migrated accessor reads
    (hy-ojwhg critic). `serve http/mcp` always run apply_startup_config, so any such default is
    on the ACTIVE path for a bare operator too. AUDIT of base.yaml against the migrated
    accessors: providers.ollama.base_url -> removed here (was the regression). The other base
    keys a migrated accessor reads -- features.pii_guard and playground.enabled -- default to
    `false`, IDENTICAL to their code default (guard off, playground off), so they cannot shift a
    bare serve. models.planner/embedding and features.discover/expand/review have NO migrated
    accessor (nothing reads them off the settings object), so they are inert. This asserts the
    endpoint keys stay absent so the audit cannot silently rot."""
    import yaml

    from hyperset.config import BASE_CONFIG

    base = yaml.safe_load(BASE_CONFIG.read_text())
    providers = base.get("providers") or {}
    assert "base_url" not in (providers.get("ollama") or {}), (
        "config/base.yaml sets providers.ollama.base_url again; on the active path a bare "
        "`serve` would serve it instead of the 127.0.0.1 code default (hy-ojwhg)"
    )
    # No provider endpoint/base_url default belongs in base at all -- the Docker value comes from
    # compose's env var, which folds over base.
    for section in (providers.get("ollama") or {}, providers.get("openai") or {}):
        assert "base_url" not in section, (
            "a provider base_url default in base.yaml leaks to bare serve"
        )
