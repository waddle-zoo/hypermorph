"""Observed relationship edges in `domain_graph` (#230 slice 7, hy-c6vx).

A persisted `asset_relationships` row whose connector word is `derived_from` or
`has_glossary_term` projects into the served `domain_graph` as an OBSERVED edge
(`lineage_to` / `has_glossary_term`, `evidence: "observation"`), distinct from
every governed edge (`evidence: "git"`). Per ADR-0034 Decision 2/Section 8 neither
observed name reuses a governed string: `lineage_to` != governed `has_lineage`,
and the glossary link keeps the connector word rather than the retired governed
`evidenced_by` -- which is never emitted, so a legacy client cannot read the
observed edge as the governed one.
"""

from __future__ import annotations

from types import SimpleNamespace

from hyperset.bundle.resolver import (
    CONNECTOR_RELATION_TO_GRAPH,
    GOVERNED_RELATIONS,
    domain_graph,
)


def _instructions(sources: list[dict] | None = None) -> dict:
    return {
        "definitions": [],
        "approved_sources": sources or [],
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


def _lineage_fragment():
    return {
        "from": "source:superset:dataset:orders",
        "to": "source:datahub:dataset:urn:li:dataset:raw_orders",
        "to_label": "urn:li:dataset:raw_orders",
        "to_connector": "datahub",
        "relation": "lineage_to",
    }


def _evidence_fragment():
    return {
        "from": "source:datahub:dataset:orders",
        "to": "source:datahub:glossary_term:urn:li:glossaryTerm:recognized_revenue",
        "to_label": "urn:li:glossaryTerm:recognized_revenue",
        "to_connector": "datahub",
        "relation": "has_glossary_term",
    }


def test_the_mapping_names_exactly_the_two_slice_seven_relations():
    # Exactly lineage_to and has_glossary_term; queries/contains/owned_by/in_domain/
    # belongs_to are deliberately not projected.
    assert CONNECTOR_RELATION_TO_GRAPH == {
        "derived_from": "lineage_to",
        "has_glossary_term": "has_glossary_term",
    }


def test_no_observed_relation_reuses_any_governed_relation_string():
    # ADR-0034 Decision 2: an observed relation may not reuse ANY governed string.
    # Asserted against the complete authoritative set, not a hand-picked couple, so
    # a future observed value like contains/owns/depends_on/evidenced_by is caught.
    graph_relations = set(CONNECTOR_RELATION_TO_GRAPH.values())
    collisions = graph_relations & GOVERNED_RELATIONS
    assert not collisions, f"observed relations reuse governed strings: {sorted(collisions)}"


def _fully_governed_instructions() -> dict:
    """Instructions dense enough to emit every WITHIN-DOMAIN governed relation, so
    the authoritative set cannot silently miss one the resolver emits."""
    return {
        "definitions": [{"term": "recognized_revenue"}],
        "approved_sources": [
            {
                "ref": "finance_orders",
                "role": "primary",
                "facets": {
                    "grain": "order_date",
                    "classification": "internal",
                    "freshness": {"cadence": "daily"},
                    "lineage": {"produced_by": "dbt:finance.orders"},
                    "checks": [{"name": "not_null(order_id)"}],
                },
            }
        ],
        "fields": [{"name": "amount", "source_ref": "finance_orders"}],
        "filters": [],
        "joins": [{"from": "orders", "to": "customers", "type": "inner"}],
        "grain": "order_date",
        "caveats": [],
        "validations": ["amount >= 0"],
        "prohibited_sources": [],
        "context_doc": None,
    }


def test_every_within_domain_governed_relation_is_in_the_authoritative_set():
    # Keeps GOVERNED_RELATIONS honest: if a new governed edge is added to the emit,
    # this fails until the authoritative set names it -- so the disjointness guard
    # above can never pass vacuously by omission.
    snapshot = SimpleNamespace(domain="revenue", owner_refs=[{"ref": "team:fin", "source": "git"}])
    graph = domain_graph(snapshot, _fully_governed_instructions(), {"observed_assets": []})
    emitted = {edge["relation"] for edge in graph["edges"] if edge["evidence"] == "git"}
    # Every within-domain governed relation is emitted by this dense fixture...
    assert {
        "owns",
        "defined_in",
        "approved_for",
        "has_grain",
        "classified_as",
        "has_freshness",
        "has_lineage",
        "has_checks",
        "reads",
        "constrains",
        "validates",
    } <= emitted
    # ...and each is in the authoritative set the observed guard checks against.
    assert emitted <= GOVERNED_RELATIONS


def test_observed_relationship_edges_carry_the_observation_evidence_class():
    graph = domain_graph(
        _snapshot(),
        _instructions(),
        {"observed_assets": []},
        [_lineage_fragment(), _evidence_fragment()],
    )
    lineage = next(edge for edge in graph["edges"] if edge["relation"] == "lineage_to")
    glossary = next(edge for edge in graph["edges"] if edge["relation"] == "has_glossary_term")
    assert lineage == {
        "from": "source:superset:dataset:orders",
        "to": "source:datahub:dataset:urn:li:dataset:raw_orders",
        "relation": "lineage_to",
        "evidence": "observation",
    }
    assert glossary["evidence"] == "observation"


def test_no_observed_edge_is_ever_governed_git_evidence():
    # The retired governed evidenced_by must not reappear at all; the observed
    # glossary edge is glossary-term-linked, observation-classed, never git.
    graph = domain_graph(
        _snapshot(),
        _instructions([{"ref": "orders", "role": "primary"}]),
        {"observed_assets": []},
        [_evidence_fragment()],
    )
    governed = [edge for edge in graph["edges"] if edge["evidence"] == "git"]
    assert governed, "the governed approved_for edge should still be present"
    assert all(edge["relation"] != "has_glossary_term" for edge in governed)
    assert all(edge["relation"] != "lineage_to" for edge in governed)
    # The retired governed string is never emitted, on either evidence class.
    assert not [edge for edge in graph["edges"] if edge["relation"] == "evidenced_by"]
    # The observed glossary edge is present and observation-classed.
    observed = [edge for edge in graph["edges"] if edge["relation"] == "has_glossary_term"]
    assert observed and observed[0]["evidence"] == "observation"


def test_a_relationship_target_becomes_one_observed_source_node():
    graph = domain_graph(
        _snapshot(), _instructions(), {"observed_assets": []}, [_lineage_fragment()]
    )
    targets = [node for node in graph["nodes"] if node["id"].endswith("raw_orders")]
    assert targets == [
        {
            "id": "source:datahub:dataset:urn:li:dataset:raw_orders",
            "kind": "observed_source",
            "label": "urn:li:dataset:raw_orders",
            "connector": "datahub",
        }
    ]


def test_a_target_node_is_not_emitted_twice():
    # Two edges to the same target dedup to one node, but both edges are kept.
    second = {**_lineage_fragment(), "from": "source:superset:dataset:other"}
    graph = domain_graph(
        _snapshot(), _instructions(), {"observed_assets": []}, [_lineage_fragment(), second]
    )
    target_nodes = [n for n in graph["nodes"] if n["id"].endswith("raw_orders")]
    assert len(target_nodes) == 1
    lineage_edges = [e for e in graph["edges"] if e["relation"] == "lineage_to"]
    assert len(lineage_edges) == 2


def test_no_relationships_changes_nothing():
    # The default keeps every existing call site byte-identical.
    base = domain_graph(
        _snapshot(), _instructions([{"ref": "s", "role": "primary"}]), {"observed_assets": []}
    )
    with_none = domain_graph(
        _snapshot(), _instructions([{"ref": "s", "role": "primary"}]), {"observed_assets": []}, None
    )
    assert base == with_none
    assert not [e for e in base["edges"] if e["evidence"] == "observation"]
