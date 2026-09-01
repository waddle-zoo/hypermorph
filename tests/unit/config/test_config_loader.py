"""The layered config loader (hy-7h00, ADR-0035): the YAML trust boundary, the
deterministic deep-merge, the fail-closed typed schema, and the base+overlay
orchestration with NO silent demo fallback.
"""

from __future__ import annotations

import pytest

from hyperset.config import BASE_CONFIG, ConfigError, load_settings
from hyperset.config.loader import load_config_mapping, read_config_file
from hyperset.config.merge import deep_merge
from hyperset.config.schema import validate

# --- YAML trust boundary -----------------------------------------------------


def test_plain_scalars_stay_strings_no_implicit_typing():
    # The footguns the schema (not YAML) must own: no/yes->bool, unquoted version,
    # `1:30`->sexagesimal int. All stay strings here.
    parsed = load_config_mapping("a: no\nb: 8080\nc: 1:30\nd: yes\ne: 2.5\n", source="t")
    assert parsed == {"a": "no", "b": "8080", "c": "1:30", "d": "yes", "e": "2.5"}


def test_an_explicit_null_stays_null_for_unset():
    assert load_config_mapping("a: null\nb: ~\n", source="t") == {"a": None, "b": None}


def test_duplicate_mapping_keys_are_rejected():
    with pytest.raises(ConfigError) as raised:
        load_config_mapping("a: 1\na: 2\n", source="t")
    assert "duplicate key" in str(raised.value)


def test_anchors_and_aliases_are_rejected():
    with pytest.raises(ConfigError) as anchor:
        load_config_mapping("a: &x 1\nb: 2\n", source="t")
    assert "anchor" in str(anchor.value)
    with pytest.raises(ConfigError) as alias:
        load_config_mapping("a: &x 1\nb: *x\n", source="t")
    # Either the anchor or the alias trips first; both are refused.
    assert "anchor" in str(alias.value) or "alias" in str(alias.value)


@pytest.mark.parametrize(
    "doc",
    [
        "a: !!int 5\n",  # an explicit core-type tag would coerce -- rejected
        "a: !!bool no\n",
        "v: !!python/object/apply:os.system ['id']\n",  # arbitrary construction
        "a: !custom 1\n",
    ],
)
def test_explicit_and_custom_tags_are_rejected(doc):
    with pytest.raises(ConfigError):
        load_config_mapping(doc, source="t")


def test_an_empty_or_non_mapping_document_is_refused():
    with pytest.raises(ConfigError):
        load_config_mapping("", source="t")
    with pytest.raises(ConfigError):
        load_config_mapping("- just\n- a\n- list\n", source="t")


def test_a_missing_overlay_file_is_fatal_never_skipped(tmp_path):
    with pytest.raises(ConfigError) as raised:
        read_config_file(tmp_path / "does-not-exist.yaml")
    assert "not found" in str(raised.value)


# --- deep-merge --------------------------------------------------------------


def test_deep_merge_merges_mappings_and_replaces_scalars_and_lists():
    base = {"server": {"bind": "loopback", "api_port": "8080"}, "list": ["a", "b"]}
    overlay = {"server": {"bind": "all"}, "list": ["c"]}
    merged = deep_merge(base, overlay)
    # Mapping deep-merged (api_port kept), scalar replaced, list replaced WHOLESALE.
    assert merged == {"server": {"bind": "all", "api_port": "8080"}, "list": ["c"]}
    assert base["server"]["bind"] == "loopback"  # inputs not mutated


def test_an_explicit_null_in_an_overlay_unsets_a_base_key():
    merged = deep_merge(
        {"auth": {"enabled": "true", "oidc": {"issuer": "x"}}}, {"auth": {"oidc": None}}
    )
    assert merged == {"auth": {"enabled": "true"}}


# --- typed schema, fail-closed ----------------------------------------------


def _minimal():
    return {"server": {"bind": "loopback"}}


def test_the_shipped_base_config_validates():
    settings = load_settings(env={})
    assert settings["server"]["bind"] == "loopback"
    assert settings["auth"]["enabled"] is False  # coerced str->bool
    assert settings["server"]["api_port"] == 8080  # coerced str->int


def test_an_unknown_key_is_rejected_naming_the_path():
    with pytest.raises(ConfigError) as raised:
        validate({"server": {"bind": "loopback"}, "nonsense": {}})
    assert "unknown config key" in str(raised.value) and "nonsense" in str(raised.value)
    with pytest.raises(ConfigError) as nested:
        validate({"server": {"bind": "loopback", "bogus": "x"}})
    assert "bogus" in str(nested.value)


def test_a_missing_required_key_is_rejected():
    with pytest.raises(ConfigError) as raised:
        validate({"server": {"api_port": "8080"}})  # no bind
    assert "missing required" in str(raised.value) and "bind" in str(raised.value)


def test_a_wrong_type_is_rejected():
    with pytest.raises(ConfigError):
        validate(_minimal() | {"auth": {"enabled": "maybe"}})  # not true/false
    with pytest.raises(ConfigError):
        validate({"server": {"bind": "loopback", "api_port": "80x"}})  # not int
    with pytest.raises(ConfigError):
        validate({"server": {"bind": "sideways"}})  # not in enum


def test_bool_does_not_accept_the_yaml_footgun_words():
    for footgun in ("yes", "no", "on", "off", "1", "0"):
        with pytest.raises(ConfigError):
            validate(_minimal() | {"auth": {"enabled": footgun}})
    assert validate(_minimal() | {"auth": {"enabled": "true"}})["auth"]["enabled"] is True


def test_a_secret_ref_field_refuses_a_plaintext_or_half_inlined_value():
    ok = validate(_minimal() | {"connections": {"datahub": {"token": "${secret:datahub_token}"}}})
    assert ok["connections"]["datahub"]["token"] == "${secret:datahub_token}"
    assert validate(_minimal() | {"connections": {"datahub": {"token": "${env:X}"}}})
    for bad in ("plaintext-token", "prefix ${secret:x}", "${secret:x} suffix", "${vault:x}"):
        with pytest.raises(ConfigError) as raised:
            validate(_minimal() | {"connections": {"datahub": {"token": bad}}})
        assert "reference" in str(raised.value)


# --- orchestration: base always, overlays ordered, no demo fallback ---------


def test_unset_config_env_is_base_only_no_demo_fallback():
    settings = load_settings(env={})
    # The demo posture (playground on) is NOT reached implicitly.
    assert settings["playground"]["enabled"] is False


def test_the_demo_overlay_is_an_explicit_selection():
    settings = load_settings(env={"HYPERSET_CONFIG": "config/demo.yaml"})
    assert settings["playground"]["enabled"] is True


def test_overlays_apply_in_order_last_wins(tmp_path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("server: { log_level: debug }\n")
    second.write_text("server: { log_level: warning }\n")
    settings = load_settings(env={"HYPERSET_CONFIG": f"{first}:{second}"})
    assert settings["server"]["log_level"] == "warning"


def test_base_is_never_named_in_config_env_and_a_named_missing_overlay_is_fatal(tmp_path):
    # base.yaml is loaded implicitly; a named overlay that does not exist is fatal.
    with pytest.raises(ConfigError):
        load_settings(env={"HYPERSET_CONFIG": str(tmp_path / "absent.yaml")})


def test_base_config_path_is_the_checked_in_file():
    assert BASE_CONFIG.name == "base.yaml" and BASE_CONFIG.exists()
