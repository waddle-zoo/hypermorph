"""The per-source classification surfaced in `domain_graph` and mirrored in
`projection_summary` (hy-4giv, 284-6a).

A source declaring `facets.classification` (a closed governed sensitivity label)
gains a classification node keyed by source and a `classified_as` edge; a source
without one changes nothing. SURFACE-ONLY: this exposes the label; it enforces no
access or PII rule (that is 284-9). The catalog derives its projection shape from
`projection_summary`, and a drift between the two is the failure this mirror
prevents, so it is asserted here on a classification-bearing snapshot."""

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


def test_a_source_classification_becomes_a_node_and_a_classified_as_edge():
    instructions = _instructions(
        [{"ref": "pii_customers", "role": "primary", "facets": {"classification": "pii"}}]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})

    nodes = [node for node in graph["nodes"] if node["kind"] == "classification"]
    assert nodes == [
        {
            "id": "classification:pii_customers:pii",
            "kind": "classification",
            "label": "pii",
            "source_ref": "pii_customers",
        }
    ]
    assert {
        "from": "source:pii_customers",
        "to": "classification:pii_customers:pii",
        "relation": "classified_as",
        "evidence": "git",
    } in graph["edges"]


def test_two_sources_of_the_same_class_do_not_collapse_to_one_node():
    instructions = _instructions(
        [
            {"ref": "a", "role": "primary", "facets": {"classification": "restricted"}},
            {"ref": "b", "role": "secondary", "facets": {"classification": "restricted"}},
        ]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    ids = sorted(node["id"] for node in graph["nodes"] if node["kind"] == "classification")
    assert ids == ["classification:a:restricted", "classification:b:restricted"]


def test_a_source_without_a_classification_adds_no_node_or_edge():
    instructions = _instructions([{"ref": "orders", "role": "primary"}])
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    assert not [n for n in graph["nodes"] if n["kind"] == "classification"]
    assert not [e for e in graph["edges"] if e["relation"] == "classified_as"]
    summary = projection_summary(_snapshot(), instructions)
    assert "classification" not in summary["node_kinds"]
    assert "classified_as" not in summary["relationships"]


def test_projection_summary_does_not_drift_from_the_graph_with_a_classification():
    instructions = _instructions(
        [{"ref": "pii_customers", "role": "primary", "facets": {"classification": "pii"}}]
    )
    snapshot = _snapshot()
    graph = domain_graph(snapshot, instructions, {"observed_assets": []})
    assert projection_summary(snapshot, instructions) == {
        "node_kinds": sorted({node["kind"] for node in graph["nodes"]}),
        "relationships": sorted({edge["relation"] for edge in graph["edges"]}),
    }
    assert "classification" in projection_summary(snapshot, instructions)["node_kinds"]
    assert "classified_as" in projection_summary(snapshot, instructions)["relationships"]


def test_grain_and_classification_coexist_without_drift():
    # A source carrying BOTH facets adds both node kinds and both edges, and the
    # summary still mirrors the graph exactly.
    instructions = _instructions(
        [
            {
                "ref": "fx_rates_daily",
                "role": "primary",
                "facets": {"grain": "fx_rate_date", "classification": "internal"},
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
    assert {"grain", "classification"} <= set(summary["node_kinds"])
    assert {"has_grain", "classified_as"} <= set(summary["relationships"])
