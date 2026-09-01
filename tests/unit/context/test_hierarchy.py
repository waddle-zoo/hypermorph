"""ADR-0031's acceptance list, executable against the hierarchy module (hy-53l9).
`contains_edge`/`validate_forest` now feed the served `domain_graph` -- the emitting
slice hy-gh-230 landed the immediate parent/child `contains` edges (the resolver-path
emit is covered in the bundle tests), which took `SCHEMA_VERSION` to 17 at the time; it
is 19 now, after the multi-domain `domains[]` and composed-graph slices moved it again.
`expand`
-- the bounded, cycle-safe multi-level traversal -- still emits nothing into the served
graph; wiring it is the later expansion slice of #230. Here the node/edge shape and that
expansion are pinned as specified.
"""

from __future__ import annotations

from hyperset.context.hierarchy import (
    _DATA_RELATIONS,
    CONTAINS,
    contains_edge,
    domain_node_id,
    expand,
    unverified_domains,
    validate_domain,
    validate_forest,
)


def _chain(*pairs: tuple[str, str]) -> list[dict]:
    return [contains_edge(parent, child) for parent, child in pairs]


# 1 (governed edge shape)
def test_a_contains_edge_is_a_governed_edge_with_ref_carrying_ids():
    assert contains_edge("revenue", "finance") == {
        "from": "domain:revenue",
        "to": "domain:finance",
        "relation": "contains",
        "evidence": "git",
    }


# 2 (depth-agnostic: same edge and expansion at any depth)
def test_a_three_level_chain_expands_with_no_per_level_type():
    edges = _chain(("a", "b"), ("b", "c"))
    reached, contains, warnings = expand("a", edges)
    assert reached == ["domain:a", "domain:b", "domain:c"]
    assert contains == edges
    assert warnings == []
    # c is a plain domain node, not a distinct "subdomain" kind.
    assert domain_node_id("c") == "domain:c"


# 3 (no name inference)
def test_expansion_follows_only_declared_edges_never_a_name_prefix():
    # revenue_eu is a REAL node (it contains its own child) that shares a prefix with
    # revenue but has NO declared edge to it -> the shared prefix creates no link.
    edges = _chain(("revenue", "finance"), ("revenue_eu", "finance_eu"))
    reached, _, _ = expand("revenue", edges)
    assert reached == ["domain:revenue", "domain:finance"]
    assert "domain:revenue_eu" not in reached


# 4 (distinct from data edges)
def test_contains_is_distinct_from_every_data_relation_and_ignores_them():
    assert CONTAINS == "contains"
    assert CONTAINS not in _DATA_RELATIONS
    # A join/lineage edge between two domains is NOT containment and is not followed.
    data_edge = {"from": "domain:a", "to": "domain:b", "relation": "reads", "evidence": "git"}
    reached, contains, _ = expand("a", [data_edge])
    assert reached == ["domain:a"]
    assert contains == []


# 5 (cycle-safe, both layers)
def test_validate_forest_rejects_a_cycle_and_an_unknown_parent():
    assert validate_forest({"a": None, "b": "a", "c": "b"}) == []  # a valid forest
    assert any("cycle" in r for r in validate_forest({"a": "b", "b": "a"}))
    assert any("unknown parent" in r for r in validate_forest({"a": "ghost"}))


def test_expand_terminates_on_a_cyclic_edge_set():
    # A malformed cyclic edge set must not loop the walk (visited set).
    edges = _chain(("a", "b"), ("b", "a"))
    reached, _, _ = expand("a", edges)
    assert set(reached) == {"domain:a", "domain:b"}


# 6 (bounded: depth, breadth, disclosure)
def test_max_hops_caps_depth_and_discloses_the_drop():
    edges = _chain(("a", "b"), ("b", "c"))
    reached, _, warnings = expand("a", edges, max_hops=1)
    assert reached == ["domain:a", "domain:b"]  # c is two hops out, dropped
    # The drop is DISCLOSED, never silent (Decision 6 / _bounded posture).
    assert warnings and "hop" in warnings[0]


