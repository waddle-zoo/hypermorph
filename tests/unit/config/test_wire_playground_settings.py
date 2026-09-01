"""The playground domain reads through the settings object, behaviour-preserving (hy-lk83s,
config slice 3d).

One accessor per read so the served `_playground_enabled` gate and the playground UI process
cannot disagree. `enabled` folds NORMALIZED to `true`/`false` (its schema home is Bool), which is
REQUIRED because base.yaml defaults it false -- an unfolded env could never re-enable the
playground once startup loaded the config. These pin the SAME value across set / unset / default,
and specifically exercise the ACTIVE startup path (apply_startup_config), not just load_settings.
"""

from __future__ import annotations

import pytest

from hyperset.config import (
    load_settings,
    playground_agents_json,
    playground_default_agent,
    playground_default_model,
    playground_enabled,
    playground_models_json,
    playground_upstream_base_url,
)
from hyperset.config.runtime import set_active_settings

_VARS = (
    "HYPERSET_PLAYGROUND_ENABLED",
    "HYPERSET_PLAYGROUND_AGENTS_JSON",
    "HYPERSET_PLAYGROUND_MODELS_JSON",
    "HYPERSET_PLAYGROUND_DEFAULT_AGENT",
    "HYPERSET_PLAYGROUND_DEFAULT_MODEL",
    "HYPERSET_HTTP_BASE_URL",
)


def test_without_startup_the_legacy_env_drives_every_playground_read(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    assert playground_enabled() is False
    assert playground_agents_json() == "" and playground_models_json() == ""
    assert playground_default_agent() == "" and playground_default_model() == ""
    assert playground_upstream_base_url() is None

    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "yes")  # lenient, not just true/false
    monkeypatch.setenv("HYPERSET_PLAYGROUND_AGENTS_JSON", '[{"value": "a"}]')
    monkeypatch.setenv("HYPERSET_PLAYGROUND_MODELS_JSON", '[{"value": "m", "provider": "ollama"}]')
    monkeypatch.setenv("HYPERSET_PLAYGROUND_DEFAULT_AGENT", "a")
    monkeypatch.setenv("HYPERSET_PLAYGROUND_DEFAULT_MODEL", "m")
    monkeypatch.setenv("HYPERSET_HTTP_BASE_URL", "http://legacy-upstream:8000")
    assert playground_enabled() is True
    assert playground_agents_json() == '[{"value": "a"}]'
    assert playground_models_json() == '[{"value": "m", "provider": "ollama"}]'
    assert playground_default_agent() == "a" and playground_default_model() == "m"
    assert playground_upstream_base_url() == "http://legacy-upstream:8000"


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "Yes"])
def test_enabled_is_lenient_without_startup(monkeypatch, raw):
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", raw)
    assert playground_enabled() is True


def test_with_startup_the_settings_object_drives_the_reads(monkeypatch):
    # A live legacy env must not win over the loaded config.
    monkeypatch.setenv("HYPERSET_PLAYGROUND_DEFAULT_AGENT", "should-be-ignored")
    monkeypatch.setenv("HYPERSET_HTTP_BASE_URL", "http://ignored:9")
    set_active_settings(
        {
            "playground": {
                "enabled": True,
                "agents": [{"value": "cfg-agent"}],  # a YAML-native structure
                "default_agent": "cfg-agent",
                "default_model": "cfg-model",
                "upstream_base_url": "http://cfg-upstream:8000",
            }
        }
    )
    assert playground_enabled() is True
    # A native structure is re-serialized so the caller's json.loads still consumes it.
    assert playground_agents_json() == '[{"value": "cfg-agent"}]'
    assert playground_default_agent() == "cfg-agent"
    assert playground_default_model() == "cfg-model"
    assert playground_upstream_base_url() == "http://cfg-upstream:8000"


def test_enabled_is_folded_normalized_so_it_passes_bool_and_overrides_base(monkeypatch):
    # The schema home is Bool (true/false only) and base.yaml sets playground.enabled: false.
    # A lenient '1'/'yes' is folded NORMALIZED to 'true' so it both validates AND overrides the
    # base default -- otherwise the env could never re-enable the playground after startup.
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "1")
    env = dict(__import__("os").environ)
    from hyperset.config import legacy_env_overlay

    assert legacy_env_overlay(env)["playground"]["enabled"] == "true"
    settings = load_settings(env=env)  # validates: 'true' is a legal Bool
    assert settings["playground"]["enabled"] is True


def test_enabled_folds_false_for_a_non_truthy_value(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HYPERSET_PLAYGROUND_ENABLED", "0")
    env = dict(__import__("os").environ)
    from hyperset.config import legacy_env_overlay

    assert legacy_env_overlay(env)["playground"]["enabled"] == "false"


def test_active_path_env_re_enables_the_playground_over_base_false():
    # THE regression: apply_startup_config loads base.yaml (playground.enabled: false) AND folds
    # the legacy env. With the fold, HYPERSET_PLAYGROUND_ENABLED=yes wins; without it, base's
    # false would stick and the accessor would never consult the env.
    from hyperset.config.startup import apply_startup_config

    env = {"HYPERSET_PLAYGROUND_ENABLED": "yes"}
    apply_startup_config(env=env)
    assert playground_enabled() is True


def test_active_path_unset_leaves_the_playground_disabled():
    from hyperset.config.startup import apply_startup_config

    apply_startup_config(env={})  # base.yaml default
    assert playground_enabled() is False


def test_active_path_explicit_false_disables():
    from hyperset.config.startup import apply_startup_config

    apply_startup_config(env={"HYPERSET_PLAYGROUND_ENABLED": "0"})
    assert playground_enabled() is False


def test_active_path_upstream_absent_uses_default_present_empty_stays_empty():
    # The prior read os.environ.get(name, default) used the default ONLY when the var was ABSENT;
    # a present-empty value produced an empty base. Preserve both across the active startup path.
    # A present-empty value is not folded (blank), so the accessor consults the same env -- pass
    # it explicitly, the way the serving process's env IS the accessor's os.environ.
    from hyperset.config.startup import apply_startup_config

    absent: dict = {}
    apply_startup_config(env=absent)
    assert playground_upstream_base_url(absent) is None  # caller applies its default

    empty = {"HYPERSET_HTTP_BASE_URL": ""}
    apply_startup_config(env=empty)
    assert playground_upstream_base_url(empty) == ""  # NOT None -> caller keeps an empty base

    present = {"HYPERSET_HTTP_BASE_URL": "http://up:8000"}
    apply_startup_config(env=present)
    assert playground_upstream_base_url(present) == "http://up:8000"  # folded into settings
