"""The per-source grain surfaced in `domain_graph` and mirrored in
`projection_summary` (hy-gp99, 284-3).

284-2 parsed and stored `approved_sources[].facets.grain`; this slice surfaces
it. A source declaring a grain gains a grain node keyed by source and a
`has_grain` edge; a source without one changes nothing. The catalog derives its
projection shape from `projection_summary`, and a drift between the two is the
failure this mirror exists to prevent, so it is asserted here on a grain-bearing
snapshot -- not only on the fixture (which declares none)."""

from __future__ import annotations

from types import SimpleNamespace

from hyperset.bundle.resolver import domain_graph, projection_summary


def _instructions(sources: list[dict], *, grain: str | None = None) -> dict:
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


def _snapshot() -> SimpleNamespace:
    return SimpleNamespace(domain="revenue", owner_refs=[])


def test_a_source_grain_becomes_a_node_and_a_has_grain_edge():
    instructions = _instructions(
        [{"ref": "fx_rates_daily", "role": "primary", "facets": {"grain": "fx_rate_date"}}]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})

    grain_nodes = [node for node in graph["nodes"] if node["kind"] == "grain"]
    assert grain_nodes == [
        {
            "id": "grain:fx_rates_daily:fx_rate_date",
            "kind": "grain",
            "label": "fx_rate_date",
            "source_ref": "fx_rates_daily",
        }
    ]
    assert {
        "from": "source:fx_rates_daily",
        "to": "grain:fx_rates_daily:fx_rate_date",
        "relation": "has_grain",
        "evidence": "git",
    } in graph["edges"]


def test_two_sources_at_the_same_grain_do_not_collapse_to_one_node():
    instructions = _instructions(
        [
            {"ref": "a", "role": "primary", "facets": {"grain": "day"}},
            {"ref": "b", "role": "secondary", "facets": {"grain": "day"}},
        ]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    grain_ids = sorted(node["id"] for node in graph["nodes"] if node["kind"] == "grain")
    assert grain_ids == ["grain:a:day", "grain:b:day"]


def test_a_source_without_a_grain_adds_no_grain_node_or_edge():
    instructions = _instructions([{"ref": "orders", "role": "primary"}])
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    assert not [node for node in graph["nodes"] if node["kind"] == "grain"]
    assert not [edge for edge in graph["edges"] if edge["relation"] == "has_grain"]
    # And the summary is silent about a grain nobody declared.
    summary = projection_summary(_snapshot(), instructions)
    assert "grain" not in summary["node_kinds"]
    assert "has_grain" not in summary["relationships"]


def test_projection_summary_does_not_drift_from_the_graph_with_a_source_grain():
    instructions = _instructions(
        [{"ref": "fx_rates_daily", "role": "primary", "facets": {"grain": "fx_rate_date"}}]
    )
    snapshot = _snapshot()
    graph = domain_graph(snapshot, instructions, {"observed_assets": []})
    assert projection_summary(snapshot, instructions) == {
        "node_kinds": sorted({node["kind"] for node in graph["nodes"]}),
        "relationships": sorted({edge["relation"] for edge in graph["edges"]}),
    }
    assert "grain" in projection_summary(snapshot, instructions)["node_kinds"]
    assert "has_grain" in projection_summary(snapshot, instructions)["relationships"]


def test_no_drift_when_a_source_grain_coexists_with_a_domain_grain():
    """The set-dedup case: a domain grain and a source grain both produce a
    `grain` kind. Because `projection_summary` and `domain_graph` dedup
    identically, the domain grain's `constrains` and the source grain's
    `has_grain` both appear on each side and neither masks the other."""
    instructions = _instructions(
        [{"ref": "fx_rates_daily", "role": "primary", "facets": {"grain": "fx_rate_date"}}],
        grain="order_date",
    )
    snapshot = _snapshot()
    graph = domain_graph(snapshot, instructions, {"observed_assets": []})
    summary = projection_summary(snapshot, instructions)
    assert summary == {
        "node_kinds": sorted({node["kind"] for node in graph["nodes"]}),
        "relationships": sorted({edge["relation"] for edge in graph["edges"]}),
    }
    assert {"constrains", "has_grain"} <= set(summary["relationships"])
    # Both grain nodes exist and are distinct: the domain grain and the source grain.
    grain_ids = sorted(node["id"] for node in graph["nodes"] if node["kind"] == "grain")
    assert grain_ids == ["grain:fx_rates_daily:fx_rate_date", "grain:order_date"]
