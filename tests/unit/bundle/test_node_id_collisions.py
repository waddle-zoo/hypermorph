"""domain_graph node ids cannot alias two nodes (hy-c89s).

A node id is SERVED. The per-source grain keys `grain:{ref}:{value}` and the domain
grain keys `grain:{value}`, both under the `grain:` prefix; a ':' inside the grain
VALUE could make one id the concatenation of a different (ref, grain) pair, or make
a domain-grain id equal a source-grain id. The parse layer forbids ':' in a grain
value (schema.py `_grain`), so the only variable segment left free of the delimiter
is the appended value -- the source REF is allowed ':' (governed refs are `table:...`
by construction and are the id's natural multi-part key). These tests prove, at the
projection, that distinct sources and the domain/source split never collide for any
delimiter-free grain, that the other four facets are collision-proof by their id
shape, and that the ids for the current (delimiter-free) fixture are byte-identical."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hyperset.bundle.resolver import domain_graph


def _instructions(sources, grain=None):
    return {
        "definitions": [],
        "approved_sources": sources,
        "fields": [],
        "filters": [],
        "joins": [],
        "grain": grain,
        "caveats": [],
        "validations": [],
        "prohibited_sources": [],
        "context_doc": None,
    }


def _snapshot():
    return SimpleNamespace(domain="revenue", owner_refs=[])


def _ids(graph):
    return [node["id"] for node in graph["nodes"]]


@pytest.mark.unit
def test_two_sources_with_colon_bearing_refs_get_distinct_grain_ids():
    # Governed refs legitimately contain ':'. With colon-free grain values, the
    # appended value is the unambiguous trailing segment, so distinct sources never
    # collapse -- even when one ref is a prefix of another.
    instructions = _instructions(
        [
            {"ref": "table:pg:orders", "role": "primary", "facets": {"grain": "order_date"}},
            {"ref": "table:pg:orders:eu", "role": "secondary", "facets": {"grain": "order_date"}},
        ]
    )
    grain_ids = [
        n["id"]
        for n in domain_graph(_snapshot(), instructions, {"observed_assets": []})["nodes"]
        if n["kind"] == "grain"
    ]
    assert grain_ids == ["grain:table:pg:orders:order_date", "grain:table:pg:orders:eu:order_date"]
    assert len(set(grain_ids)) == 2


@pytest.mark.unit
def test_domain_grain_and_a_source_grain_never_share_an_id():
    # The domain grain id (`grain:{value}`, value delimiter-free) carries zero
    # colons after the prefix; a source grain id (`grain:{ref}:{value}`) carries at
    # least the boundary ':'. Disjoint by colon count, so the shared `grain:` prefix
    # cannot alias the two even if the domain grain equals a source's grain text.
    instructions = _instructions(
        [{"ref": "table:pg:orders", "role": "primary", "facets": {"grain": "order_date"}}],
        grain="order_date",
    )
    grain_ids = {
        n["id"]
        for n in domain_graph(_snapshot(), instructions, {"observed_assets": []})["nodes"]
        if n["kind"] == "grain"
    }
    assert grain_ids == {"grain:order_date", "grain:table:pg:orders:order_date"}
    assert len(grain_ids) == 2


@pytest.mark.unit
def test_every_facet_id_is_distinct_across_two_fully_loaded_sources():
    facets = {
        "grain": "order_date",
        "classification": "internal",
        "freshness": {"cadence": "daily"},
        "lineage": {"produced_by": "job"},
        "checks": [{"name": "not_null"}],
    }
    instructions = _instructions(
        [
            {"ref": "table:pg:a", "role": "primary", "facets": facets},
            {"ref": "table:pg:b", "role": "secondary", "facets": facets},
        ]
    )
    ids = _ids(domain_graph(_snapshot(), instructions, {"observed_assets": []}))
    # No id repeats: two sources sharing identical facet values still yield distinct
    # nodes because every id carries the disambiguating ref.
    assert len(ids) == len(set(ids))
    for ref in ("table:pg:a", "table:pg:b"):
        # grain/classification carry the value; freshness/lineage/checks are ref-only.
        assert f"grain:{ref}:order_date" in ids
        assert f"classification:{ref}:internal" in ids
        assert f"freshness:{ref}" in ids
        assert f"lineage:{ref}" in ids
        assert f"checks:{ref}" in ids


@pytest.mark.unit
def test_the_delimiter_free_fixture_ids_are_byte_identical():
    # A frozen snapshot of the grain ids the current (delimiter-free) revenue-style
    # data serves. The hy-c89s guard must change NONE of these; this is the
    # byte-identity anchor for the whole change.
    instructions = _instructions(
        [{"ref": "table:postgres:analytics.public.finance_orders_daily", "role": "primary"}],
        grain="order_date by customer_dim.region",
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    assert {n["id"] for n in graph["nodes"] if n["kind"] == "grain"} == {
        "grain:order_date by customer_dim.region"
    }
    # The source node id embeds the colon-bearing ref verbatim, unchanged.
    assert "source:table:postgres:analytics.public.finance_orders_daily" in _ids(graph)
