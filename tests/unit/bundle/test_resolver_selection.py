"""The estate-ambiguity vs caller-multiple-domains split in `_select` (hy-gh-282).

`_select` receives sources already filtered to enabled + current-snapshot
(resolver.py), so these fakes stand in for that: the question here is purely how
one requested domain claimed by two sources is told apart from a caller naming
two domains.
"""

from __future__ import annotations

from types import SimpleNamespace

from hyperset.bundle.resolver import _select
from hyperset.bundle.schema import DOMAIN_AMBIGUOUS


def _source(source_id: str, domain: str, *, commit: str = "abc123", declares=()):
    return SimpleNamespace(
        id=source_id,
        repository=f"repo/{source_id}",
        ref="main",
        path=domain,
        enabled=True,
        current_snapshot=SimpleNamespace(
            domain=domain,
            commit_sha=commit,
            normalized={"definitions": [{"term": term} for term in declares]},
        ),
    )


def _directive(*domains: str, concepts=()):
    return SimpleNamespace(domains=list(domains), concepts=list(concepts))


def _codes(selection):
    return [w["code"] for w in selection.warnings]


def test_one_domain_claimed_by_two_sources_is_ambiguous_not_multiple_domains():
    selection = _select(
        [_source("src-a", "revenue", commit="aaa"), _source("src-b", "revenue", commit="bbb")],
        _directive("revenue", concepts=["recognized_revenue"]),
    )

    assert selection.refused
    assert selection.matched is None  # no authority silently chosen between two commits
    assert _codes(selection) == [DOMAIN_AMBIGUOUS]
    message = selection.warnings[0]["message"]
    # Names the conflicting sources AND commits, and points at the recovery.
    assert "src-a" in message and "src-b" in message
    assert "aaa" in message and "bbb" in message
    assert "hyperset context disable" in message
    # It is the ESTATE that is named, not the directive.
    assert "the directive is correct" in message


def test_two_distinct_domains_resolve_instead_of_multiple_domains():
    # #230 slice 3 (hy-cnto): a directive naming two distinct governed domains no
    # longer refuses with `multiple_domains` -- it selects both for an independent
    # per-domain resolve. Each domain declares the concept it is claimed to cover.
    src_a = _source("src-a", "revenue", declares=["revenue_x"])
    src_b = _source("src-b", "marketing", declares=["marketing_y"])
    selection = _select(
        [src_a, src_b],
        _directive("revenue", "marketing", concepts=["revenue_x", "marketing_y"]),
    )

    assert not selection.refused
    assert selection.matched is None  # no single authority is chosen
    assert selection.matched_domains == [src_a, src_b]  # both selected, union-covered
    assert selection.warnings == []


def test_a_concept_no_named_domain_declares_fails_the_multi_domain_request():
    selection = _select(
        [
            _source("src-a", "revenue", declares=["revenue_x"]),
            _source("src-b", "marketing", declares=["marketing_y"]),
        ],
        _directive("revenue", "marketing", concepts=["revenue_x", "nobody_declares"]),
    )
    assert selection.refused
    assert _codes(selection) == ["domain_does_not_declare"]
    assert "nobody_declares" in selection.warnings[0]["message"]


def test_a_named_domain_declaring_none_of_the_claim_fails_the_multi_domain_request():
    # union coverage completion (hy-cnto): every named domain must contribute >=1
    # claimed concept, so no domain is resolved against an empty per-domain claim.
    selection = _select(
        [
            _source("src-a", "revenue", declares=["revenue_x", "marketing_y"]),
            _source("src-b", "marketing", declares=["unrelated"]),
        ],
        _directive("revenue", "marketing", concepts=["revenue_x", "marketing_y"]),
    )
    assert selection.refused
    assert _codes(selection) == ["domain_does_not_declare"]
    assert "marketing" in selection.warnings[0]["message"]


def test_ambiguity_takes_precedence_when_one_of_several_domains_collides():
    # revenue is claimed twice; logistics once. The ambiguity is reported first
    # because that domain cannot be served at all.
    selection = _select(
        [
            _source("src-a", "revenue", commit="aaa"),
            _source("src-b", "revenue", commit="bbb"),
            _source("src-c", "logistics"),
        ],
        _directive("revenue", "logistics", concepts=["x"]),
    )

    assert _codes(selection) == [DOMAIN_AMBIGUOUS]
    assert "revenue" in selection.warnings[0]["message"]
