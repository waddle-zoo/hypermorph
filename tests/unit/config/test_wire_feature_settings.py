"""The features/PII domain reads through the settings object, behaviour-preserving (hy-e16vx,
config slice 3f).

The PII guard's master switch and its action/entities/spaCy settings resolve one configuration on
both write-back boundaries. `pii_guard` folds NORMALIZED (its schema home is Bool and base.yaml
sets features.pii_guard: false), which is REQUIRED so a lenient `on`/`1` env can still ENGAGE the
guard once startup loaded the config. These exercise the ACTIVE startup path (apply_startup_config).
"""

from __future__ import annotations

import pytest

from hyperset.config import (
    load_settings,
    pii_action,
    pii_entities,
    pii_guard,
    pii_spacy_model,
)
from hyperset.config.runtime import set_active_settings
from hyperset.config.startup import apply_startup_config

_VARS = (
    "HYPERSET_PII_GUARD",
    "HYPERSET_PII_ACTION",
    "HYPERSET_PII_ENTITIES",
    "HYPERSET_PII_SPACY_MODEL",
)


def test_without_startup_the_legacy_env_drives_every_read(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    assert pii_guard() is False
    assert pii_action() is None
    assert pii_entities() is None
    assert pii_spacy_model() is None

    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")  # lenient: 'on', not just true/false
    monkeypatch.setenv("HYPERSET_PII_ACTION", "block")
    monkeypatch.setenv("HYPERSET_PII_ENTITIES", "EMAIL_ADDRESS,PHONE_NUMBER")
    monkeypatch.setenv("HYPERSET_PII_SPACY_MODEL", "en_core_web_sm")
    assert pii_guard() is True
    assert pii_action() == "block"
    assert pii_entities() == "EMAIL_ADDRESS,PHONE_NUMBER"
    assert pii_spacy_model() == "en_core_web_sm"


@pytest.mark.parametrize("raw", ["on", "ON", "1", "true", "yes", "Yes"])
def test_pii_guard_is_lenient_without_startup(monkeypatch, raw):
    monkeypatch.setenv("HYPERSET_PII_GUARD", raw)
    assert pii_guard() is True


@pytest.mark.parametrize("raw", ["off", "0", "false", "no", ""])
def test_pii_guard_is_disengaged_for_a_non_engaging_value(monkeypatch, raw):
    monkeypatch.setenv("HYPERSET_PII_GUARD", raw)
    assert pii_guard() is False


def test_with_startup_the_settings_object_drives_the_reads(monkeypatch):
    monkeypatch.setenv("HYPERSET_PII_ACTION", "should-be-ignored")
    set_active_settings(
        {
            "features": {
                "pii_guard": True,
                "pii": {"action": "block", "entities": "PERSON", "spacy_model": "cfg-model"},
            }
        }
    )
    assert pii_guard() is True
    assert pii_action() == "block"
    assert pii_entities() == "PERSON"
    assert pii_spacy_model() == "cfg-model"


def test_action_present_empty_is_preserved_as_redact(monkeypatch):
    # os.environ.get semantics: present-empty -> "", absent -> None; either resolves to redact.
    monkeypatch.setenv("HYPERSET_PII_ACTION", "")
    assert pii_action() == ""
    monkeypatch.delenv("HYPERSET_PII_ACTION", raising=False)
    assert pii_action() is None


def test_active_path_env_engages_the_guard_over_base_false():
    # THE regression: apply_startup_config loads base.yaml (features.pii_guard: false) AND folds
    # HYPERSET_PII_GUARD=on NORMALIZED to true, so the lenient value both validates and wins.
    settings = apply_startup_config(env={"HYPERSET_PII_GUARD": "on"})
    assert settings["features"]["pii_guard"] is True
    assert pii_guard() is True


def test_active_path_unset_leaves_the_guard_off():
    apply_startup_config(env={})  # base.yaml default false
    assert pii_guard() is False


def test_active_path_explicit_off_disables():
    apply_startup_config(env={"HYPERSET_PII_GUARD": "off"})
    assert pii_guard() is False


def test_pii_guard_fold_normalizes_to_a_legal_bool(monkeypatch):
    for var in _VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HYPERSET_PII_GUARD", "on")
    env = dict(__import__("os").environ)
    from hyperset.config import legacy_env_overlay

    assert legacy_env_overlay(env)["features"]["pii_guard"] == "true"
    load_settings(env=env)  # validates: 'true' is a legal Bool (raw 'on' would not be)