def test_max_components_caps_breadth_and_discloses():
    edges = _chain(("a", "b"), ("a", "c"), ("a", "d"))
    reached, _, warnings = expand("a", edges, max_components=2)
    assert len(reached) == 2
    assert warnings and "bounded" in warnings[0]


# 7 (provenance-preserving)
def test_every_returned_edge_carries_git_provenance():
    edges = _chain(("a", "b"), ("b", "c"))
    _, contains, _ = expand("a", edges)
    assert contains and all(edge["evidence"] == "git" for edge in contains)


# 8 (collision-safe ids -- hy-c89s)
def test_two_distinct_domains_have_two_distinct_ids():
    assert domain_node_id("a") != domain_node_id("ab")
    assert domain_node_id("revenue") == "domain:revenue"


# 9 (invariant: emitting the contains edge holds tools_hash, moves the served version)
def test_emitting_the_contains_edge_holds_tools_hash_and_moves_schema_version():
    # The `contains` edge is a served OUTPUT shape (a domain_graph node/edge), not a
    # resolve-path input schema, so it does NOT move `tools_hash` -- that pin covers only
    # RESOLVE_PATH_OPERATIONS input schemas. It DOES move `SCHEMA_VERSION`, because a new
    # always-available served edge is a shape a caller can now RECEIVE (ADR 0018).
    from hyperset.bundle.schema import SCHEMA_VERSION
    from hyperset.planner.loop import tools_hash

    assert tools_hash() == "sha256:fe930a003b731211"
    assert SCHEMA_VERSION == 26


# 10 (the per-source sync check: scoped to ONE domain, hy-2zoy)
def test_validate_domain_flags_only_the_named_domains_own_faults():
    # A valid child in a valid forest: no reason.
    assert validate_domain("regional", {"revenue": None, "regional": "revenue"}) == []
    # Its own parent is unknown -> flagged.
    assert any("unknown parent" in r for r in validate_domain("regional", {"regional": "ghost"}))
    # Its own chain cycles -> flagged.
    assert any(
        "cycle" in r
        for r in validate_domain("revenue", {"revenue": "regional", "regional": "revenue"})
    )


def test_validate_domain_ignores_a_dangling_parent_on_another_domain():
    # `orphan` has a dangling parent (its parent `ghost` is not declared), but the
    # domain being validated is `sales`, whose own chain is clean. The per-source check
    # must NOT punish `sales` for `orphan`'s pre-existing fault -- that is the wedge
    # `validate_forest` (whole-estate) would cause. `validate_forest` DOES flag it,
    # proving the two checks differ.
    parent_of = {"orphan": "ghost", "sales": None}
    assert validate_domain("sales", parent_of) == []
    assert any("orphan" in r for r in validate_forest(parent_of))


# 11 (the emit-path localizer: whole-estate, transitive, blame-local -- hy-gh-230)
def test_unverified_domains_is_empty_for_a_governed_forest():
    forest = {"revenue": None, "regional": "revenue", "supply_chain": None}
    assert unverified_domains(forest) == set()


def test_unverified_domains_flags_a_transitive_unknown_ancestor_that_validate_domain_misses():
    # revenue's DIRECT parent `finance` exists, so `validate_domain` does not flag revenue
    # (it checks only the direct parent for existence). But finance's parent is a ghost, so
    # revenue's ancestor CHAIN is dangling and its `contains` edge must not be served.
    parent_of = {"revenue": "finance", "finance": "ghost"}
    assert validate_domain("revenue", parent_of) == []  # the gap `validate_forest` covers
    assert unverified_domains(parent_of) == {"revenue", "finance"}


def test_unverified_domains_flags_a_cycle():
    assert unverified_domains({"a": "b", "b": "a"}) == {"a", "b"}


def test_unverified_domains_keeps_blame_local_and_does_not_blackout_a_clean_domain():
    # A broken `orphan` makes the estate invalid, but a clean chain is still verifiable --
    # the no-blackout property the served emit needs (hy-2zoy lesson).
    parent_of = {"revenue": None, "regional": "revenue", "orphan": "ghost"}
    assert unverified_domains(parent_of) == {"orphan"}
