"""The operator view's linked-evidence surface over a real store (hy-4dke, S3).

Read-only, and it REUSES `_linked_evidence` -- the exact per-ref evidence the
served bundle carries -- so the ops surface and the bundle cannot drift. The
tests cover the observed-asset + freshness fields, a source_deleted deprecation, a
prohibited_by_context deprecation, and assert the ops evidence equals the served
bundle's for the same domain.
"""

from __future__ import annotations

from hyperset.ops.status import read_linked_evidence
from hyperset.repositories.postgres import (
    PostgresContextRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
)
from tests.postgres.test_context_bundle import (
    APPROVED_DATASET,
    APPROVED_REF,
    PROHIBITED_SOURCE,
    _resolve,
)


def _revenue(session_factory):
    return next(d for d in read_linked_evidence(session_factory) if d.domain == "revenue")


def _delete_the_observed_dataset(session_factory, revenue_slice):
    assets = PostgresObservedAssetRepository(session_factory)
    gone = PostgresSyncRepository(session_factory).begin_run(
        revenue_slice["connection_id"], mode="full"
    )
    assets.mark_missing_deleted(
        connection_id=revenue_slice["connection_id"],
        asset_type="dataset",
        seen_external_ids=set(),
        sync_run_id=gone.id,
    )


def test_observed_assets_and_freshness_are_surfaced(session_factory, revenue_slice):
    evidence = _revenue(session_factory)
    assert evidence.observed_assets
    asset = evidence.observed_assets[0]
    assert set(asset) == {
        "ref",
        "connector",
        "observed_version",
        "observed_version_id",
        "content_sha256",
        "linked_version_id",
        "governance",
    }

    freshness = {item["ref"]: item for item in evidence.freshness}
    assert APPROVED_REF in freshness
    entry = freshness[APPROVED_REF]
    assert entry["last_observed_at"] is not None
    for field in ("observed_version_at", "source_modified_at", "deleted_at"):
        assert field in entry


def test_a_source_deleted_asset_is_surfaced_as_a_deprecation(session_factory, revenue_slice):
    _delete_the_observed_dataset(session_factory, revenue_slice)
    evidence = _revenue(session_factory)

    deprecations = {item["ref"]: item for item in evidence.deprecations}
    assert deprecations[APPROVED_REF]["kind"] == "source_deleted"
    freshness = {item["ref"]: item for item in evidence.freshness}
    assert freshness[APPROVED_REF]["deleted_at"] is not None


def test_a_prohibited_source_is_surfaced_as_a_deprecation(session_factory, revenue_slice):
    evidence = _revenue(session_factory)
    deprecations = {item["ref"]: item for item in evidence.deprecations}
    assert deprecations[PROHIBITED_SOURCE]["kind"] == "prohibited_by_context"


def test_ops_evidence_matches_the_served_bundle_with_no_drift(session_factory, revenue_slice):
    # The reuse proof: the ops surface carries exactly what the bundle serves,
    # because both go through _linked_evidence. Delete an asset first so freshness
    # and deprecations are non-trivial.
    _delete_the_observed_dataset(session_factory, revenue_slice)
    served = _resolve(session_factory).linked_evidence
    evidence = _revenue(session_factory)

    assert tuple(served["freshness"]) == evidence.freshness
    assert tuple(served["deprecations"]) == evidence.deprecations
    served_assets = {asset["ref"]: asset for asset in served["observed_assets"]}
    assert {asset["ref"] for asset in evidence.observed_assets} == set(served_assets)
    for asset in evidence.observed_assets:
        expected = served_assets[asset["ref"]]
        assert asset == {
            key: expected.get(key)
            for key in (
                "ref",
                "connector",
                "observed_version",
                "observed_version_id",
                "content_sha256",
                "linked_version_id",
                "governance",
            )
        }


