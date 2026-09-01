"""A context-adapter.yaml is parsed and VALIDATED, never applied (hy-9keh, #283).

The design surface the panel guards: an unmapped/unknown key is an ERROR (never a
silent drop); every `| transform` resolves to the closed whitelist; the parser
FABRICATES nothing (an absent optional field stays absent, not a guessed default);
and it does NOT apply -- a mapping over a path that resolves to nothing still
parses, because no corpus is read here.
"""

from __future__ import annotations

import pytest

from hyperset.context.adapter import schema
from hyperset.context.adapter.schema import AdapterValidationError, parse_adapter

_FULL = """
schema_version: 1
adapter: acme-pipeline-docs-v2
discover:
  unit: "*/projects/*"
  manifest: "project.md"
  context_doc: "project.md"
  evals: "evals/cases/*.yaml"
map:
  domain: "$.urn | slug"
  title: "$.title"
  owners: "$.owners[*] | prefix('team:')"
  definitions:
    from: "../../concepts/*.md"
    term: "$.urn | slug"
    statement: "$.description"
"""


def _reasons(source) -> list[str]:
    with pytest.raises(AdapterValidationError) as exc:
        parse_adapter(source)
    return exc.value.reasons


def _minimal(**over) -> str:
    body = {
        "schema_version": "1",
        "adapter": "adapter: a",
        "discover": "discover:\n  unit: u\n  manifest: m\n  context_doc: c",
        "map": "map:\n  domain: '$.d'\n  title: '$.t'",
    }
    body.update(over)
    return (
        f"schema_version: {body['schema_version']}\n"
        f"{body['adapter']}\n{body['discover']}\n{body['map']}\n"
    )


# --- the happy path parses the whole #283 example ---


def test_the_reference_adapter_parses():
    spec = parse_adapter(_FULL)
    assert spec.schema_version == 1
    assert spec.adapter == "acme-pipeline-docs-v2"
    assert spec.discover["unit"] == "*/projects/*"
    assert spec.discover["evals"] == "evals/cases/*.yaml"
    assert spec.domain.path == "$.urn"
    assert [s.name for s in spec.domain.steps] == ["slug"]
    assert spec.title.steps == ()  # a bare path, no transform
    assert [s.name for s in spec.owners.steps] == ["prefix"]
    assert spec.definitions.source == "../../concepts/*.md"
    assert [s.name for s in spec.definitions.term.steps] == ["slug"]


def test_an_already_loaded_mapping_is_accepted():
    import yaml

    spec = parse_adapter(yaml.safe_load(_FULL))
    assert spec.adapter == "acme-pipeline-docs-v2"


# --- unmapped/unknown key is an ERROR at every level (no silent drop) ---


def test_an_unknown_top_level_key_is_an_error():
    reasons = _reasons(_minimal() + "surprise: x\n")
    assert any("unknown key 'surprise'" in r and "unmapped key is an error" in r for r in reasons)


def test_an_unknown_discover_key_is_an_error():
    reasons = _reasons(
        _minimal(discover="discover:\n  unit: u\n  manifest: m\n  context_doc: c\n  extra: e")
    )
    assert any("discover: unknown key 'extra'" in r for r in reasons)


def test_a_map_field_from_a_later_slice_is_an_error_not_silently_ignored():
    # approved_sources / caveats are in #283's fuller example but NOT this slice.
    # Unmapped-is-error means they are refused now, not tolerated.
    reasons = _reasons(_minimal(map="map:\n  domain: '$.d'\n  title: '$.t'\n  caveats: '$.c'"))
    assert any("map: unknown key 'caveats'" in r for r in reasons)


def test_an_unknown_definitions_key_is_an_error():
    m = (
        "map:\n  domain: '$.d'\n  title: '$.t'\n  definitions:\n"
        "    from: f\n    term: '$.x'\n    statement: '$.y'\n    note: n"
    )
    reasons = _reasons(_minimal(map=m))
    assert any("map.definitions: unknown key 'note'" in r for r in reasons)


# --- every transform resolves to the whitelist; an unknown one is an error ---


@pytest.mark.parametrize(
    "name", ["slug", "prefix", "default", "one_line", "catalog_urn_to_source_ref"]
)
def test_each_whitelisted_transform_resolves(name):
    spec = parse_adapter(_minimal(map=f"map:\n  domain: '$.d | {name}'\n  title: '$.t'"))
    assert [s.name for s in spec.domain.steps] == [name]


