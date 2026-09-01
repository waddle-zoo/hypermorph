"""The playground demo estates parse and read as sizable (hy-zx0q).

The playground manifests are demo content, not the test fixtures, so nothing
else validated them: a typo in a governed source ref or a field citing an
unapproved source would have surfaced only when someone ran the live demo.
parse_context is the same contract the sync uses, so this fails on exactly what
the demo would fail on -- and the size floors keep the estates industry-scale,
which is what hy-zx0q made them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyperset.context.schema import parse_context

EXAMPLES = Path(__file__).resolve().parents[2] / "playground" / "examples"
ESTATES = sorted(p.name for p in EXAMPLES.iterdir() if (p / "manifest.yaml").exists())


def _parse(estate: str):
    base = EXAMPLES / estate
    files = {p.name: p.read_text() for p in base.iterdir() if p.is_file()}
    return parse_context(files)


def test_the_demo_estates_are_present():
    # subscription_revenue is a governed SUBDOMAIN of revenue (parent: revenue),
    # added for the linked multi-domain scenario (#230 slice 8, hy-2pqi).
    assert ESTATES == ["revenue", "subscription_revenue", "supply_chain"]


@pytest.mark.parametrize("estate", ESTATES)
def test_the_estate_parses_as_valid_v0_context(estate):
    # parse_context raises ContextValidationError on any invalid ref, an
    # unapproved field source, or a duplicate/conflicting ref.
    doc = _parse(estate)
    assert doc.domain == estate


@pytest.mark.parametrize("estate", ESTATES)
def test_the_estate_reads_as_a_sizable_governed_context(estate):
    n = _parse(estate).normalized
    # Industry-scale floors: several governed sources, fields, and joins, so
    # the catalog and the resolved instructions render as a large estate.
    assert len(n["approved_sources"]) >= 5, "estate should declare several governed sources"
    assert len(n["fields"]) >= 5
    assert len(n["joins"]) >= 3
    assert len(n["definitions"]) >= 3


@pytest.mark.parametrize("estate", ESTATES)
def test_every_field_cites_an_approved_source(estate):
    # A structural property parse_context already enforces, asserted here so the
    # demo manifests carry it as a checked guarantee rather than a coincidence.
    n = _parse(estate).normalized
    approved = {source["ref"] for source in n["approved_sources"]}
    for field in n["fields"]:
        assert field["source_ref"] in approved
