"""Applying a validated adapter projects a customer corpus into v0 context (hy-s8up).

The design surface the panel guards: no silent-drop on LIVE data (an unmapped
source key is an error); the whitelisted transforms run on real values; nothing is
fabricated (an absent path is an error, not a guessed default); and a corpus with
NO adapter is parsed unchanged (back-compat). Provenance is exercised at the sync
seam: the snapshot commit is the corpus commit, never a build artifact.
"""

from __future__ import annotations

import pytest

from hyperset.context.adapter.apply import AdapterApplyError, apply_adapter, has_adapter

_ADAPTER = """
schema_version: 1
adapter: acme-pipeline-docs-v2
discover:
  unit: "projects/*"
  manifest: project.md
  context_doc: project.md
map:
  domain: "$.urn | slug"
  title: "$.title"
  owners: "$.owners[*] | prefix('team:')"
"""

_PROJECT = """---
urn: Revenue By Region
title: Revenue by Region
owners:
  - finance-data
  - revenue-eng
---
# Revenue by Region

How revenue is governed here.
"""


def _corpus(*, adapter=_ADAPTER, project=_PROJECT, **extra) -> dict[str, str]:
    files = {"context-adapter.yaml": adapter, "project.md": project}
    files.update(extra)
    return files


def _reasons(files) -> list[str]:
    with pytest.raises(AdapterApplyError) as exc:
        apply_adapter(files)
    return exc.value.reasons


# --- the happy path projects into the existing normalized shape ---


def test_the_corpus_projects_through_the_transforms_on_real_data():
    doc = apply_adapter(_corpus())
    assert doc.domain == "revenue-by-region"  # $.urn | slug
    assert doc.title == "Revenue by Region"
    assert doc.owner_refs == ["team:finance-data", "team:revenue-eng"]  # prefix('team:') per item
    assert doc.normalized["schema_version"] == 1
    assert doc.normalized["documents"]["context_doc"]["text"].startswith("# Revenue by Region")


def test_has_adapter_detects_the_file():
    assert has_adapter(_corpus()) is True
    assert has_adapter({"project.md": _PROJECT}) is False


# --- unmapped-is-error on LIVE data: no silent drop ---


def test_an_unmapped_source_key_is_an_error():
    project = _PROJECT.replace("title: Revenue by Region", "title: Revenue by Region\nsecret: x")
    reasons = _reasons(_corpus(project=project))
    assert any("source key 'secret' is unmapped" in r for r in reasons)


def test_every_unmapped_key_is_reported_not_only_the_first():
    project = _PROJECT.replace("title: Revenue by Region", "title: Revenue by Region\na: 1\nb: 2")
    reasons = _reasons(_corpus(project=project))
    assert sum("is unmapped" in r for r in reasons) == 2


# --- no fabrication: an absent path is an error, a failed transform fails closed ---


def test_a_mapped_path_absent_from_the_source_is_an_error_not_a_default():
    project = _PROJECT.replace("urn: Revenue By Region\n", "")  # $.urn now missing
    reasons = _reasons(_corpus(project=project))
    assert any("no value at '$.urn'" in r and "missing 'urn'" in r for r in reasons)


def test_a_wildcard_on_a_non_list_is_an_error():
    project = _PROJECT.replace("owners:\n  - finance-data\n  - revenue-eng", "owners: not-a-list")
    reasons = _reasons(_corpus(project=project))
    assert any("[*] on 'owners'" in r and "not a list" in r for r in reasons)


def test_a_transform_that_cannot_produce_a_value_fails_closed():
    # slug on an empty urn cannot produce a slug -> the transform raises, becomes a
    # reason, and no guessed value is emitted.
    project = _PROJECT.replace("urn: Revenue By Region", "urn: '!!!'")
    reasons = _reasons(_corpus(project=project))
    assert any("map.domain" in r for r in reasons)


# --- definitions apply: an adapter domain declares its concepts (283-4b) ---

_DEFINITIONS = (
    "  definitions:\n    from: concepts/*.md\n    term: '$.term'\n    statement: '$.statement'\n"
)
_CONCEPT_A = "---\nterm: recognized_revenue\nstatement: Revenue recognized under ASC 606.\n---\n"
_CONCEPT_B = "---\nterm: region\nstatement: The customer's billing region.\n---\n"


def test_definitions_are_projected_from_the_matched_files():
    doc = apply_adapter(
        _corpus(
            adapter=_ADAPTER + _DEFINITIONS,
            **{"concepts/revenue.md": _CONCEPT_A, "concepts/region.md": _CONCEPT_B},
        )
    )
    # Sorted by matched filename: concepts/region.md then concepts/revenue.md.
    assert doc.normalized["definitions"] == [
        {"term": "region", "statement": "The customer's billing region."},
        {"term": "recognized_revenue", "statement": "Revenue recognized under ASC 606."},
    ]