def test_an_unknown_transform_is_an_error_naming_the_whitelist():
    reasons = _reasons(_minimal(map="map:\n  domain: '$.d | frobnicate'\n  title: '$.t'"))
    assert any("unknown transform 'frobnicate'" in r and "slug" in r for r in reasons)


def test_a_transform_reference_never_reaches_an_attribute_lookup():
    # A dunder name must be refused as an unknown transform, never resolved.
    reasons = _reasons(_minimal(map="map:\n  domain: '$.d | __class__'\n  title: '$.t'"))
    assert any("unknown transform '__class__'" in r for r in reasons)


# --- schema_version and required fields ---


def test_schema_version_must_be_one():
    assert any("schema_version must be 1" in r for r in _reasons(_minimal(schema_version="2")))
    body = (
        "adapter: a\ndiscover:\n  unit: u\n  manifest: m\n  context_doc: c\n"
        "map:\n  domain: '$.d'\n  title: '$.t'\n"
    )
    assert any("schema_version must be 1" in r for r in _reasons(body))


def test_the_adapter_id_is_required():
    body = (
        "schema_version: 1\ndiscover:\n  unit: u\n  manifest: m\n  context_doc: c\n"
        "map:\n  domain: '$.d'\n  title: '$.t'\n"
    )
    assert any("adapter must be a non-empty string" in r for r in _reasons(body))


def test_the_core_discover_selectors_are_required_evals_is_optional():
    reasons = _reasons(_minimal(discover="discover:\n  manifest: m\n  context_doc: c"))
    assert any("discover.unit is required" in r for r in reasons)
    # evals absent is fine.
    spec = parse_adapter(_minimal())
    assert "evals" not in spec.discover


def test_domain_and_title_are_required_owners_and_definitions_optional():
    reasons = _reasons(_minimal(map="map:\n  title: '$.t'"))
    assert any("map.domain is required" in r for r in reasons)
    spec = parse_adapter(_minimal())  # no owners, no definitions
    assert spec.owners is None and spec.definitions is None  # absent, not fabricated


# --- mapping-expression well-formedness ---


def test_a_source_path_must_start_with_a_dollar():
    reasons = _reasons(_minimal(map="map:\n  domain: 'd | slug'\n  title: '$.t'"))
    assert any("must start with '$'" in r for r in reasons)


def test_a_pipe_inside_a_quoted_argument_does_not_split_the_step():
    # default('a|b') is ONE step naming `default`, not two -- the '|' is data.
    spec = parse_adapter(_minimal(map="map:\n  domain: \"$.d | default('a|b')\"\n  title: '$.t'"))
    assert [s.name for s in spec.domain.steps] == ["default"]
    assert spec.domain.steps[0].args_raw == "('a|b')"


def test_an_unterminated_quote_is_a_malformed_error():
    reasons = _reasons(_minimal(map="map:\n  domain: \"$.d | default('oops)\"\n  title: '$.t'"))
    assert any("unterminated quote" in r for r in reasons)


def test_a_malformed_transform_step_is_an_error():
    reasons = _reasons(_minimal(map="map:\n  domain: '$.d | 9bad'\n  title: '$.t'"))
    assert any("malformed transform step" in r for r in reasons)


# --- fail-safe shape: every reason at once, and NOT applied ---


def test_all_reasons_are_collected_not_only_the_first():
    body = _minimal(map="map:\n  domain: '$.d | frobnicate'\n  title: 'bad-path'") + "mystery: 1\n"
    reasons = _reasons(body)
    assert len(reasons) >= 3  # unknown transform + bad path + unknown top key


def test_parse_does_not_apply_a_mapping_over_a_path_that_resolves_to_nothing():
    # No corpus is read: an expression whose path would match nothing still parses,
    # because validation checks SHAPE, not the customer's data (apply is 283-4).
    spec = parse_adapter(_minimal(map="map:\n  domain: '$.does.not.exist | slug'\n  title: '$.t'"))
    assert spec.domain.path == "$.does.not.exist"


def test_the_adapter_file_carries_its_own_schema_version_constant():
    assert schema.SCHEMA_VERSION == 1
