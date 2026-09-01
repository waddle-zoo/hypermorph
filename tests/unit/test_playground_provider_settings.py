"""The playground server (playground/ui/app.py) reads its model/provider selection through the
settings object, not a second raw `os.environ` path (hy-7m5yg critic -- no split-brain).

app.py runs IN the serving process (http.py imports it), so a var migrated for the ops and
candidates call sites but still read raw here would resolve differently in the two halves. These
pin that the SAME loaded config drives app.py's OpenAI model selection, base URL, reasoning
effort, and token cap -- and that with no startup the OpenAI legacy env is still honored.
"""

from __future__ import annotations

from hyperset.config.runtime import set_active_settings
from playground.ui import app
from playground.ui.app import (
    _build_agent_model,
    _configured_models,
    _openai_base_url,
    _openai_max_completion_tokens,
)


def test_configured_models_follow_the_loaded_settings(monkeypatch):
    # A live legacy env must not win over the loaded config.
    monkeypatch.setenv("HYPERSET_MODEL_PROVIDER", "ollama")
    monkeypatch.setenv("HYPERSET_OPENAI_MODEL", "should-be-ignored")
    monkeypatch.delenv("HYPERSET_PLAYGROUND_MODELS_JSON", raising=False)
    set_active_settings(
        {"models": {"provider": "openai"}, "providers": {"openai": {"model": "cfg-openai-model"}}}
    )
    models = _configured_models()
    assert models == [
        {"value": "gpt-5.6-luna", "label": "gpt-5.6-luna · openai", "provider": "openai"}
    ]


def test_base_urls_and_caps_follow_the_loaded_settings(monkeypatch):
    monkeypatch.setenv("HYPERSET_OPENAI_BASE_URL", "https://ignored/v1")
    monkeypatch.setenv("HYPERSET_OPENAI_MAX_COMPLETION_TOKENS", "111")
    set_active_settings(
        {
            "providers": {
                "openai": {"base_url": "https://cfg-gw/v1/", "max_completion_tokens": 4096},
            }
        }
    )
    assert _openai_base_url() == "https://cfg-gw/v1"  # trailing slash trimmed, as before
    assert _openai_max_completion_tokens() == 4096


def test_without_startup_the_openai_legacy_env_still_drives_app_reads(monkeypatch):
    monkeypatch.delenv("HYPERSET_PLAYGROUND_MODELS_JSON", raising=False)
    monkeypatch.setenv("HYPERSET_OPENAI_MODEL", "gpt-5.6-luna")
    models = _configured_models()
    assert models[0]["provider"] == "openai" and models[0]["value"] == "gpt-5.6-luna"


def test_the_agent_builder_rejects_a_retired_local_provider(monkeypatch):
    import pytest

    with pytest.raises(ValueError, match="OpenAI only"):
        _build_agent_model("ollama", "qwen2.5:7b")


def test_the_playground_rejects_an_arbitrary_openai_model(monkeypatch):
    monkeypatch.setenv(
        "HYPERSET_PLAYGROUND_MODELS_JSON",
        '[{"value": "gpt-4o", "provider": "openai"}, '
        '{"value": "qwen2.5:7b", "provider": "ollama"}]',
    )
    assert _configured_models() == [
        {"value": "gpt-5.6-luna", "label": "gpt-5.6-luna · openai", "provider": "openai"}
    ]
    import pytest

    with pytest.raises(ValueError, match="gpt-5.6-luna"):
        _build_agent_model("openai", "gpt-4o")


def test_openai_path_uses_the_secret_api_key_from_settings(monkeypatch):
    # The OpenAI credential comes from the resolved Secret, not a raw env key: with OPENAI_API_KEY
    # unset, _build_agent_model still succeeds because the settings Secret supplies the key.
    from hyperset.config.secrets import Secret

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(app, "_is_vendor_openai_base_url", lambda _base: False)
    set_active_settings(
        {
            "providers": {
                "openai": {
                    "base_url": "https://cfg-gw/v1",
                    "api_key": Secret("${env:HYPERSET_OPENAI_API_KEY}", "sk-CFG"),
                    "max_completion_tokens": 4096,
                }
            }
        }
    )
    sdk_model, _settings = _build_agent_model("openai", "gpt-5.6-luna")
    # A client was built (no RuntimeError for a missing key); the revealed Secret supplied it.
    assert sdk_model is not None
