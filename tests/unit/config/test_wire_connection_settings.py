"""The connection credentials read through the settings object, secret-safe and
behaviour-preserving (hy-py62a, config slice 3e).

One accessor per credential so the live-connector build path and the live detail-lookup cannot
authenticate two different ways. All three are SECRET -- they fold as `${env:VAR}` references,
resolve to write-only `Secret`s, and are revealed only at the read. These exercise the ACTIVE
startup path (apply_startup_config), not just load_settings.
"""

from __future__ import annotations

import re
from pathlib import Path

from hyperset.config import (
    Secret,
    datahub_token,
    redact_settings,
    superset_password,
    superset_username,
)
from hyperset.config.runtime import set_active_settings
from hyperset.config.startup import apply_startup_config

_VARS = ("HYPERSET_SUPERSET_USERNAME", "HYPERSET_SUPERSET_PASSWORD", "HYPERSET_DATAHUB_TOKEN")


def test_without_startup_the_legacy_env_drives_every_credential(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    assert superset_username() is None
    assert superset_password() is None
    assert datahub_token() is None

    monkeypatch.setenv("HYPERSET_SUPERSET_USERNAME", "admin")
    monkeypatch.setenv("HYPERSET_SUPERSET_PASSWORD", "pw")
    monkeypatch.setenv("HYPERSET_DATAHUB_TOKEN", "tok")
    assert superset_username() == "admin"
    assert superset_password() == "pw"
    assert datahub_token() == "tok"


def test_present_empty_is_preserved_not_collapsed(monkeypatch):
    # The prior read was os.environ.get(NAME): present-empty -> "", absent -> None. Preserve both
    # (a present-empty datahub token produced an empty token, not an absent one).
    monkeypatch.setenv("HYPERSET_DATAHUB_TOKEN", "")
    assert datahub_token() == ""
    monkeypatch.delenv("HYPERSET_DATAHUB_TOKEN", raising=False)
    assert datahub_token() is None


def test_with_startup_the_settings_object_drives_the_reads(monkeypatch):
    # A live env var must not leak past the loaded config.
    monkeypatch.setenv("HYPERSET_SUPERSET_USERNAME", "should-be-ignored")
    set_active_settings(
        {"connections": {"superset": {"username": "cfg-user", "password": "cfg-pw"}}}
    )
    assert superset_username() == "cfg-user"
    assert superset_password() == "cfg-pw"


def test_active_path_credentials_are_secret_end_to_end(monkeypatch):
    # apply_startup_config folds each legacy var as a ${env:VAR} reference, resolves it to a
    # write-only Secret, and reveals it only at the read; redaction never shows the plaintext.
    env = {
        "HYPERSET_SUPERSET_USERNAME": "admin",
        "HYPERSET_SUPERSET_PASSWORD": "pw-SECRETPW",
        "HYPERSET_DATAHUB_TOKEN": "tok-SECRETTOK",
    }
    settings = apply_startup_config(env=env)
    assert isinstance(settings["connections"]["superset"]["password"], Secret)
    assert isinstance(settings["connections"]["datahub"]["token"], Secret)
    rendered = str(redact_settings(settings))
    assert "SECRETPW" not in rendered and "SECRETTOK" not in rendered
    # revealed only at the read
    assert superset_username() == "admin"
    assert superset_password() == "pw-SECRETPW"
    assert datahub_token() == "tok-SECRETTOK"


def test_active_path_unset_leaves_credentials_absent(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    apply_startup_config(env={})  # no connections in base.yaml, nothing folded
    assert superset_username() is None
    assert superset_password() is None
    assert datahub_token() is None


# Repo root: tests/unit/config/<this file> -> parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]
# The SHIPPED source that runs Hyperset code, including the Compose entrypoints under docker/
# (datahub-seed / datahub-evidence) that the completeness grep must cover.
_SOURCE_DIRS = ("hyperset", "playground", "docker", "scripts")
# A raw `os.environ.get("HYPERSET_SUPERSET_USERNAME")` (or [...] indexing) for any connection
# credential -- the exact bypass this slice closes. The accessor uses a NAMED constant via
# `_env(env).get(...)`, never this literal, so it is not a match.
_RAW_CRED_READ = re.compile(
    r"""os\.environ(?:\.get)?\s*[\[(]\s*['"]"""
    r"""(HYPERSET_SUPERSET_USERNAME|HYPERSET_SUPERSET_PASSWORD|HYPERSET_DATAHUB_TOKEN)['"]"""
)


def test_no_connection_credential_is_read_raw_outside_the_accessor():
    """Exact-name sweep: NO shipped module reads a connection credential straight from
    os.environ. Any such read bypasses the settings/secret-ref boundary (hy-py62a adversary --
    the Compose entrypoints under docker/ were the miss). Repo-wide, not per-site."""
    offenders = []
    for name in _SOURCE_DIRS:
        for path in (_REPO_ROOT / name).rglob("*.py"):
            if "__pycache__" in path.parts or path.name == "connection_settings.py":
                continue
            for match in _RAW_CRED_READ.finditer(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(f"{path.relative_to(_REPO_ROOT)}: {match.group(0)}")
    assert offenders == [], (
        "connection credentials read raw from os.environ (bypass the settings/secret boundary):\n"
        + "\n".join(offenders)
    )
