"""The DB domain (system + analytics) reads through the settings object, behaviour-preserving
and secret-safe (hy-2562h, config slice 3b).

Both URLs are SECRET -- the whole DSN embeds a password -- so a runtime `HYPERSET_*_URL` is
folded into the config as a `${env:VAR}` REFERENCE (never plaintext), resolved by slice-2 into
a write-only `Secret`, and revealed only at the read. These pin: the SAME DSN resolves across
set / unset / default with and without startup; an unset system DB still fails loudly; the
plaintext DSN never enters the config or a redacted dump.
"""

from __future__ import annotations

import pytest

from hyperset.config import (
    Secret,
    analytics_db_url,
    legacy_env_overlay,
    load_settings,
    redact_settings,
    resolve_secrets,
)
from hyperset.config.runtime import set_active_settings
from hyperset.config.startup import apply_startup_config
from hyperset.db.engine import database_url_from_env

_DB_ENV = "HYPERSET_DATABASE_URL"
_ANALYTICS_ENV = "HYPERSET_ANALYTICS_DB_URL"
_DSN = "postgresql+psycopg://user:s3cretpw@db.internal/hyperset"
_WH = "postgresql://a:apw@warehouse/wh"


# --- no startup (unit test / non-serving CLI): the EXACT legacy env read --------------------


def test_without_startup_the_legacy_db_env_still_drives_the_value(monkeypatch):
    monkeypatch.setenv(_DB_ENV, _DSN)
    assert database_url_from_env() == _DSN
    monkeypatch.delenv(_DB_ENV, raising=False)
    with pytest.raises(RuntimeError):  # unset system DB still fails loudly
        database_url_from_env()


def test_without_startup_analytics_is_the_legacy_env_or_none(monkeypatch):
    monkeypatch.delenv(_ANALYTICS_ENV, raising=False)
    assert analytics_db_url() is None
    monkeypatch.setenv(_ANALYTICS_ENV, _WH)
    assert analytics_db_url() == _WH


# --- the legacy overlay folds a SECRET var as a reference, never plaintext ------------------


def test_a_secret_runtime_var_folds_as_an_env_reference_not_plaintext(capsys):
    overlay = legacy_env_overlay({_DB_ENV: _DSN, _ANALYTICS_ENV: _WH})
    # The DSN plaintext is NOT in the overlay -- only a ${env:VAR} reference.
    assert overlay["connections"]["system_db"]["url"] == "${env:HYPERSET_DATABASE_URL}"
    assert overlay["connections"]["analytics_db"]["url"] == "${env:HYPERSET_ANALYTICS_DB_URL}"
    assert "s3cretpw" not in str(overlay)
    assert capsys.readouterr().err == ""


# --- startup: the ref resolves to a Secret and the read reveals it --------------------------


def test_startup_resolves_the_db_ref_to_a_write_only_secret_and_reveals_it(monkeypatch):
    monkeypatch.setenv(_DB_ENV, _DSN)
    settings = resolve_secrets(
        load_settings(env=dict(__import__("os").environ)), env=dict(__import__("os").environ)
    )
    # The config holds a write-only Secret, never the plaintext DSN.
    assert isinstance(settings["connections"]["system_db"]["url"], Secret)
    assert "s3cretpw" not in str(redact_settings(settings))
    # The read reveals the same DSN the legacy env carried -- behaviour-preserving.
    set_active_settings(settings)
    assert database_url_from_env() == _DSN


def test_startup_analytics_reveals_or_reports_unconfigured():
    set_active_settings({"connections": {"analytics_db": {"url": Secret("${env:X}", _WH)}}})
    assert analytics_db_url() == _WH
    set_active_settings({"connections": {}})  # startup ran, analytics not configured
    assert analytics_db_url() is None


def test_apply_startup_config_wires_the_legacy_db_var_end_to_end(monkeypatch):
    monkeypatch.setenv(_DB_ENV, _DSN)
    apply_startup_config(env=dict(__import__("os").environ))
    # A deployment still setting HYPERSET_DATABASE_URL keeps working, now sourced from the
    # resolved settings object, and the read reveals the same DSN.
    assert database_url_from_env() == _DSN
