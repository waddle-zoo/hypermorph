"""The governed `contains` hierarchy edges emitted into the served `domain_graph`
(hy-gh-230 slice 1, ADR-0031). A domain that declares a `parent`, or is the declared
parent of another domain, carries `contains` edges one immediate level each way. The
whole-estate map is re-validated before any edge is served, so a dangling or
transitive-unknown ancestor is dropped fail-closed.

These pin `_contains_edges`, the pure whole-estate emit -- the resolver-path wiring
(estate map from the repository) is exercised in the postgres/compose suites."""

from __future__ import annotations

from hyperset.bundle.resolver import _contains_edges
from hyperset.context.hierarchy import contains_edge, domain_node_id


def _node(domain: str) -> dict:
    return {"id": domain_node_id(domain), "kind": "domain", "label": domain}


def test_a_declared_parent_becomes_a_domain_node_and_a_contains_edge():
    nodes, edges = _contains_edges("revenue", {"finance": None, "revenue": "finance"})
    assert nodes == [_node("finance")]
    assert edges == [contains_edge("finance", "revenue")]


def test_declared_children_become_contains_edges_in_sorted_order():
    parent_of = {"finance": None, "revenue": "finance", "tax": "finance"}
    nodes, edges = _contains_edges("finance", parent_of)
    assert nodes == [_node("revenue"), _node("tax")]
    assert edges == [
        contains_edge("finance", "revenue"),
        contains_edge("finance", "tax"),
    ]


def test_a_root_that_parents_nothing_emits_no_hierarchy_edge():
    # Every shipped playground context: no parent, parents nothing -> byte-identical graph.
    assert _contains_edges("revenue", {"revenue": None, "supply_chain": None}) == ([], [])


def test_a_transitive_unknown_ancestor_is_dropped_fail_closed():
    # revenue's DIRECT parent `finance` is a known domain -- revenue's own sync passed the
    # per-sync `validate_domain` when finance existed. But finance's parent is now a ghost
    # (an intermediate ancestor disabled AFTER that valid sync). The per-sync check cannot
    # see this; the whole-estate `validate_forest` must, and the edge must NOT be served.
    parent_of = {"revenue": "finance", "finance": "ghost"}
    assert _contains_edges("revenue", parent_of) == ([], [])


def test_a_cycle_drops_the_edge_fail_closed():
    assert _contains_edges("a", {"a": "b", "b": "a"}) == ([], [])


def test_a_broken_domain_elsewhere_does_not_suppress_an_unrelated_valid_edge():
    # `orphan` has an unknown parent, so the whole estate is invalid -- but the emit is
    # SCOPED to unverified endpoints (the hy-2zoy lesson: never blackout unrelated
    # domains). revenue and its child tax are on clean chains, so their edge still serves.
    parent_of = {"revenue": None, "tax": "revenue", "orphan": "ghost"}
    nodes, edges = _contains_edges("revenue", parent_of)
    assert nodes == [_node("tax")]
    assert edges == [contains_edge("revenue", "tax")]
