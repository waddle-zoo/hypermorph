"""The per-source freshness contract surfaced in `domain_graph` and mirrored in
`projection_summary` (hy-6c8z, 284-6b).

A source declaring `facets.freshness` (cadence and/or max_staleness) gains ONE
freshness node keyed by source, carrying the contract fields, and a `has_freshness`
edge; a source without one changes nothing. SURFACE-ONLY: this exposes the governed
contract; it computes and enforces no staleness (a later check bead). The catalog
derives its projection shape from `projection_summary`, and a drift is the failure
this mirror prevents, so it is asserted on a freshness-bearing snapshot."""

from __future__ import annotations

from types import SimpleNamespace

from hyperset.bundle.resolver import domain_graph, projection_summary


def _instructions(sources: list[dict]) -> dict:
    return {
        "definitions": [],
        "approved_sources": sources,
        "fields": [],
        "filters": [],
        "joins": [],
        "grain": None,
        "caveats": [],
        "validations": [],
        "prohibited_sources": [],
        "context_doc": None,
    }


def _snapshot() -> SimpleNamespace:
    return SimpleNamespace(domain="revenue", owner_refs=[])


def test_a_source_freshness_becomes_a_node_carrying_the_contract_and_an_edge():
    instructions = _instructions(
        [
            {
                "ref": "fx_rates_daily",
                "role": "primary",
                "facets": {"freshness": {"cadence": "daily", "max_staleness": "24h"}},
            }
        ]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})

    nodes = [node for node in graph["nodes"] if node["kind"] == "freshness"]
    assert nodes == [
        {
            "id": "freshness:fx_rates_daily",
            "kind": "freshness",
            "label": "cadence=daily; max_staleness=24h",
            "source_ref": "fx_rates_daily",
            "cadence": "daily",
            "max_staleness": "24h",
        }
    ]
    assert {
        "from": "source:fx_rates_daily",
        "to": "freshness:fx_rates_daily",
        "relation": "has_freshness",
        "evidence": "git",
    } in graph["edges"]


def test_two_sources_with_the_same_contract_do_not_collapse_to_one_node():
    contract = {"freshness": {"cadence": "daily"}}
    instructions = _instructions(
        [
            {"ref": "a", "role": "primary", "facets": contract},
            {"ref": "b", "role": "secondary", "facets": contract},
        ]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    ids = sorted(node["id"] for node in graph["nodes"] if node["kind"] == "freshness")
    assert ids == ["freshness:a", "freshness:b"]


def test_a_source_without_a_freshness_adds_no_node_or_edge():
    instructions = _instructions([{"ref": "orders", "role": "primary"}])
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    assert not [n for n in graph["nodes"] if n["kind"] == "freshness"]
    assert not [e for e in graph["edges"] if e["relation"] == "has_freshness"]
    summary = projection_summary(_snapshot(), instructions)
    assert "freshness" not in summary["node_kinds"]
    assert "has_freshness" not in summary["relationships"]


def test_projection_summary_does_not_drift_from_the_graph_with_a_freshness():
    instructions = _instructions(
        [{"ref": "fx", "role": "primary", "facets": {"freshness": {"max_staleness": "1h"}}}]
    )
    snapshot = _snapshot()
    graph = domain_graph(snapshot, instructions, {"observed_assets": []})
    assert projection_summary(snapshot, instructions) == {
        "node_kinds": sorted({node["kind"] for node in graph["nodes"]}),
        "relationships": sorted({edge["relation"] for edge in graph["edges"]}),
    }
    assert "freshness" in projection_summary(snapshot, instructions)["node_kinds"]
    assert "has_freshness" in projection_summary(snapshot, instructions)["relationships"]


def test_all_three_facets_coexist_without_drift():
    instructions = _instructions(
        [
            {
                "ref": "fx_rates_daily",
                "role": "primary",
                "facets": {
                    "grain": "fx_rate_date",
                    "classification": "internal",
                    "freshness": {"cadence": "daily", "max_staleness": "24h"},
                },
            }
        ]
    )
    snapshot = _snapshot()
    graph = domain_graph(snapshot, instructions, {"observed_assets": []})
    summary = projection_summary(snapshot, instructions)
    assert summary == {
        "node_kinds": sorted({node["kind"] for node in graph["nodes"]}),
        "relationships": sorted({edge["relation"] for edge in graph["edges"]}),
    }
    assert {"grain", "classification", "freshness"} <= set(summary["node_kinds"])
    assert {"has_grain", "classified_as", "has_freshness"} <= set(summary["relationships"])
