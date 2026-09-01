"""Composer-pinned attachments: dedup, validation, and directive seeding."""

from playground.ui.app import (
    _attachment_facts,
    _merge_attachments,
    _normalise_attachments,
)


def test_normalise_drops_invalid_and_dedupes():
    raw = [
        {"kind": "domain", "domain": "revenue"},
        {"kind": "domain", "domain": "revenue"},  # duplicate
        {"kind": "concept", "domain": "revenue", "term": "recognized_revenue"},
        {"kind": "concept", "domain": "revenue"},  # missing term -> dropped
        {"kind": "asset", "domain": "revenue"},  # unsupported kind -> dropped
        "not-a-dict",
    ]
    out = _normalise_attachments(raw)
    assert out == [
        {"kind": "domain", "domain": "revenue", "term": ""},
        {"kind": "concept", "domain": "revenue", "term": "recognized_revenue"},
    ]


def test_merge_seeds_domain_from_pinned_concept_without_duplicating():
    directive = {"domains": ["revenue"], "concepts": []}
    attachments = [
        {"kind": "concept", "domain": "revenue", "term": "recognized_revenue"},
        {"kind": "domain", "domain": "billing", "term": ""},
    ]
    merged = _merge_attachments(directive, attachments)
    assert merged["domains"] == ["revenue", "billing"]
    assert merged["concepts"] == ["recognized_revenue"]
    assert merged["asset_refs"] == []


def test_facts_render_kind_specific_lines():
    facts = _attachment_facts(
        [
            {"kind": "domain", "domain": "revenue", "term": ""},
            {"kind": "concept", "domain": "revenue", "term": "recognized_revenue"},
        ]
    )
    assert facts == [
        "- domain 'revenue'",
        "- concept 'recognized_revenue' in domain 'revenue'",
    ]
