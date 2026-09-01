"""The `context` domain reads through the settings object, behaviour-preserving (hy-tc4o,
config slice 3).

The first domain migrated off scattered `os.environ.get`. These pin that the SAME value
resolves as before across set / unset / default whether or not startup ran, that a loaded
config (or a legacy env folded into it) drives the value once startup ran, and that a legacy
var is honored with a deprecation warning (the first, behaviour-preserving half of the staged
migration -- no reject-unknown here).
"""

from __future__ import annotations

import pytest

from hyperset.config import (
    ConfigError,
    active_settings,
    context_cache_dir,
    legacy_env_overlay,
    load_settings,
    set_active_settings,
)
from hyperset.config.context_settings import _default_cache_dir
from hyperset.config.startup import apply_startup_config
from hyperset.context.git import MAX_FILES, GitReadError, _max_files

_CACHE_ENV = "HYPERSET_CONTEXT_CACHE_DIR"
_MAX_ENV = "HYPERSET_CONTEXT_MAX_FILES"


# --- no startup (unit test / non-serving CLI): the EXACT legacy env read -------------------


def test_without_startup_the_legacy_env_still_drives_the_value(monkeypatch):
    monkeypatch.delenv(_CACHE_ENV, raising=False)
    monkeypatch.delenv(_MAX_ENV, raising=False)
    assert context_cache_dir() == _default_cache_dir()  # unset -> default
    assert _max_files() == MAX_FILES

    monkeypatch.setenv(_CACHE_ENV, "/tmp/legacy-cache")
    monkeypatch.setenv(_MAX_ENV, "7")
    assert context_cache_dir() == "/tmp/legacy-cache"  # set -> the env value
    assert _max_files() == 7


def test_without_startup_a_bad_max_files_still_fails_loudly(monkeypatch):
    monkeypatch.setenv(_MAX_ENV, "not-an-int")
    with pytest.raises(GitReadError):
        _max_files()
    monkeypatch.setenv(_MAX_ENV, "0")
    with pytest.raises(GitReadError):
        _max_files()


def _overlay_env(tmp_path, body: str, name: str) -> dict:
    path = tmp_path / name
    path.write_text(body)
    return {"HYPERSET_CONFIG": str(path)}


def test_max_files_zero_is_rejected_identically_on_the_config_path(tmp_path):
    # hy-tc4o critic: the legacy env path rejects HYPERSET_CONTEXT_MAX_FILES=0 (>= 1), so the
    # config path must reject `context.max_files: 0` too -- the schema bound is Int(minimum=1),
    # not 0. Otherwise 0 would silently pass once startup ran, a behaviour DIVERGENCE that
    # turns hy-gh-288's loud-cap intent into a silent zero cap.
    with pytest.raises(ConfigError) as raised:
        load_settings(env=_overlay_env(tmp_path, "context:\n  max_files: 0\n", "zero.yaml"))
    assert "context.max_files" in str(raised.value)
    # ...and 1 is accepted, so the boundary matches the legacy `>= 1` exactly.
    ok = load_settings(env=_overlay_env(tmp_path, "context:\n  max_files: 1\n", "one.yaml"))
    assert ok["context"]["max_files"] == 1


# --- startup ran: the value comes from the loaded settings object --------------------------


def test_with_startup_the_settings_object_drives_the_value():
    set_active_settings({"context": {"cache_dir": "/cfg/cache", "max_files": 9}})
    assert context_cache_dir() == "/cfg/cache"
    assert _max_files() == 9


def test_with_startup_but_no_context_section_uses_the_defaults(monkeypatch):
    # A live env var must NOT leak past the loaded config: once startup ran, the settings
    # object is authoritative, and an absent context section means the built-in defaults.
    monkeypatch.setenv(_CACHE_ENV, "/tmp/should-be-ignored")
    monkeypatch.setenv(_MAX_ENV, "999")
    set_active_settings({"server": {"bind": "loopback"}})
    assert context_cache_dir() == _default_cache_dir()
    assert _max_files() == MAX_FILES


# --- the legacy overlay: a legacy var folds into the config, warned once -------------------


def test_legacy_env_overlay_maps_and_warns_once(capsys):
    first = legacy_env_overlay({_CACHE_ENV: "/leg", _MAX_ENV: "3"})
    assert first == {"context": {"cache_dir": "/leg", "max_files": "3"}}
    err = capsys.readouterr().err
    assert "HYPERSET_CONTEXT_CACHE_DIR is deprecated" in err
    assert "context.cache_dir" in err
    # Warned ONCE per process: a second call maps but does not re-warn.
    legacy_env_overlay({_CACHE_ENV: "/leg"})
    assert "deprecated" not in capsys.readouterr().err


def test_an_unmigrated_env_produces_no_overlay():
    assert legacy_env_overlay({"HYPERSET_SOMETHING_ELSE": "x"}) == {}


def test_load_settings_folds_a_legacy_context_var(tmp_path, monkeypatch):
    monkeypatch.setenv(_CACHE_ENV, "/legacy/from/load")
    settings = load_settings(env=dict(__import__("os").environ))
    assert settings["context"]["cache_dir"] == "/legacy/from/load"


# --- end to end: apply_startup_config wires the legacy var through to the accessor ---------


def test_apply_startup_config_wires_the_legacy_var_through_to_the_reader(monkeypatch):
    monkeypatch.setenv(_CACHE_ENV, "/legacy/e2e")
    apply_startup_config(env=dict(__import__("os").environ))
    # The loaded settings carry it, and the migrated reader returns it -- a deployment still
    # setting the old env var keeps working, now sourced from the config.
    assert active_settings()["context"]["cache_dir"] == "/legacy/e2e"
    assert context_cache_dir() == "/legacy/e2e"