def test_the_linked_version_stays_pinned_across_source_drift(session_factory, revenue_slice):
    # The pinned-identity guarantee (hy-hske round 3). `observed_version`/`content_sha256`
    # describe the asset's CURRENT version and DRIFT when the source moves after the commit
    # was pinned; `linked_version_id` is the exact observed version Git linked against and
    # must NOT move. Before drift the two version ids are equal (the source has not moved);
    # after a new version is observed for the same asset, `linked_version_id` still names the
    # pinned version while `observed_version_id` advances -- so `hyperset ops status` can
    # still identify what Git pinned. Dropping either field (as the projection did before
    # this fix) makes that impossible, which is what this test REDS on.
    assets = PostgresObservedAssetRepository(session_factory)
    pinned_version_id = assets.get_by_external_id(
        connection_id=revenue_slice["connection_id"],
        external_id=APPROVED_DATASET,
        asset_type="dataset",
    ).current_version.id

    before = next(a for a in _revenue(session_factory).observed_assets if a["ref"] == APPROVED_REF)
    assert before["governance"] == "git_linked"  # a governed ref Git linked to an observation
    assert before["linked_version_id"] == pinned_version_id
    assert before["observed_version_id"] == pinned_version_id  # equal: the source has not moved

    # DRIFT: observe a NEW version of the same asset (a changed payload -> a new content hash
    # -> a new immutable current version).
    run = PostgresSyncRepository(session_factory).begin_run(
        revenue_slice["connection_id"], mode="full"
    )
    _asset, change = assets.upsert(
        connection_id=revenue_slice["connection_id"],
        external_id=APPROVED_DATASET,
        asset_type="dataset",
        sync_run_id=run.id,
        raw_payload={"drifted": "a new content payload", "uuid": APPROVED_DATASET},
    )
    assert change == "updated", change
    new_version_id = assets.get_by_external_id(
        connection_id=revenue_slice["connection_id"],
        external_id=APPROVED_DATASET,
        asset_type="dataset",
    ).current_version.id
    assert new_version_id != pinned_version_id  # the source really drifted

    after = next(a for a in _revenue(session_factory).observed_assets if a["ref"] == APPROVED_REF)
    # The pinned identity SURVIVES the drift; the current-version fields move.
    assert after["linked_version_id"] == pinned_version_id, "the pinned link drifted"
    assert after["observed_version_id"] == new_version_id
    assert after["observed_version_id"] != after["linked_version_id"]  # drift is now legible
    assert after["content_sha256"] != before["content_sha256"]


def test_a_source_with_no_snapshot_yields_no_evidence(session_factory):
    PostgresContextRepository(session_factory).register_source(
        repository="/tmp/repo", ref="main", path="domains/unsynced"
    )
    assert read_linked_evidence(session_factory) == []


def test_read_linked_evidence_delegates_to_the_resolver_not_a_parallel_derivation(
    session_factory, revenue_slice, monkeypatch
):
    # MUTATION-LOAD-BEARING no-drift binding (hy-hske, adversary). The value
    # comparison above passes for ANY implementation that reproduces the fixture's
    # numbers, including a parallel re-derivation -- which is exactly the drift the
    # "reuse" claim forbids. Bind the CALL: replace resolver._linked_evidence with
    # a spy returning a distinctive sentinel and assert the ops row is built from
    # it, for the pinned snapshot and the empty directive. A derivation that never
    # calls _linked_evidence produces neither the sentinel nor the recorded call
    # and REDS here.
    from hyperset.bundle import resolver
    from hyperset.bundle.directive import ContextDirective

    sentinel_asset = {
        "ref": "superset:dataset:sentinel",
        "connector": "superset",
        "observed_version": "v-sentinel",
        "observed_version_id": "ver-sentinel-current",
        "content_sha256": "sha-sentinel",
        "linked_version_id": "ver-sentinel-linked",
        "governance": "governed",
        "extra": "dropped-by-projection",
    }
    sentinel = {
        "observed_assets": [sentinel_asset],
        "freshness": [{"ref": "superset:dataset:sentinel", "last_observed_at": "sentinel"}],
        "deprecations": [{"ref": "superset:dataset:sentinel", "kind": "sentinel_only"}],
    }
    calls: list[tuple] = []

    def spy(session_factory, *, snapshot, directive):
        calls.append((snapshot.id, directive))
        return sentinel, [], []

    monkeypatch.setattr(resolver, "_linked_evidence", spy)

    rows = read_linked_evidence(session_factory)

    # It was actually invoked -- once per pinned source -- with the pinned
    # snapshot and an empty directive, not re-derived.
    sources = PostgresContextRepository(session_factory).list_sources()
    pinned = [s for s in sources if s.current_snapshot is not None]
    assert calls, "read_linked_evidence never called resolver._linked_evidence"
    assert len(calls) == len(pinned)
    called_snapshot_ids = {snapshot_id for snapshot_id, _ in calls}
    assert called_snapshot_ids == {s.current_snapshot.id for s in pinned}
    for _snapshot_id, directive in calls:
        assert directive == ContextDirective()
        assert not directive.domains and not directive.asset_refs

    # And the ops row carries exactly what the resolver returned -- the sentinel,
    # projected to the surfaced keys -- so a path that bypassed the resolver could
    # not have produced it.
    revenue = next(r for r in rows if r.domain == "revenue")
    assert revenue.observed_assets == (
        {
            "ref": "superset:dataset:sentinel",
            "connector": "superset",
            "observed_version": "v-sentinel",
            "observed_version_id": "ver-sentinel-current",
            "content_sha256": "sha-sentinel",
            "linked_version_id": "ver-sentinel-linked",
            "governance": "governed",
        },
    )
    assert revenue.freshness == tuple(sentinel["freshness"])
    assert revenue.deprecations == tuple(sentinel["deprecations"])
