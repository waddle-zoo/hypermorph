"""The directive's own shape rules (hy-9lct, hy-bdff).

`domains` and `concepts` are one parameter in two halves: context to resolve,
and what that context has to cover. Either half alone is a malformed request,
and both are refused at construction so every adapter -- HTTP, MCP, CLI --
gives the same answer without restating the rule.
"""

from __future__ import annotations

import pytest

from hyperset.bundle import ContextDirective
from hyperset.bundle.schema import WARNING_CODES

DOMAIN = "revenue"
CONCEPT = "recognized_revenue"
REF = "superset:dataset:1"


def test_a_domain_and_the_claim_it_must_cover_are_accepted_together():
    directive = ContextDirective(domains=[DOMAIN], concepts=[CONCEPT])

    assert directive.domains == [DOMAIN]
    assert directive.concepts == [CONCEPT]
    assert directive.to_dict()["concepts"] == [CONCEPT]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"domains": [DOMAIN]},
        {"domains": [DOMAIN], "concepts": []},
        {"domains": [DOMAIN], "asset_refs": [REF]},
    ],
    ids=["concepts absent", "concepts empty", "with asset_refs"],
)
def test_a_domain_with_no_coverage_claim_is_refused(kwargs):
    """The half that used to be served as a `no_match` bundle. A missing
    required parameter is knowable from the request alone, so it gets a
    verdict about the request rather than a summary about the corpus."""
    with pytest.raises(ValueError) as refused:
        ContextDirective(**kwargs)

    assert "without saying what it must cover" in str(refused.value)
    assert "list_context_catalog" in str(refused.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"concepts": [CONCEPT]},
        {"concepts": [CONCEPT], "asset_refs": [REF]},
        {"concepts": [CONCEPT], "domains": []},
    ],
    ids=["domains absent", "with asset_refs", "domains empty"],
)
def test_a_coverage_claim_with_no_domain_is_refused(kwargs):
    """The mirror image: a claim nothing can check would be accepted, echoed
    back in `request.directive`, and never acted on."""
    with pytest.raises(ValueError) as refused:
        ContextDirective(**kwargs)

    assert "names no 'domains'" in str(refused.value)


def test_asset_refs_alone_still_need_no_coverage_claim():
    """`concepts` says what a DOMAIN must declare. Observed-only retrieval
    names no domain, so there is nothing for a claim to be about and
    requiring one would refuse a request that is complete."""
    directive = ContextDirective(asset_refs=[REF])

    assert directive.concepts == []
    assert not directive.is_empty


def test_a_directive_naming_nothing_is_empty_rather_than_malformed():
    """The empty directive has its own answer -- `plan_first_required`, which
    names the catalog -- and the pairing rule must not shadow it."""
    assert ContextDirective().is_empty


def test_an_unstated_coverage_claim_has_no_warning_code():
    """It is refused before retrieval, so it can never appear on a bundle. A
    code for it would be a second, contradictory answer to one request
    (hy-bdff)."""
    assert "coverage_not_declared" not in WARNING_CODES
    assert "domain_does_not_declare" in WARNING_CODES


def test_assist_is_on_without_being_asked_for_and_only_a_refusal_is_echoed():
    """ADR 0019 decision 1, the half that was already served: assist "runs
    where governance is silent, whether or not a caller asked". So the default
    is on -- and the echo carries the field only when it was DECLINED (hy-c3dl).

    A declining caller gets its refusal confirmed rather than inferring it from
    an absent section. Echoing `assist: true` on every other answer would
    advertise a key the transports refuse on input, since the allow-list and the
    MCP schema still take five until hy-hj9g, so a caller reading the echo would
    be told to send something that is rejected.
    """
    directive = ContextDirective(domains=[DOMAIN], concepts=[CONCEPT])

    assert directive.assist is True
    assert "assist" not in directive.to_dict(), "the default advertises nothing"
    assert (
        ContextDirective(domains=[DOMAIN], concepts=[CONCEPT], assist=False).to_dict()["assist"]
        is False
    )


def test_declining_assist_is_the_only_value_that_changes_the_request():
    """The asymmetry IS the decision (hy-c9mb): refusable, never demandable.

    `assist=True` must be a no-op rather than a request, so a directive that
    passes it is byte-for-byte the directive that omits it -- there is no state
    in which asking makes a section appear that absence would not have
    produced. `assist=False` is the only value that carries information.
    """
    omitted = ContextDirective(domains=[DOMAIN], concepts=[CONCEPT])
    demanded = ContextDirective(domains=[DOMAIN], concepts=[CONCEPT], assist=True)
    declined = ContextDirective(domains=[DOMAIN], concepts=[CONCEPT], assist=False)

    assert demanded.to_dict() == omitted.to_dict(), "asking for assist is not a request"
    assert declined.to_dict() != omitted.to_dict(), "declining it is"
    assert declined.to_dict()["assist"] is False


def test_declining_assist_is_not_a_coverage_claim_and_does_not_relax_the_pairing():
    """The opt-out is orthogonal to the rule that made `concepts` required.

    A caller could otherwise read "governed answer alone" as "no coverage claim
    needed", which would restore exactly the request hy-9lct refused: a domain
    named with nothing to verify it against.
    """
    with pytest.raises(ValueError, match="without saying what it must cover"):
        ContextDirective(domains=[DOMAIN], assist=False)

    assert "assist_declined" not in WARNING_CODES, (
        "a refused assist is a request shape, not something that went wrong with retrieval"
    )


def test_domains_are_casefolded_so_the_request_agrees_with_the_stored_domain():
    """The request side of the hy-gh-282 casefold: `parse_context` casefolds the
    stored domain, so a directive must normalize `domains` the same way, or a
    request naming 'Revenue' would miss a 'revenue' store and a previously
    resolvable domain would break. The store, the collision query, and the
    request then genuinely agree on case."""
    directive = ContextDirective(domains=["Revenue", "  Logistics "], concepts=[CONCEPT])

    assert directive.domains == ["revenue", "logistics"]
    # And the echo reports the canonical form, not the caller's casing.
    assert directive.to_dict()["domains"] == ["revenue", "logistics"]
