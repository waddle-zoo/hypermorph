"""The served playground gate and the playground UI process read their config through the
settings object, not a second raw os.environ path (hy-lk83s -- no split-brain).

`hyperset/transport/http.py._playground_enabled` and `playground/ui/app.py` both run in the
serving deployment; a var migrated for one but read raw in the other would resolve differently.
These pin that the SAME loaded config drives both -- and that with no startup the legacy env is
still honored.
"""

from __future__ import annotations

from hyperset.config.runtime import set_active_settings
from hyperset.transport.http import _playground_enabled
from playground.ui import app
from playground.ui.app import (
    _configured_agent_profiles,
    _configured_models,
    _playground_runtime_config,
    _upstream_base_url,
    _upstream_target,
)


def test_served_gate_reads_the_loaded_enabled(monkeypatch):
    # The gate is true from the loaded config even with the legacy env unset (and false the
    # same way), so the served surface and the UI process cannot disagree.
    monkeypatch.delenv("HYPERSET_PLAYGROUND_ENABLED", raising=False)
    set_active_settings({"playground": {"enabled": True}})
    assert _playground_enabled() is True
    set_active_settings({"playground": {"enabled": False}})
    assert _playground_enabled() is False


def test_proxy_target_is_verbatim_but_request_base_is_trimmed(monkeypatch):
    # The startup diagnostic print and the proxy concatenation use the VERBATIM upstream target,
    # byte-identical to the pre-migration os.environ.get(name, default) output -- a trailing
    # slash is PRESERVED. Only the request-base helper trims it, as the old _base_url did
    # (hy-lk83s adversary: the log must not rstrip).
    monkeypatch.setenv("HYPERSET_HTTP_BASE_URL", "http://upstream:8000/")
    assert _upstream_target() == "http://upstream:8000/"  # verbatim -- slash kept, as before
    assert _upstream_base_url() == "http://upstream:8000"  # trimmed for path concatenation

    monkeypatch.setenv("HYPERSET_HTTP_BASE_URL", "")  # present-empty stays empty, as before
    assert _upstream_target() == ""


def test_served_gate_active_path_env_re_enables_over_base_false():
    # The full active path: apply_startup_config loads base.yaml (playground.enabled: false) and
    # folds HYPERSET_PLAYGROUND_ENABLED=1. The served gate must come out true -- the regression
    # the adversary caught (base-false was silently defeating the env).
    from hyperset.config.startup import apply_startup_config

    apply_startup_config(env={"HYPERSET_PLAYGROUND_ENABLED": "1"})
    assert _playground_enabled() is True


def test_app_reads_agents_models_and_upstream_from_settings(monkeypatch):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_AGENTS_JSON", "[]")  # must lose to the loaded config
    monkeypatch.setenv("HYPERSET_HTTP_BASE_URL", "http://ignored:9/")
    set_active_settings(
        {
            "playground": {
                "agents": [
                    {"value": "cfg", "label": "Cfg agent", "description": "d", "instruction": "i"}
                ],
                "models": [{"value": "cfg-model", "provider": "local"}],
                "default_agent": "cfg",
                "default_model": "cfg-model",
                "upstream_base_url": "http://cfg-upstream:8000/",
            }
        }
    )
    assert set(_configured_agent_profiles()) == {"cfg"}
    assert _configured_models() == [
        {"value": "gpt-5.6-luna", "label": "gpt-5.6-luna · openai", "provider": "openai"}
    ]
    runtime = _playground_runtime_config()
    assert runtime["default_agent"] == "cfg" and runtime["default_model"] == "gpt-5.6-luna"
    assert _upstream_base_url() == "http://cfg-upstream:8000"  # trailing slash trimmed as before


def test_without_startup_the_legacy_env_still_drives_app_reads(monkeypatch):
    monkeypatch.delenv("HYPERSET_PLAYGROUND_MODELS_JSON", raising=False)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_AGENTS_JSON", '[{"value": "envagent"}]')
    monkeypatch.setenv("HYPERSET_HTTP_BASE_URL", "http://legacy-upstream:8000")
    assert set(_configured_agent_profiles()) == {"envagent"}
    assert _upstream_base_url() == "http://legacy-upstream:8000"
    assert app.playground_upstream_base_url() == "http://legacy-upstream:8000"
