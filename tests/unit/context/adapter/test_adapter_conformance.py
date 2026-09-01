"""The adapter conformance kit (283-9, hy-7oe6).

The design surface: the NEGATIVE arms must be NON-VACUOUS -- each must actually
fail on a real bad adapter, not pass hollow. So every negative test builds a bad
adapter fixture and asserts the specific failure fires, and a paired positive
proves the same arm passes a good adapter (a check that can only fail is as
useless as one that can only pass).
"""

from __future__ import annotations

from hyperset.context.adapter.conformance import (
    _disclosure_failures,
    run_conformance,
)
from hyperset.context.adapter.schema import parse_adapter

# A GOOD adapter: every field faithfully mapped. `domain` reads a pre-slugged
# corpus field (bare path, no transform), `owners` prefixes, definitions are bare
# -- no lossy transform on a content field, nothing authored, every corpus key
# consumed.
_GOOD_ADAPTER = (
    "schema_version: 1\nadapter: acme-good\n"
    "discover:\n  unit: 'docs/*'\n  manifest: project.md\n  context_doc: project.md\n"
    "map:\n  domain: '$.slug'\n  title: '$.title'\n"
    "  owners: \"$.owners[*] | prefix('team:')\"\n"
    "  definitions:\n    from: 'concepts/*.md'\n    term: '$.term'\n    statement: '$.statement'\n"
)
_PROJECT = (
    "---\nslug: revenue-by-region\ntitle: Revenue by Region\nowners:\n  - finance-data\n---\n"
    "# Revenue by Region\n\nHow revenue is governed here.\n"
)
_CONCEPT = "---\nterm: recognized_revenue\nstatement: Revenue recognized under ASC 606.\n---\n"

# The REAL canonical adapter (#309/#310): domain via `$.urn | slug`. Slug on the
# IDENTITY field is the domain's canonical form, not a lossy content drop, so the
# standard mapping must PASS -- a kit that flagged it would be unusable.
_CANONICAL_ADAPTER = (
    "schema_version: 1\nadapter: acme-pipeline-docs-v2\n"
    "discover:\n  unit: 'docs/*'\n  manifest: project.md\n  context_doc: project.md\n"
    "map:\n  domain: '$.urn | slug'\n  title: '$.title'\n"
    "  owners: \"$.owners[*] | prefix('team:')\"\n"
)
_CANONICAL_PROJECT = (
    "---\nurn: Revenue By Region\ntitle: Revenue by Region\nowners:\n  - finance-data\n---\n"
    "# Revenue by Region\n\nHow revenue is governed here.\n"
)


def _corpus(adapter, project=_PROJECT, **extra):
    files = {"context-adapter.yaml": adapter, "project.md": project}
    files.update(extra)
    return files


def _good_corpus(adapter=_GOOD_ADAPTER):
    return _corpus(adapter, **{"concepts/revenue.md": _CONCEPT})


# --- positive: a good adapter, and the real canonical one, PASS every arm ---


def test_a_faithful_adapter_passes_every_arm():
    result = run_conformance(_good_corpus())
    assert result.passed, result.failures
    assert result.failures == []
    # not vacuous: all five arms ran
    assert set(result.checks) == {
        "stable-projection",
        "fully-mapped",
        "derived-attributed",
        "v0-valid",
        "lossless-or-declared",
    }


def test_the_real_canonical_adapter_is_conformant():
    result = run_conformance(_corpus(_CANONICAL_ADAPTER, project=_CANONICAL_PROJECT))
    assert result.passed, result.failures


def test_projection_is_stable_across_runs():
    # Two runs inside run_conformance already compare; assert the arm is present
    # and green on a good adapter (the negative of instability cannot be built
    # without nondeterminism, so stability is asserted positively).
    result = run_conformance(_good_corpus())
    assert not any("unstable" in f for f in result.failures)


# --- negative arm: an UNMAPPED corpus key fails (fully-mapped) ---


def test_an_unmapped_corpus_key_fails_conformance():
    project = _PROJECT.replace("title: Revenue by Region", "title: Revenue by Region\nsecret: x")
    result = run_conformance(_good_corpus(_GOOD_ADAPTER) | {"project.md": project})
    assert not result.passed
    assert any("apply failed" in f and "unmapped" in f for f in result.failures)


# --- negative arm: a LOSSY-but-UNDECLARED content field fails (lossless-or-declared) ---


def test_a_lossy_undeclared_content_field_fails_conformance():
    # `one_line` on the title loses the field's whitespace/newlines; the served
    # projection declares nothing lossy, so the kit must flag it.
    lossy = _GOOD_ADAPTER.replace("title: '$.title'", "title: '$.title | one_line'")
    result = run_conformance(_good_corpus(lossy))
    assert not result.passed
    assert any("lossy-undeclared" in f and "'title'" in f for f in result.failures)


def test_a_slug_on_the_identity_field_is_not_flagged_lossy():
    # The canonical `$.urn | slug` domain (identity) must NOT trip the lossy arm.
    result = run_conformance(_corpus(_CANONICAL_ADAPTER, project=_CANONICAL_PROJECT))
    assert not any("lossy-undeclared" in f for f in result.failures)


# --- negative arm: a DERIVED (authored) field without attribution fails ---


def test_an_authored_field_without_attribution_fails_conformance():
    # `default(...)` authors a value the customer did not write; the served
    # projection carries no fields_derived attribution, so the kit must flag it.
    derived = _GOOD_ADAPTER.replace("title: '$.title'", "title: \"$.title | default('Untitled')\"")
    result = run_conformance(_good_corpus(derived))
    assert not result.passed
    assert any("unattributed-derived" in f and "'title'" in f for f in result.failures)


# --- the attribution check itself: both branches fire, and a good entry passes ---


def _spec_authoring_title():
    adapter = _GOOD_ADAPTER.replace("title: '$.title'", "title: \"$.title | default('Untitled')\"")
    return parse_adapter(adapter)


def test_attribution_fails_when_the_derived_field_is_absent_from_fields_derived():
    spec = _spec_authoring_title()
    failures = _disclosure_failures(spec, {"fields_derived": [], "fields_lossy": []})
    assert any(
        "unattributed-derived" in f and "not declared in fields_derived" in f for f in failures
    )


def test_attribution_fails_when_the_derived_entry_has_no_reviewed_by():
    spec = _spec_authoring_title()
    projection = {"fields_derived": [{"field": "title"}], "fields_lossy": []}
    failures = _disclosure_failures(spec, projection)
    assert any("without a reviewed_by" in f for f in failures)


def test_attribution_passes_when_the_derived_field_is_attributed():
    spec = _spec_authoring_title()
    projection = {
        "fields_derived": [{"field": "title", "reviewed_by": "alice@acme.test"}],
        "fields_lossy": [],
    }
    assert _disclosure_failures(spec, projection) == []


def test_a_declared_lossy_content_field_passes():
    spec = parse_adapter(_GOOD_ADAPTER.replace("title: '$.title'", "title: '$.title | one_line'"))
    assert _disclosure_failures(spec, {"fields_lossy": ["title"], "fields_derived": []}) == []


def test_a_corpus_without_an_adapter_fails_closed_rather_than_raising():
    # The kit's contract is "never raises for a non-conformant input"; a corpus
    # with no context-adapter.yaml is reported, not crashed.
    result = run_conformance({"project.md": _PROJECT})
    assert not result.passed
    assert any("no context-adapter.yaml" in f for f in result.failures)
