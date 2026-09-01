"""The per-source lineage contract surfaced in `domain_graph` and mirrored in
`projection_summary` (hy-sr7w, 284-7).

A source declaring `facets.lineage` (produced_by and/or upstream) gains ONE lineage
node keyed by source ref (no trailing value -- avoids the hy-c89s aliasing class),
carrying the contract fields, and a `has_lineage` edge; a source without one changes
nothing. SURFACE-ONLY: `upstream` rides as a list FIELD, not as edges, so no graph
relationship is asserted beyond what Git stated, and no cycle/reachability is
computed. The catalog derives its shape from `projection_summary`, and a drift is
the failure this mirror prevents, so it is asserted on a lineage-bearing snapshot."""

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


def test_a_source_lineage_becomes_a_node_carrying_the_contract_and_an_edge():
    instructions = _instructions(
        [
            {
                "ref": "finance_orders",
                "role": "primary",
                "facets": {
                    "lineage": {
                        "produced_by": "dbt:finance.orders",
                        "upstream": ["table:raw.orders"],
                    }
                },
            }
        ]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})

    nodes = [node for node in graph["nodes"] if node["kind"] == "lineage"]
    assert nodes == [
        {
            "id": "lineage:finance_orders",
            "kind": "lineage",
            "label": "produced_by=dbt:finance.orders; upstream=['table:raw.orders']",
            "source_ref": "finance_orders",
            "produced_by": "dbt:finance.orders",
            "upstream": ["table:raw.orders"],
        }
    ]
    assert {
        "from": "source:finance_orders",
        "to": "lineage:finance_orders",
        "relation": "has_lineage",
        "evidence": "git",
    } in graph["edges"]


def test_upstream_is_a_field_not_an_edge_no_reachability_asserted():
    # SURFACE-ONLY: the upstream refs are carried on the node, NOT emitted as edges,
    # so nothing walks or asserts a graph path Git did not state directly.
    instructions = _instructions(
        [
            {
                "ref": "s",
                "role": "primary",
                "facets": {"lineage": {"upstream": ["table:raw.a", "table:raw.b"]}},
            }
        ]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    # The only lineage edge is source->lineage; no edge points at an upstream ref.
    lineage_edges = [e for e in graph["edges"] if e["relation"] == "has_lineage"]
    assert lineage_edges == [
        {"from": "source:s", "to": "lineage:s", "relation": "has_lineage", "evidence": "git"}
    ]
    assert not [e for e in graph["edges"] if "raw.a" in e["to"] or "raw.b" in e["to"]]


def test_two_sources_with_lineage_do_not_collapse_to_one_node():
    contract = {"lineage": {"produced_by": "job"}}
    instructions = _instructions(
        [
            {"ref": "a", "role": "primary", "facets": contract},
            {"ref": "b", "role": "secondary", "facets": contract},
        ]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    ids = sorted(node["id"] for node in graph["nodes"] if node["kind"] == "lineage")
    assert ids == ["lineage:a", "lineage:b"]


def test_a_source_without_a_lineage_adds_no_node_or_edge():
    instructions = _instructions([{"ref": "orders", "role": "primary"}])
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    assert not [n for n in graph["nodes"] if n["kind"] == "lineage"]
    assert not [e for e in graph["edges"] if e["relation"] == "has_lineage"]
    summary = projection_summary(_snapshot(), instructions)
    assert "lineage" not in summary["node_kinds"]
    assert "has_lineage" not in summary["relationships"]


def test_projection_summary_does_not_drift_from_the_graph_with_a_lineage():
    instructions = _instructions(
        [{"ref": "s", "role": "primary", "facets": {"lineage": {"produced_by": "job"}}}]
    )
    snapshot = _snapshot()
    graph = domain_graph(snapshot, instructions, {"observed_assets": []})
    assert projection_summary(snapshot, instructions) == {
        "node_kinds": sorted({node["kind"] for node in graph["nodes"]}),
        "relationships": sorted({edge["relation"] for edge in graph["edges"]}),
    }
    assert "lineage" in projection_summary(snapshot, instructions)["node_kinds"]
    assert "has_lineage" in projection_summary(snapshot, instructions)["relationships"]


def test_all_four_facets_coexist_without_drift():
    instructions = _instructions(
        [
            {
                "ref": "fx_rates_daily",
                "role": "primary",
                "facets": {
                    "grain": "fx_rate_date",
                    "classification": "internal",
                    "freshness": {"cadence": "daily"},
                    "lineage": {"produced_by": "job", "upstream": ["table:raw.fx"]},
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
    assert {"grain", "classification", "freshness", "lineage"} <= set(summary["node_kinds"])
    assert {"has_grain", "classified_as", "has_freshness", "has_lineage"} <= set(
        summary["relationships"]
    )
