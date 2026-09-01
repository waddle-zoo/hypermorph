"""The PII guard reads its configuration through the settings object, not a second raw os.environ
path (hy-e16vx -- no split-brain, active-path safe).

`security/pii.py` sits on two write-back boundaries; its master switch and action/entities/model
settings must resolve one way. These pin that the loaded config drives the guard's internal
readers, that the legacy env still works with no startup, and -- the regression -- that
HYPERSET_PII_GUARD engages the guard on the ACTIVE path despite base.yaml's features.pii_guard:
false.
"""

from __future__ import annotations

from hyperset.config.runtime import set_active_settings
from hyperset.config.startup import apply_startup_config
from hyperset.security import pii


def test_guard_internals_read_from_the_loaded_settings(monkeypatch):
    # A live legacy env must not win over the loaded config.
    monkeypatch.setenv("HYPERSET_PII_ACTION", "should-be-ignored")
    monkeypatch.setenv("HYPERSET_PII_ENTITIES", "IGNORED")
    set_active_settings(
        {
            "features": {
                "pii_guard": True,
                "pii": {
                    "action": "block",
                    "entities": "EMAIL_ADDRESS, PHONE_NUMBER",
                    "spacy_model": "cfg-model",
                },
            }
        }
    )
    assert pii._engaged() is True
    assert pii._action() == "block"
    assert pii._configured_entities() == ["EMAIL_ADDRESS", "PHONE_NUMBER"]
    assert pii._spacy_model() == "cfg-model"


def test_guard_internals_fall_back_to_the_legacy_env_without_startup(monkeypatch):
    monkeypatch.setenv("HYPERSET_PII_GUARD", "1")
    monkeypatch.setenv("HYPERSET_PII_ACTION", "block")
    monkeypatch.delenv("HYPERSET_PII_SPACY_MODEL", raising=False)
    assert pii._engaged() is True
    assert pii._action() == "block"
    # The pinned default still applies when the override is absent.
    assert pii._spacy_model() == pii._DEFAULT_SPACY_MODEL


def test_active_path_env_engages_the_guard_over_base_false():
    apply_startup_config(env={"HYPERSET_PII_GUARD": "on"})
    assert pii._engaged() is True


def test_active_path_unset_leaves_the_guard_off():
    apply_startup_config(env={})
    assert pii._engaged() is False
