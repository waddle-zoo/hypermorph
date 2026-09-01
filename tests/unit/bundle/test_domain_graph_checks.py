"""The per-source checks contract surfaced in `domain_graph` and mirrored in
`projection_summary` (hy-w16y, 284-8).

A source declaring `facets.checks` (a list of owned data-quality checks, each a
name plus optional description/severity) gains ONE checks node keyed by source ref
(no trailing value -- avoids the hy-c89s aliasing class), carrying the checks as a
list FIELD, plus a `has_checks` edge; a source without one changes nothing.
SURFACE-ONLY: the checks ride as data, NOT as executed checks -- no pass/fail node,
no derived status, nothing run. The catalog derives its shape from
`projection_summary`, and a drift is the failure this mirror prevents, so it is
asserted on a checks-bearing snapshot. The `checks`/`has_checks` projection is
DISTINCT from the domain's own `check`/`validates` projection."""

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


def test_a_source_checks_becomes_a_node_carrying_the_list_and_an_edge():
    checks = [
        {"name": "not_null", "description": "id present", "severity": "error"},
        {"name": "unique"},
    ]
    instructions = _instructions(
        [{"ref": "finance_orders", "role": "primary", "facets": {"checks": checks}}]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})

    nodes = [node for node in graph["nodes"] if node["kind"] == "checks"]
    assert nodes == [
        {
            "id": "checks:finance_orders",
            "kind": "checks",
            "label": "not_null; unique",
            "source_ref": "finance_orders",
            "checks": checks,
        }
    ]
    assert {
        "from": "source:finance_orders",
        "to": "checks:finance_orders",
        "relation": "has_checks",
        "evidence": "git",
    } in graph["edges"]


def test_checks_ride_as_a_field_nothing_is_run_no_passfail_node():
    # SURFACE-ONLY: the checks are carried as data on the node; no check is executed,
    # so no pass/fail or status node is emitted and the only checks edge is the
    # source->checks declaration.
    instructions = _instructions(
        [{"ref": "s", "role": "primary", "facets": {"checks": [{"name": "fresh"}]}}]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    checks_edges = [e for e in graph["edges"] if e["relation"] == "has_checks"]
    assert checks_edges == [
        {"from": "source:s", "to": "checks:s", "relation": "has_checks", "evidence": "git"}
    ]
    # No pass/fail/result relation was invented from the checks.
    assert not [e for e in graph["edges"] if e["relation"] in {"passed", "failed", "check_result"}]


def test_two_sources_with_checks_do_not_collapse_to_one_node():
    checks = [{"name": "not_null"}]
    instructions = _instructions(
        [
            {"ref": "a", "role": "primary", "facets": {"checks": checks}},
            {"ref": "b", "role": "secondary", "facets": {"checks": checks}},
        ]
    )
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    ids = sorted(node["id"] for node in graph["nodes"] if node["kind"] == "checks")
    assert ids == ["checks:a", "checks:b"]


def test_a_source_without_checks_adds_no_node_or_edge():
    instructions = _instructions([{"ref": "orders", "role": "primary"}])
    graph = domain_graph(_snapshot(), instructions, {"observed_assets": []})
    assert not [n for n in graph["nodes"] if n["kind"] == "checks"]
    assert not [e for e in graph["edges"] if e["relation"] == "has_checks"]
    summary = projection_summary(_snapshot(), instructions)
    assert "checks" not in summary["node_kinds"]
    assert "has_checks" not in summary["relationships"]


def test_projection_summary_does_not_drift_from_the_graph_with_checks():
    instructions = _instructions(
        [{"ref": "s", "role": "primary", "facets": {"checks": [{"name": "not_null"}]}}]
    )
    snapshot = _snapshot()
    graph = domain_graph(snapshot, instructions, {"observed_assets": []})
    assert projection_summary(snapshot, instructions) == {
        "node_kinds": sorted({node["kind"] for node in graph["nodes"]}),
        "relationships": sorted({edge["relation"] for edge in graph["edges"]}),
    }
    assert "checks" in projection_summary(snapshot, instructions)["node_kinds"]
    assert "has_checks" in projection_summary(snapshot, instructions)["relationships"]


def test_all_five_facets_coexist_without_drift():
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
                    "checks": [{"name": "not_null"}],
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
    assert {"grain", "classification", "freshness", "lineage", "checks"} <= set(
        summary["node_kinds"]
    )
    assert {
        "has_grain",
        "classified_as",
        "has_freshness",
        "has_lineage",
        "has_checks",
    } <= set(summary["relationships"])