def test_an_unmapped_key_in_a_definition_file_is_an_error():
    concept = _CONCEPT_A.replace(
        "statement: Revenue recognized under ASC 606.",
        "statement: Revenue recognized under ASC 606.\nsecret: x",
    )
    reasons = _reasons(_corpus(adapter=_ADAPTER + _DEFINITIONS, **{"concepts/revenue.md": concept}))
    assert any("concepts/revenue.md" in r and "'secret' is unmapped" in r for r in reasons)


def test_a_definitions_glob_matching_nothing_is_an_error_not_an_empty_block():
    reasons = _reasons(_corpus(adapter=_ADAPTER + _DEFINITIONS))  # no concepts/*.md in corpus
    assert any("matched no corpus file" in r for r in reasons)


def test_the_adapter_file_is_never_read_as_a_definition_source():
    # A glob wide enough to match context-adapter.yaml must not consume it as a
    # concept file (it is Hyperset's projection control, not customer data). Were
    # it included, its own keys (schema_version, adapter, discover, map) would all
    # be unmapped and apply would fail; excluding it lets apply succeed with only
    # the real glossary file's definition.
    adapter = _ADAPTER + (
        "  definitions:\n    from: '*.yaml'\n    term: '$.term'\n    statement: '$.statement'\n"
    )
    doc = apply_adapter(_corpus(adapter=adapter, **{"glossary.yaml": _CONCEPT_A}))
    assert doc.normalized["definitions"] == [
        {"term": "recognized_revenue", "statement": "Revenue recognized under ASC 606."}
    ]


# --- an invalid adapter file is an apply failure carrying the schema's reasons ---


def test_an_invalid_adapter_file_fails_apply_with_its_reasons():
    reasons = _reasons(_corpus(adapter="schema_version: 2\nadapter: a\ndiscover: {}\nmap: {}"))
    assert any("schema_version must be 1" in r for r in reasons)


# --- a missing corpus file named by discover is an error ---


def test_a_missing_manifest_file_is_an_error():
    files = {"context-adapter.yaml": _ADAPTER}  # no project.md
    reasons = _reasons(files)
    assert any("discover.manifest names 'project.md'" in r for r in reasons)


# --- the projected output is held to the SAME v0 rules (reuses parse_context) ---


def test_the_projection_is_validated_by_the_v0_parser():
    # A domain that slugs to empty (all punctuation) is refused by v0's domain rule
    # reached through parse_context -- the adapter cannot emit a shape v0 rejects.
    project = _PROJECT.replace("urn: Revenue By Region", "urn: ' '")
    reasons = _reasons(_corpus(project=project))
    assert reasons  # refused, not accepted


# --- a separate context_doc file is read in full ---


def test_a_separate_context_doc_file_is_used_as_prose():
    adapter = _ADAPTER.replace("context_doc: project.md", "context_doc: guide.md")
    manifest_only = "---\nurn: Rev\ntitle: Rev\nowners:\n  - x\n---\n"
    files = _corpus(adapter=adapter, project=manifest_only)
    files["guide.md"] = "# Guide\n\nprose"
    doc = apply_adapter(files)
    assert doc.normalized["documents"]["context_doc"]["text"] == "# Guide\n\nprose"


# --- unmapped-is-error is checked at the LEAF, not only the top level ---

_NESTED_ADAPTER = _ADAPTER.replace('title: "$.title"', 'title: "$.meta.title"')


def test_a_nested_sibling_the_customer_wrote_is_not_silently_dropped():
    # A mapping reads $.meta.title; a SIBLING leaf under meta must not vanish
    # because its top-level parent was touched (adversary MAJOR).
    project = """---
urn: Revenue By Region
meta:
  title: Revenue by Region
  secret_pii: alice@example.com
owners:
  - finance-data
---
# body
"""
    reasons = _reasons(_corpus(adapter=_NESTED_ADAPTER, project=project))
    assert any("source key 'meta.secret_pii' is unmapped" in r for r in reasons)


def test_a_fully_mapped_nested_corpus_projects():
    project = """---
urn: Revenue By Region
meta:
  title: Revenue by Region
owners:
  - finance-data
---
# body
"""
    doc = apply_adapter(_corpus(adapter=_NESTED_ADAPTER, project=project))
    assert doc.domain == "revenue-by-region"
    assert doc.title == "Revenue by Region"


# --- resolution.projection disclosure substrate (283-5, hy-13b8) ---


def test_adapter_projection_discloses_the_adapter_and_empty_substrate_lists():
    from hyperset.context.adapter.apply import adapter_projection

    projection = adapter_projection(_corpus())
    assert projection == {
        "adapter": "acme-pipeline-docs-v2",
        "adapter_version": 1,
        "fields_unmapped": [],
        "fields_lossy": [],
        "fields_derived": [],
    }


def test_adapter_projection_is_none_for_a_non_adapter_corpus():
    # A hand-written v0 domain discloses no projection -- the served resolution is
    # byte-identical to before the field existed (additive by construction).
    from hyperset.context.adapter.apply import adapter_projection

    assert adapter_projection({"manifest.yaml": "schema_version: 1\n"}) is None
