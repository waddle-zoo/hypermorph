"""The gather producer's boundary, proven by RESOLUTION not membership (hy-1f9h).

Flywheel step 2 gathers observed sources for a miss and hands them to a later
authoring step. ADR 0024 decision 5 says the check that AI-sourced output cannot
enter a governed section is stated over DERIVATION AND RESOLUTION, not presence,
and the mayor's implementer flag on this bead is that no such instrument exists
yet even for the `git_linked` case -- ADR 0019's own text says "only a
conformance implementation performs it" and none did. This is that instrument.

Run against the same slice as the resolver's discovery tests: the pinned
Superset 6.1.0 captures and the checked-in revenue commit, real Postgres. The
churn question is the one the revenue commit declares no concept for, so it
reaches the coverage refusal and therefore the gather path.
"""

from __future__ import annotations

import pytest

from hyperset.bundle import ContextDirective, resolve_analytics_context
from hyperset.bundle.discovery import CANDIDATE_SOURCES, OBSERVED
from hyperset.bundle.gather import gather
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresGovernedContextRepository,
    PostgresObservedAssetRepository,
    PostgresReviewRepository,
)

CHURN_QUESTION = "How much did customer churn cost us last quarter?"
FABRICATED_VERSION = "oav-FABRICATED-BY-AISOURCE"


def _gather_revenue_churn(session_factory) -> dict:
    section = gather(domain="revenue", undeclared=["churn"], session_factory=session_factory)
    assert section is not None, "the churn miss over the real estate must gather candidates"
    assert section["candidates"], "the fixture is the refusal that has candidates"
    return section


def _resolves_to_stored_version(session_factory, candidate: dict) -> bool:
    """The candidate's served identity, checked against the store it CLAIMS to
    come from -- BY IDENTITY. The served `external_id` is resolved to an asset
    row, and the served `observed_version_id` must BE that row's current
    version. A fabricated id fails here on the ATTRIBUTES of the row it resolves
    to, or on the absence of any row -- which is exactly what a set-membership
    check over the producer's own output cannot see, because the fabrication is
    a member of that set.
    """
    assets = PostgresObservedAssetRepository(session_factory)
    try:
        asset = assets.get_by_external_id(
            connection_id=candidate["connection_id"],
            external_id=candidate["external_id"],
            asset_type=candidate["asset_type"],
        )
    except NotFoundError:
        return False
    version = asset.current_version
    return (
        version is not None
        and candidate["observed_version_id"] == version.id
        and candidate["asset_id"] == asset.id
        and candidate["connection_id"] == asset.connection_id
    )


@pytest.mark.postgres
def test_every_gathered_candidate_resolves_to_a_stored_observed_version(
    session_factory, revenue_slice
):
    """The generalized resolution check ADR 0024 decision 5 names. Every source
    the producer gathered is a real observed asset whose served version pin is
    that asset's actual current version -- the producer cites, it does not
    invent."""
    section = _gather_revenue_churn(session_factory)

    for candidate in section["candidates"]:
        assert _resolves_to_stored_version(session_factory, candidate), (
            f"gathered candidate {candidate['ref']} does not resolve by identity to a stored "
            "observed version"
        )


@pytest.mark.postgres
def test_a_fabricated_identity_passes_membership_and_fails_resolution(
    session_factory, revenue_slice
):
    """The teeth (ADR 0019's own doctored-entry method). A set-membership check
    over the producer's output would accept a fabricated citation -- the
    fabrication is in the set -- so this shows both directions: membership says
    yes, resolution says no. That gap is the whole reason the check is stated
    over resolution.

    Two ghosts, because the smuggle has two shapes:
      - a wholly absent asset: `get_by_external_id` finds no row at all;
      - a REAL observed asset with a LIED-ABOUT version pin: the row resolves,
        and the served `observed_version_id` is not its current version. This is
        the one a membership check is blindest to -- the external_id is genuine.
    """
    section = _gather_revenue_churn(session_factory)
    real = section["candidates"][0]

    ghost_absent = {
        **real,
        "external_id": "ghost-dataset-no-such-asset",
        "asset_id": "oa-ghost",
        "observed_version_id": FABRICATED_VERSION,
    }
    ghost_versioned = {**real, "observed_version_id": FABRICATED_VERSION}

    # Membership would pass both: each fabricated id is a member of the set that
    # includes it, which is all a presence check can ever ask.
    smuggled = [*section["candidates"], ghost_absent, ghost_versioned]
    served_version_ids = {candidate["observed_version_id"] for candidate in smuggled}
    assert ghost_absent["observed_version_id"] in served_version_ids
    assert ghost_versioned["observed_version_id"] in served_version_ids

    # Resolution rejects both: one has no row, the other's row does not carry the
    # version the ghost pinned.
    assert not _resolves_to_stored_version(session_factory, ghost_absent)
    assert not _resolves_to_stored_version(session_factory, ghost_versioned)
    # And the real candidate the ghost was cloned from still resolves, so the
    # rejection is about the fabrication, not about the check refusing everything.
    assert _resolves_to_stored_version(session_factory, real)


@pytest.mark.postgres
def test_gather_reaches_no_writer(session_factory, revenue_slice, monkeypatch):
    """ADR 0024 decision 4: AI-sourcing has no writer it may call. Every
    observation-, review-, and governed-context writer is armed to raise; the
    producer still returns its section, so it reached none of them."""

    def forbidden(name):
        def _stub(*args, **kwargs):
            raise AssertionError(f"gather called the forbidden writer {name!r}")

        return _stub

    for method in ("upsert", "mark_missing_deleted", "replace_relationships"):
        monkeypatch.setattr(PostgresObservedAssetRepository, method, forbidden(method))
    monkeypatch.setattr(PostgresReviewRepository, "approve", forbidden("approve"))
    monkeypatch.setattr(
        PostgresGovernedContextRepository, "propose_version", forbidden("propose_version")
    )

    section = _gather_revenue_churn(session_factory)
    assert section["kind"] == CANDIDATE_SOURCES


@pytest.mark.postgres
def test_gathered_output_cannot_enter_a_governed_section_of_the_served_bundle(
    session_factory, revenue_slice
):
    """The integration end (ADR 0024 conformance sketch 3-4). The gathered
    section rides on the served `no_match` bundle and touches nothing governed:
    no candidate ref or version pin is in the citation surface the evaluator
    derives `source_refs` from, the observed-evidence list is empty, every
    candidate is labelled `observed`, execution stays false, and the section's
    own `assist_id` is not a component of `bundle_id`.
    """
    bundle = resolve_analytics_context(
        query=CHURN_QUESTION,
        directive=ContextDirective(domains=["revenue"], concepts=["churn"]),
        session_factory=session_factory,
    )
    payload = bundle.to_dict()

    assert bundle.status == "no_match"
    assert bundle.provenance_refs == []
    assert bundle.linked_evidence["observed_assets"] == []

    section = bundle.assist
    assert section is not None
    assert section["kind"] == CANDIDATE_SOURCES
    assert section["produced_by"]["model"] is None
    for candidate in section["candidates"]:
        assert candidate["governance"] == OBSERVED
        assert candidate["ref"] not in bundle.provenance_refs
        assert f"observed_version:{candidate['observed_version_id']}" not in bundle.provenance_refs

    assert payload["execution"] == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }
    # Its own identity, never the bundle's (ADR 0019 floor 8).
    assert section["assist_id"] not in payload["bundle_id"]
