"""Walking-skeleton step 9 against real evidence (hy-gh-31).

One `ContextBundle` compiled from the pinned Git commit plus the observations
and findings the earlier steps persisted. Same slice as the processor's
tests: pinned Superset 6.1.0 REST captures, a repository built with the `git`
CLI from the checked-in revenue context, real Postgres.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyperset.bundle import ContextDirective, resolve_analytics_context
from hyperset.bundle.discovery import (
    CANDIDATE_SOURCES,
    DECLARED_REFERENCES,
    EXPRESSION_AGREEMENT,
    GIT_ENGAGEMENT,
    NO_GOVERNING_DOMAIN,
    PROHIBITED_BY_CONTEXT,
)
from hyperset.bundle.reconcile import (
    BUNDLE_RECONCILIATION,
    PROHIBITED_BUT_REFERENCED,
    SOURCE_DELETED_WHILE_GOVERNED,
)
from hyperset.bundle.schema import (
    DOMAIN_DOES_NOT_DECLARE,
    GIT_LINKED,
    MAX_HOPS_NOT_APPLICABLE,
    OBSERVED_PAYLOADS_OMITTED,
    OVER_CONTEXT_BUDGET,
    PLAN_FIRST_REQUIRED,
    PROJECTION_BOUNDED,
    REF_AWAITING_SYNC,
    REF_CORROBORATED_LATE,
    REF_NOT_OBSERVED,
    REF_OUTSIDE_CONTEXT,
    UNKNOWN_DOMAIN,
)
from hyperset.context.evidence import ObservedEvidenceResolver
from hyperset.context.schema import EvidenceRef
from hyperset.processor import run_sync_processing
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresContextRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES
from tests.postgres.conftest import (
    APPROVED_DATASET,
    DRIFTED_EXPRESSION,
    GIT_EXPRESSION,
    GLOSSARY_TERM,
    build_git_before_evidence,
    build_unread_superset,
    corroborate_superset,
    make_superset_connection,
    observe_glossary_term,
    sync_revenue_context,
    sync_superset,
)

QUESTION = "Which source and rules should an analyst use for recognized revenue by region?"
# The question the revenue commit does NOT declare a concept for, which is the
# one request that reaches the coverage refusal and therefore the assist path.
CHURN_QUESTION = "How much did customer churn cost us last quarter?"
APPROVED_REF = f"superset:dataset:{APPROVED_DATASET}"
GLOSSARY_REF = f"datahub:glossary_term:{GLOSSARY_TERM}"
APPROVED_SOURCE = "table:postgres:analytics.public.finance_orders_daily"
PROHIBITED_SOURCE = "table:postgres:analytics.public.raw_payments"
# Every ref the checked-in revenue commit declares, in the order the resolver
# serves them. Three are Superset datasets nothing observes until that
# connection is synced, which is what makes the commit-before-connector case
# reachable at all.
SUPERSET_DECLARED = sorted(
    [
        APPROVED_REF,
        "superset:dataset:5bcf01e3-3f70-50d2-bb31-562b627b09b8",
        "superset:dataset:6f4976c2-25ea-5d98-b714-9ca8e6c9b7e4",
    ]
)
# The same three, in the order discovery ranks them for a claim the revenue
# domain does not declare: approved AND computing what the commit declares,
# then approved alone, then the one the commit prohibits.
SUPERSET_DECLARED_BY_RANK = [
    APPROVED_REF,
    "superset:dataset:5bcf01e3-3f70-50d2-bb31-562b627b09b8",
    "superset:dataset:6f4976c2-25ea-5d98-b714-9ca8e6c9b7e4",
]


def _resolve(session_factory, **kwargs):
    """Resolve the canonical question. The directive names the revenue
    domain unless a test names something else: nothing is inferred from the
    wording of the question (hy-x7f, GitHub #70).

    It also carries the coverage claim that domain must declare (hy-9lct).
    The canonical question is about recognized revenue, so the claim is the
    true one; the tests that make the claim false say so explicitly.
    """
    query = kwargs.pop("query", QUESTION)
    kwargs.setdefault("domains", ["revenue"])
    if kwargs["domains"]:
        kwargs.setdefault("concepts", ["recognized_revenue"])
    return resolve_analytics_context(
        query=query, directive=ContextDirective(**kwargs), session_factory=session_factory
    )


def _undeclared_ref(session_factory, revenue_slice) -> str:
    """A real observed asset the revenue context never declares."""
    from hyperset.repositories.postgres import PostgresContextRepository

    snapshot = PostgresContextRepository(session_factory).get_snapshot(
        revenue_slice["context"].snapshot_id
    )
    declared = {ref["ref"] for ref in snapshot.evidence_refs}
    refs = sorted(
        f"superset:{asset.asset_type}:{asset.external_id}"
        for asset in PostgresObservedAssetRepository(session_factory).list_all(
            connection_id=revenue_slice["connection_id"], workspace=ALL_WORKSPACES
        )
    )
    return next(ref for ref in refs if ref not in declared)


@pytest.mark.postgres
def test_the_bundle_answers_from_the_exact_git_commit(session_factory, revenue_slice):
    bundle = _resolve(session_factory)

    assert bundle.status == "governed"
    authority = bundle.context_authority
    assert authority["type"] == "git"
    assert authority["ref"] == "main"
    assert authority["path"] == "domains/revenue"
    assert authority["commit_sha"] == revenue_slice["context"].commit_sha
    assert authority["context_snapshot_id"] == revenue_slice["context"].snapshot_id
    assert authority["owner_refs"] == ["team:finance-data"]
    # "governed" is about who owns the meaning, and the response says so.
    assert "Hyperset did not author or approve this meaning" in bundle.resolution["summary"]
    assert bundle.to_dict()["execution"] == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }


@pytest.mark.postgres
def test_every_semantic_field_traces_to_that_snapshot(session_factory, revenue_slice):
    from hyperset.repositories.postgres import PostgresContextRepository

    snapshot = PostgresContextRepository(session_factory).get_snapshot(
        revenue_slice["context"].snapshot_id
    )

    instructions = _resolve(session_factory).instructions

    assert instructions["definitions"] == snapshot.normalized["definitions"]
    assert instructions["approved_sources"] == snapshot.normalized["approved_sources"]
    assert instructions["prohibited_sources"] == snapshot.normalized["prohibited_sources"]
    assert instructions["fields"] == snapshot.normalized["fields"]
    assert instructions["joins"] == snapshot.normalized["joins"]
    assert instructions["filters"] == snapshot.normalized["filters"]
    assert instructions["grain"] == snapshot.normalized["grain"]
    assert instructions["caveats"] == snapshot.normalized["caveats"]
    # The manifest's `checks` is the contract's `validations`; same content.
    assert instructions["validations"] == snapshot.normalized["checks"]
    assert instructions["context_doc"] == snapshot.files["context.md"]


@pytest.mark.postgres
def test_every_evidence_claim_pins_an_exact_observed_version(session_factory, revenue_slice):
    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=revenue_slice["connection_id"],
        external_id=APPROVED_DATASET,
        asset_type="dataset",
    )

    bundle = _resolve(session_factory)

    observed = {item["ref"]: item for item in bundle.linked_evidence["observed_assets"]}
    approved = observed[APPROVED_REF]
    assert approved["observed_version_id"] == dataset.current_version.id
    assert approved["content_sha256"] == dataset.current_version.content_hash
    assert approved["connector"] == "superset"
    assert approved["governed_source_ref"] == APPROVED_SOURCE
    assert GLOSSARY_REF not in observed

    assert (
        f"git_context:{revenue_slice['context'].snapshot_id}@{bundle.context_authority['commit_sha']}"
        in (bundle.provenance_refs)
    )
    assert f"observed_version:{dataset.current_version.id}" in bundle.provenance_refs


def _unpinned(bundle) -> list[str]:
    """Every observed asset the bundle serves as evidence without pinning the
    version it served.

    Refs rather than a bool, so a failure names the entry that broke the
    relation instead of reporting `assert [] == [...]` over the whole list.
    """
    pinned = set(bundle.provenance_refs)
    return [
        item["ref"]
        for item in bundle.linked_evidence["observed_assets"]
        if item["observed_version_id"]
        and f"observed_version:{item['observed_version_id']}" not in pinned
    ]


@pytest.mark.postgres
def test_every_observed_version_served_as_evidence_is_pinned_in_provenance(
    session_factory, revenue_slice
):
    """ADR 0019 decision 2(c), stated over every constructor rather than over
    one dataset (hy-c4bk).

    What this is and is not. `provenance_refs` is a PROJECTION of
    `observed_assets`, built by the one `_evidence_provenance` and handed to
    the one assembly point, so nothing in the list under check can fail today
    -- handing that function a doctored entry with an invented
    `observed_version_id` pins the invention. So this does not prove a pinned
    id is real; the half of 2(c) that does is the conformance check, which
    requires each pinned id to RESOLVE to a stored version.

    It is a preservation guard on the projection staying a projection. It
    breaks the day a fourth constructor assembles `provenance_refs`
    independently, or a step after the projection appends an entry to
    `observed_assets` -- and `provenance_refs` is `list[str]`, so the type
    would not object to either. Demonstrated red by appending one entry after
    `_evidence_provenance` runs: `unpinned == ['superset:dataset:PROOF']`.

    One-way by design: `provenance_refs` may over-pin -- the governed bundle
    pins its `git_context:` snapshot and every finding -- so an id in
    `observed_assets` must appear in `provenance_refs` and never the converse.
    """
    ungoverned = _undeclared_ref(session_factory, revenue_slice)
    governed = _resolve(session_factory)
    bundles = {
        "governed": governed,
        "mixed": _resolve(session_factory, asset_refs=[APPROVED_REF, ungoverned]),
        "observed_only": _resolve(session_factory, domains=[], asset_refs=[ungoverned]),
        "no_match": _resolve(session_factory, domains=[]),
        # The one mutation that runs after the projection: it empties each
        # entry's payload and must remove no entry and no id.
        "governed under a budget": _resolve(session_factory, context_budget=1),
    }

    for status, bundle in bundles.items():
        assert _unpinned(bundle) == [], status

    # Not vacuously green: three of the five actually carried observations,
    # and `no_match` carries none by construction rather than by accident.
    assert {status for status, b in bundles.items() if b.linked_evidence["observed_assets"]} == {
        "governed",
        "mixed",
        "observed_only",
        "governed under a budget",
    }
    assert bundles["no_match"].provenance_refs == []


@pytest.mark.postgres
def test_the_same_pinned_state_resolves_the_same_bundle(session_factory, revenue_slice):
    first = _resolve(session_factory)
    second = _resolve(session_factory)

    assert first.bundle_id == second.bundle_id
    assert first.to_dict() | {"resolved_at": None} == second.to_dict() | {"resolved_at": None}


@pytest.mark.postgres
def test_a_source_change_changes_the_bundle_and_travels_as_a_conflict(
    session_factory, revenue_slice
):
    """Step 7 -> 8 -> 9: the source drifts, the processor explains it, and
    the disagreement reaches the agent with the instruction it disputes."""
    before = _resolve(session_factory)
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    bundle = _resolve(session_factory)

    assert bundle.bundle_id != before.bundle_id
    (finding,) = bundle.linked_evidence["findings"]
    assert finding["severity"] == "error"
    assert finding["ref"] == APPROVED_REF
    assert finding["context_snapshot_id"] == revenue_slice["context"].snapshot_id
    (conflict,) = bundle.linked_evidence["conflicts"]
    assert conflict["field"] == "recognized_revenue"
    assert conflict["context_says"] == GIT_EXPRESSION
    assert conflict["source_says"] == DRIFTED_EXPRESSION
    assert f"finding:{finding['finding_id']}" in bundle.provenance_refs
    # The Git instruction is still reported as Git wrote it; the bundle
    # discloses the dispute instead of quietly rewriting the meaning.
    assert bundle.instructions["fields"][0]["expression"] == GIT_EXPRESSION


@pytest.mark.postgres
def test_freshness_and_deprecation_travel_with_the_evidence(session_factory, revenue_slice):
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

    bundle = _resolve(session_factory)

    freshness = {item["ref"]: item for item in bundle.linked_evidence["freshness"]}
    assert freshness[APPROVED_REF]["deleted_at"] is not None
    assert freshness[APPROVED_REF]["last_observed_at"] is not None
    deprecations = {item["ref"]: item for item in bundle.linked_evidence["deprecations"]}
    assert deprecations[APPROVED_REF]["kind"] == "source_deleted"
    # A source Git forbids is disclosed as a deprecation as well as an
    # instruction, so an agent reading either one avoids it.
    assert deprecations[PROHIBITED_SOURCE]["kind"] == "prohibited_by_context"
    assert PROHIBITED_SOURCE in [item["ref"] for item in bundle.instructions["prohibited_sources"]]


@pytest.mark.postgres
def test_the_domain_graph_only_carries_declared_or_observed_edges(session_factory, revenue_slice):
    graph = _resolve(session_factory).domain_graph

    kinds = {node["kind"] for node in graph["nodes"]}
    assert {"domain", "owner", "concept", "source", "field", "join", "grain", "check"} <= kinds
    assert {edge["evidence"] for edge in graph["edges"]} <= {"git", "observation"}
    assert {
        "from": "field:recognized_revenue",
        "to": f"source:{APPROVED_SOURCE}",
        "relation": "reads",
        "evidence": "git",
    } in graph["edges"]
    # The only non-Git edges are exact observations.
    observed = [edge for edge in graph["edges"] if edge["evidence"] == "observation"]
    assert observed and all(edge["to"].startswith("observed_version:") for edge in observed)


@pytest.mark.postgres
def test_a_question_that_names_nothing_is_told_to_plan_rather_than_guessed_at(
    session_factory, revenue_slice
):
    """The deleted `_match` would have read this question for a configured
    domain name (GitHub #70). Word matching is not replaced by a smarter
    guess; it is replaced by asking the planner to name what it wants."""
    bundle = _resolve(
        session_factory, query="How much did we actually earn in Canada last quarter?", domains=[]
    )

    assert bundle.status == "no_match"
    assert bundle.context_authority is None
    # Nothing that could be read as approved meaning.
    assert bundle.instructions["definitions"] == []
    assert bundle.instructions["approved_sources"] == []
    assert bundle.linked_evidence["observed_assets"] == []
    assert bundle.provenance_refs == []
    assert "Nothing here is approved, canonical, or trusted" in bundle.resolution["summary"]
    # A code a planner can branch on, and a sentence that names the surface
    # replacing the guess.
    (told,) = bundle.resolution["warnings"]
    assert told["code"] == PLAN_FIRST_REQUIRED
    assert "list_context_catalog" in told["message"]


@pytest.mark.postgres
def test_a_domain_the_catalog_does_not_list_is_a_no_match_not_a_guess(
    session_factory, revenue_slice
):
    bundle = _resolve(session_factory, domains=["marketing"])

    assert bundle.status == "no_match"
    (refused,) = bundle.resolution["warnings"]
    assert refused["code"] == UNKNOWN_DOMAIN
    assert "'marketing'" in refused["message"]
    assert "configured domains: revenue" in refused["message"]


@pytest.mark.postgres
def test_a_domain_that_does_not_declare_the_named_concept_serves_nothing_governed(
    session_factory, revenue_slice
):
    """hy-9lct, measured: the governed arm resolved `revenue` for a supplier
    lead-time question and answered from it. Hyperset may not read the
    question to know that, so the caller states what coverage it needs and
    the domain's own Git declarations either carry it or do not."""
    bundle = _resolve(
        session_factory,
        query="What is our average supplier lead time by warehouse over the last quarter?",
        concepts=["supplier_lead_time"],
    )

    assert bundle.status == "no_match"
    assert bundle.context_authority is None
    assert bundle.instructions["definitions"] == []
    assert bundle.instructions["approved_sources"] == []
    assert bundle.linked_evidence["observed_assets"] == []
    assert bundle.provenance_refs == []
    (refused,) = bundle.resolution["warnings"]
    assert refused["code"] == DOMAIN_DOES_NOT_DECLARE
    assert "'supplier_lead_time'" in refused["message"]
    assert "'revenue' declares: recognized_revenue" in refused["message"]


@pytest.mark.postgres
def test_a_domain_named_without_a_coverage_claim_never_reaches_the_resolver(
    session_factory, revenue_slice
):
    """The move the run actually made: domains=['revenue'], nothing else.

    It used to resolve to a `no_match` bundle. It is now refused as a
    malformed request before retrieval (hy-bdff), so what this asserts is
    that no bundle is produced at all -- the served verdict is in
    `tests/unit/transport/test_operations.py`, where a caller can see it.
    Deleting the prompt line that forbids the substitution still reproduces a
    refusal rather than a governed answer about the wrong subject.
    """
    with pytest.raises(ValueError) as refused:
        _resolve(session_factory, concepts=[])

    assert "without saying what it must cover" in str(refused.value)


@pytest.mark.postgres
def test_requested_refs_narrow_the_evidence_and_unobserved_ones_are_disclosed(
    session_factory, revenue_slice
):
    bundle = _resolve(session_factory, asset_refs=[APPROVED_REF, "superset:dataset:not-observed"])

    assert [item["ref"] for item in bundle.linked_evidence["observed_assets"]] == [APPROVED_REF]
    # `ref_not_observed`, not merely "something was wrong with a ref": the
    # caller cannot fix this one by editing the directive, and the code is
    # what tells it so without reading the sentence.
    assert [w["code"] for w in bundle.resolution["warnings"]] == [REF_NOT_OBSERVED]
    assert "matches no observed asset" in bundle.resolution["warnings"][0]["message"]
    # Narrowing evidence never narrows what Git approved.
    assert len(bundle.instructions["approved_sources"]) == 2


@pytest.mark.postgres
def test_a_declared_ref_with_no_observation_is_uncorroborated_not_outside_the_context(
    session_factory, tmp_path
):
    """Reachable only since ADR 0017: a commit is snapshotted before its
    connectors are synced, so a ref the Git context DOES declare can have no
    observation behind it. It must not be reported as `ref_outside_context` --
    that says Git never approved it, which is exactly backwards.
    """
    observe_glossary_term(session_factory)
    # Deliberately no Superset sync: the domain's own approved dataset refs
    # are declared by the commit and observed by nobody. No Superset
    # connection exists at all, so the code is `ref_awaiting_sync` -- nothing
    # measured whether the dataset is there (hy-lcgq). What this test is about
    # is unchanged: whichever of the two it is, it is not
    # `ref_outside_context`.
    sync_revenue_context(session_factory, tmp_path)

    bundle = _resolve(session_factory, asset_refs=[APPROVED_REF])

    assert [w["code"] for w in bundle.resolution["warnings"]] == [REF_AWAITING_SYNC]
    assert APPROVED_REF in bundle.resolution["warnings"][0]["message"]
    assert bundle.linked_evidence["observed_assets"] == []
    # The Git half is unaffected: an uncorroborated ref is still approved.
    approved = bundle.instructions["approved_sources"]
    assert APPROVED_SOURCE in [item["ref"] for item in approved]
    assert APPROVED_REF in [item["bi_override"]["ref"] for item in approved]


@pytest.mark.postgres
def test_a_governed_bundle_carries_the_refs_its_commit_declared_and_nothing_observed(
    session_factory, tmp_path
):
    """hy-zhv9's core case, and the one a directive cannot ask about: no
    `asset_refs`, so every declared ref is in play.

    The bundle used to serve the three uncorroborated refs as silence -- the
    resolver read `snapshot.evidence_refs`, which ADR 0017 defines as declared
    AND observed, so a gap the snapshot records structurally could not reach
    the agent. Corroboration missing is never a `no_match` and never a reason
    to withhold the governed half; it is a disclosure that travels with it.
    """
    observe_glossary_term(session_factory)
    sync_revenue_context(session_factory, tmp_path)

    bundle = _resolve(session_factory)

    assert bundle.status == "governed"
    assert bundle.context_authority["type"] == "git"
    assert bundle.linked_evidence["observed_assets"] == []
    # `ref_awaiting_sync`: no Superset connection is configured here, so the
    # three declared dataset refs are unmeasured rather than absent (hy-lcgq).
    assert [
        (entry["ref"], entry["code"]) for entry in bundle.linked_evidence["uncorroborated"]
    ] == [(ref, REF_AWAITING_SYNC) for ref in SUPERSET_DECLARED]
    assert [w["code"] for w in bundle.resolution["warnings"]] == [REF_AWAITING_SYNC] * 3
    # The governed half is untouched: an uncorroborated ref is still approved.
    assert len(bundle.instructions["approved_sources"]) == 2
    assert "3 declared ref(s) uncorroborated" in bundle.resolution["summary"]


@pytest.mark.postgres
def test_a_ref_the_commit_declared_is_never_reported_as_outside_that_commit(
    session_factory, tmp_path
):
    """The commit is snapshotted before its connector is synced, so the ref is
    declared and uncorroborated; the connector is synced afterwards, so the
    same ref is now observable.

    The bundle answered `ref_outside_context` -- "is not part of the 'revenue'
    context at commit ..., it is returned as observed-only evidence and
    nothing about it is approved" -- which is exactly backwards about a ref
    the commit approves. It came out that way because the resolver's idea of
    what the commit declared was the RESOLVED subset.
    """
    observe_glossary_term(session_factory)
    sync_revenue_context(session_factory, tmp_path)
    sync_superset(make_superset_connection(session_factory), session_factory)

    bundle = _resolve(session_factory, asset_refs=[APPROVED_REF])

    (asset,) = bundle.linked_evidence["observed_assets"]
    assert asset["ref"] == APPROVED_REF
    assert asset["governance"] == "git_linked"
    # No commit ever pinned a version for it, and the bundle says so rather
    # than borrowing the version it happens to observe now.
    assert asset["linked_version_id"] is None
    assert asset["observed_version_id"] is not None
    # CHANGED by hy-gh-118 slice 3, deliberately: this asserted `warnings == []`,
    # which encoded "reconciliation is silent". That is the behaviour the slice
    # changes -- a gap the snapshot still records, closed by a later sync, is now
    # disclosed rather than dropped. The finding this test exists for is that the
    # code is not `ref_outside_context`, and that holds exactly as before.
    assert [w["code"] for w in bundle.resolution["warnings"]] == [REF_CORROBORATED_LATE]
    assert REF_OUTSIDE_CONTEXT not in [w["code"] for w in bundle.resolution["warnings"]]
    assert bundle.linked_evidence["uncorroborated"] == []


@pytest.mark.postgres
def test_the_coded_refusals_and_the_prose_ones_stay_in_step(session_factory, revenue_slice):
    """`reasons` is printed in a sync report and `findings` is what the
    snapshot stores and the bundle serves, and the first is a key lookup into
    the second rather than a second statement of it -- deliberately, because
    deriving a code from a sentence is the substring matching this contract
    removes. Nothing else asserts they agree, so this does: same refusals,
    same order, same words.

    `coded_reasons` used to be a third projection, `{code, message}`, for the
    bundle. It is gone: the parser now codes its own refusals as `{code, ref,
    message}` too, so the bundle merges both without reshaping either, and a
    projection that drops the ref is one the served surface cannot use."""
    resolution = ObservedEvidenceResolver(session_factory, workspace=ALL_WORKSPACES).resolve(
        [
            EvidenceRef(connector="superset", asset_type="dataset", external_id="not-observed"),
            EvidenceRef(connector="datahub", asset_type="glossary_term", external_id="missing"),
        ]
    )

    assert resolution.resolved == []
    assert [entry["message"] for entry in resolution.findings] == resolution.reasons
    assert [(entry["code"], entry["ref"]) for entry in resolution.findings] == [
        (REF_NOT_OBSERVED, "superset:dataset:not-observed"),
        (REF_NOT_OBSERVED, "datahub:glossary_term:missing"),
    ]


def _absent_ref(session_factory):
    """One ref no observation carries, resolved through the real resolver."""
    return ObservedEvidenceResolver(session_factory, workspace=ALL_WORKSPACES).resolve(
        [EvidenceRef(connector="superset", asset_type="dataset", external_id="never-observed")]
    )


def _observed_superset_rows(session_factory, connection_id):
    """The evidence half of the estate, as the code that used to decide this
    saw it: every observed asset on the connection, at HEAD."""
    return sorted(
        (asset.external_id, asset.asset_type, asset.deleted_at)
        for asset in PostgresObservedAssetRepository(session_factory).list_all(
            connection_id=connection_id, workspace=ALL_WORKSPACES
        )
    )


@pytest.mark.postgres
def test_an_absence_becomes_established_only_when_the_connector_has_read(session_factory):
    """hy-lcgq item 2, and the world where a wrong implementation reports the
    same thing as a right one.

    A connector that has not read and one that read and does not carry the
    asset are IDENTICAL in `observed_assets` -- both are the empty set. Any
    implementation deciding from observations alone must give one answer to
    both, so this moves the estate from the first world to the second WITHOUT
    touching an observation: nothing is upserted, nothing is deleted, only the
    sync run reaches a terminal status. The assertion that the observed rows
    are unchanged across it is the instrument; without it, two codes from two
    resolves prove nothing about which input decided them.
    """
    unread = build_unread_superset(session_factory)
    before = _observed_superset_rows(session_factory, unread["connection_id"])

    unmeasured = _absent_ref(session_factory)

    PostgresSyncRepository(session_factory).finish_run(unread["sync_run_id"], counters={})
    measured = _absent_ref(session_factory)

    assert _observed_superset_rows(session_factory, unread["connection_id"]) == before, (
        "the two worlds differ by the sync run's status and by nothing observed"
    )
    assert [entry["code"] for entry in unmeasured.findings] == [REF_AWAITING_SYNC]
    assert [entry["code"] for entry in measured.findings] == [REF_NOT_OBSERVED]
    # The message says which one it is rather than leaving the code to carry
    # it alone: the first is read by an operator deciding whether to wait.
    assert "not evidence that it is absent" in unmeasured.findings[0]["message"]
    assert "is not in the estate" in measured.findings[0]["message"]


@pytest.mark.postgres
def test_a_connector_that_read_before_and_is_down_now_stops_claiming_the_asset_is_absent(
    session_factory,
):
    """The other half of transient absence: connector DOWN, not never-synced.

    A connection that has read successfully before has measured the estate
    once, and the tempting rule -- "has it ever succeeded?" -- would answer
    "absent" here forever, while a failing sync is exactly when a newly
    created asset goes unseen. So the rule reads the LAST finished run, and
    this test fails under the ever-succeeded version.
    """
    unread = build_unread_superset(session_factory)
    syncs = PostgresSyncRepository(session_factory)
    syncs.finish_run(unread["sync_run_id"], counters={})

    established = _absent_ref(session_factory)

    later = syncs.begin_run(unread["connection_id"], mode="full")
    syncs.fail_run(later.id, errors=["connection refused"])
    down = _absent_ref(session_factory)

    assert [entry["code"] for entry in established.findings] == [REF_NOT_OBSERVED]
    assert [entry["code"] for entry in down.findings] == [REF_AWAITING_SYNC]
    assert "last sync failed" in down.findings[0]["message"]


@pytest.mark.postgres
def test_one_connection_that_has_not_read_unmeasures_the_whole_connector(session_factory):
    """A ref is looked up across every connection of its type, so absence is
    established only when every one of them has read. A staging Superset that
    has never synced is a place the asset could be sitting unseen, and the
    healthy production one does not measure it -- `any()` would report the
    asset absent from an estate half of which nobody looked at."""
    healthy = build_unread_superset(session_factory)
    PostgresSyncRepository(session_factory).finish_run(healthy["sync_run_id"], counters={})
    assert [entry["code"] for entry in _absent_ref(session_factory).findings] == [REF_NOT_OBSERVED]

    staging = PostgresConnectionRepository(session_factory).create_or_update(
        connector_type="superset", display_name="Superset (staging)"
    )
    PostgresSyncRepository(session_factory).begin_run(staging.id, mode="full")

    findings = _absent_ref(session_factory).findings

    assert [entry["code"] for entry in findings] == [REF_AWAITING_SYNC]
    assert staging.id in findings[0]["message"], "the message names the connection to look at"


@pytest.mark.postgres
def test_a_connector_nobody_configured_measures_nothing(session_factory):
    """The empty case, spelled out because `all(())` is vacuously true: with
    no Superset connection at all, an `all()` over zero connections would
    report the asset missing from an estate Hyperset has never been pointed
    at."""
    findings = _absent_ref(session_factory).findings

    assert [entry["code"] for entry in findings] == [REF_AWAITING_SYNC]
    assert "no connection is configured" in findings[0]["message"]


@pytest.mark.postgres
def test_a_ref_git_does_not_cover_comes_back_observed_only_inside_a_mixed_bundle(
    session_factory, revenue_slice
):
    """The directive can reach real assets the Git context never approved.
    They are served, marked, and disclosed -- never folded into the governed
    part (hy-5c2, GitHub #70)."""
    ungoverned = _undeclared_ref(session_factory, revenue_slice)

    bundle = _resolve(session_factory, asset_refs=[APPROVED_REF, ungoverned])

    assert bundle.status == "mixed"
    governance = {
        item["ref"]: item["governance"] for item in bundle.linked_evidence["observed_assets"]
    }
    assert governance == {APPROVED_REF: "git_linked", ungoverned: "observed_only"}
    # The governed half is unchanged and still carries its authority.
    assert bundle.context_authority["commit_sha"] == revenue_slice["context"].commit_sha
    assert ungoverned not in [item["ref"] for item in bundle.instructions["approved_sources"]]
    assert any(
        w["code"] == REF_OUTSIDE_CONTEXT and ungoverned in w["message"]
        for w in bundle.resolution["warnings"]
    )
    assert "observed-only" in bundle.resolution["summary"]
    # The projection states no relationship Git never declared.
    ungoverned_node = next(
        node for node in bundle.domain_graph["nodes"] if node["id"] == f"source:{ungoverned}"
    )
    assert ungoverned_node["kind"] == "observed_source"
    assert not [
        edge
        for edge in bundle.domain_graph["edges"]
        if edge["from"] == f"source:{ungoverned}" and edge["relation"] != "observed_as"
    ]


@pytest.mark.postgres
def test_refs_without_a_domain_are_observation_and_say_so(session_factory, revenue_slice):
    ungoverned = _undeclared_ref(session_factory, revenue_slice)

    bundle = _resolve(session_factory, domains=[], asset_refs=[ungoverned])

    assert bundle.status == "observed_only"
    assert bundle.context_authority is None
    assert bundle.instructions["approved_sources"] == []
    assert bundle.instructions["fields"] == []
    (observed,) = bundle.linked_evidence["observed_assets"]
    assert observed["ref"] == ungoverned
    assert observed["governance"] == "observed_only"
    # Provenance is still exact: an observation is pinned even when nothing
    # governs it.
    assert f"observed_version:{observed['observed_version_id']}" in bundle.provenance_refs
    assert "not approved, canonical, or validated business meaning" in bundle.resolution["summary"]


@pytest.mark.postgres
def test_max_hops_bounds_the_projection_and_leaves_the_instructions_whole(
    session_factory, revenue_slice
):
    whole = _resolve(session_factory)
    bounded = _resolve(session_factory, max_hops=1)

    assert len(bounded.domain_graph["nodes"]) < len(whole.domain_graph["nodes"])
    # One hop from the domain: what Git declared about it directly, and not
    # the fields that hang off a source or the versions behind them.
    assert {node["kind"] for node in bounded.domain_graph["nodes"]} == {
        "domain",
        "owner",
        "concept",
        "source",
        "join",
        "grain",
        "check",
    }
    assert bounded.instructions == whole.instructions
    assert bounded.linked_evidence == whole.linked_evidence
    # By code, not by wording: `projection_bounded` is one of the three
    # conditions this contract says a planner must branch on, and it is the
    # code the original inventory missed precisely because nothing identified
    # it except a sentence.
    assert any(w["code"] == PROJECTION_BOUNDED for w in bounded.resolution["warnings"])


@pytest.mark.postgres
def test_max_hops_without_a_domain_says_it_did_not_apply(session_factory, revenue_slice):
    """Accepted, echoed back in request.directive, and inert. Saying so is
    the whole fix: a parameter that looks honoured and is not is a contract
    lie even when nothing is hidden by it (hy-hu7)."""
    ungoverned = _undeclared_ref(session_factory, revenue_slice)

    bundle = _resolve(session_factory, domains=[], asset_refs=[ungoverned], max_hops=1)

    assert bundle.status == "observed_only"
    assert bundle.request["directive"]["max_hops"] == 1
    assert any(w["code"] == MAX_HOPS_NOT_APPLICABLE for w in bundle.resolution["warnings"])


@pytest.mark.postgres
def test_a_budget_the_governed_part_alone_exceeds_is_served_over_it_and_discloses_that(
    session_factory, revenue_slice
):
    """The forcing case (hy-09f): dropping every payload does not get this
    answer under budget, and the governed meaning is served anyway. A bundle
    that fits by dropping an instruction would be the failure this system
    exists to prevent."""
    whole = _resolve(session_factory)

    bounded = _resolve(session_factory, context_budget=1)

    assert len(bounded.to_json().encode()) > 1
    assert bounded.instructions == whole.instructions
    assert bounded.instructions["caveats"] == whole.instructions["caveats"]
    assert bounded.instructions["prohibited_sources"] == whole.instructions["prohibited_sources"]
    assert bounded.provenance_refs == whole.provenance_refs
    assert bounded.linked_evidence["findings"] == whole.linked_evidence["findings"]
    assert bounded.linked_evidence["freshness"] == whole.linked_evidence["freshness"]
    assert all(item["normalized"] == {} for item in bounded.linked_evidence["observed_assets"])
    # Both disclosures: what was dropped, and that it was not enough.
    warnings = bounded.resolution["warnings"]
    assert [w["code"] for w in warnings] == [OBSERVED_PAYLOADS_OMITTED, OVER_CONTEXT_BUDGET]


@pytest.mark.postgres
def test_a_context_budget_the_trim_can_meet_is_met(session_factory, revenue_slice):
    whole = _resolve(session_factory)
    # Between the two: too small for the payloads, big enough without them.
    # Derived from this slice rather than guessed, because in this fixture
    # the governed instructions, not the payloads, are most of the bundle.
    budget = (
        len(_resolve(session_factory, context_budget=1).to_json().encode())
        + len(whole.to_json().encode())
    ) // 2

    bounded = _resolve(session_factory, context_budget=budget)

    assert all(item["normalized"] == {} for item in bounded.linked_evidence["observed_assets"])
    assert len(bounded.to_json().encode()) <= budget
    # What makes the answer safe to act on survives any budget.
    assert bounded.instructions == whole.instructions
    assert bounded.provenance_refs == whole.provenance_refs
    assert [item["ref"] for item in bounded.linked_evidence["observed_assets"]] == [
        item["ref"] for item in whole.linked_evidence["observed_assets"]
    ]
    assert any(w["code"] == OBSERVED_PAYLOADS_OMITTED for w in bounded.resolution["warnings"])


@pytest.mark.postgres
def test_the_coverage_refusal_now_carries_ranked_candidates_from_the_real_estate(
    session_factory, revenue_slice
):
    """hy-gh-124 slice 1, on the pinned Superset 6.1.0 captures and the
    checked-in revenue commit.

    The refusal above is unchanged -- same status, same code, same empty
    governed sections -- and beside it the caller now gets the three observed
    datasets that estate actually carries, ordered by signal rather than by
    name. `finance_orders_daily` leads because the commit approves it AND it
    computes the expression the commit declares; `customer_dim` is approved
    and computes none of them; `raw_payments` is prohibited, so it sorts last
    and says why instead of disappearing.
    """
    bundle = _resolve(
        session_factory,
        query="How much did customer churn cost us last quarter?",
        concepts=["churn"],
    )

    assert bundle.status == "no_match"
    assert bundle.provenance_refs == []
    section = bundle.assist
    assert section["kind"] == CANDIDATE_SOURCES
    assert section["answers"] == {"domain": "revenue", "undeclared_concepts": ["churn"]}
    assert [candidate["ref"] for candidate in section["candidates"]] == SUPERSET_DECLARED_BY_RANK
    assert section["considered"] == 3

    leader, dimension, prohibited = section["candidates"]
    assert leader["ref"] == APPROVED_REF
    assert _signal(leader, EXPRESSION_AGREEMENT)["value"] == {"fields": ["recognized_revenue"]}
    assert "as 'primary'" in _signal(leader, GIT_ENGAGEMENT)["statement"]
    assert _signal(dimension, EXPRESSION_AGREEMENT)["value"] == {"fields": []}
    assert [entry["kind"] for entry in prohibited["disagrees_with_git"]] == [PROHIBITED_BY_CONTEXT]


@pytest.mark.postgres
def test_a_candidate_rank_moves_with_the_observed_expression_and_not_with_the_name(
    session_factory, tmp_path
):
    """The drift capture renames nothing and changes one expression. If the
    ranking were reading names it would not move; it reads what the source
    computes, so the leader loses its agreement signal and falls behind the
    other approved source on the tie-break below it.
    """
    connection_id = make_superset_connection(session_factory)
    observe_glossary_term(session_factory)
    sync_superset(connection_id, session_factory)
    sync_revenue_context(session_factory, tmp_path)

    before = _resolve(session_factory, concepts=["churn"]).assist
    sync_superset(connection_id, session_factory, capture="drift")
    after = _resolve(session_factory, concepts=["churn"]).assist

    assert _signal(before["candidates"][0], EXPRESSION_AGREEMENT)["value"] == {
        "fields": ["recognized_revenue"]
    }
    drifted = next(entry for entry in after["candidates"] if entry["ref"] == APPROVED_REF)
    assert _signal(drifted, EXPRESSION_AGREEMENT)["value"] == {"fields": []}
    assert before["assist_id"] != after["assist_id"]


@pytest.mark.postgres
def test_an_unknown_domain_grows_a_candidate_list_that_can_never_carry_a_proposal(
    session_factory, revenue_slice
):
    """hy-xq55 at the wire, and the arm that replaced `unknown.assist is None`.

    The caller names a domain nothing configured declares, so Git says nothing
    WHATSOEVER rather than nothing about one term -- maximal governance silence,
    and ADR 0019's premise is that silence where governance is silent is the
    defect. It gets the list.

    What it can never get is a proposal. Every candidate here is a ref the
    REVENUE commit engages, so the ranking has real Git-relative signal to work
    with and the old `no_git_relative_signal` decline would have been false --
    which is why the outcome is its own value. An approval written against
    `revenue` is a governed fact about `revenue`, not a reason to put its source
    forward for a `marketing` claim nothing governs.
    """
    unknown = _resolve(session_factory, domains=["marketing"], concepts=["campaigns"])

    assert unknown.assist is not None
    served = [candidate["ref"] for candidate in unknown.assist["candidates"]]
    assert served == SUPERSET_DECLARED_BY_RANK
    assert unknown.assist["proposal"]["outcome"] == NO_GOVERNING_DOMAIN
    assert unknown.assist["proposal"]["ref"] is None
    # The signal the superseded decline would have denied, present and populated
    # on the wire -- the measurement that made a fifth outcome necessary.
    leader = unknown.assist["candidates"][0]
    engagement = next(entry for entry in leader["signals"] if entry["signal"] == GIT_ENGAGEMENT)
    assert engagement["value"]["approved_by"]
    # Still governed silence: a list is not an answer.
    assert unknown.resolution["status"] == "no_match"


@pytest.mark.postgres
def test_the_refusals_that_are_requests_to_fix_grow_nothing(session_factory, revenue_slice):
    """The scope boundary, and it is not about what is computable (hy-xq55).

    A directive naming no domain at all is not a refusal and has no question to
    rank for. A directive naming a configured domain AND an unknown one is
    refused by the `unknown_domain` branch and stays silent because a candidate
    list would have no single subject to answer for. (Naming several KNOWN domains
    no longer refuses at all since slice 3, hy-cnto -- it resolves each; the
    retired `multiple_domains` is no longer among these fix-the-request refusals,
    so the case that still refuses here is the unknown one.)

    AND THE SILENCE MUST NOT READ AS A FRUITLESS SEARCH, which is the mayor's
    condition on this scope call. "Nothing was looked for, because the question
    has no single subject" and "something was looked for and nothing was found"
    are opposite facts that arrive at a consumer as the same absent key, so the
    one sentence the response does carry has to be the first. It is checked in
    both directions: the refusal states the corpus fact and names what IS
    configured -- something a caller can act on -- and no empty-result language
    appears anywhere in the served payload. The estate here is NOT empty
    (`test_an_unknown_domain_grows...` above serves the same fixture's sources as
    a full list), so an empty-search sentence would be false as well as
    misleading.
    """
    empty = resolve_analytics_context(
        query=QUESTION, directive=ContextDirective(), session_factory=session_factory
    )
    mixed = _resolve(
        session_factory,
        domains=["revenue", "marketing"],
        concepts=["recognized_revenue"],
    )

    assert empty.assist is None
    assert mixed.assist is None
    assert "assist" not in mixed.to_dict()

    refusal = next(
        entry for entry in mixed.resolution["warnings"] if entry["code"] == UNKNOWN_DOMAIN
    )
    assert "'marketing'" in refusal["message"]
    assert "no configured context declares" in refusal["message"]
    assert "configured domains: revenue" in refusal["message"]
    served = json.dumps(mixed.to_dict()).lower()
    for empty_search in (
        "no candidate",
        "none found",
        "no matching",
        "no results",
        "came up empty",
    ):
        assert empty_search not in served, empty_search


@pytest.mark.postgres
def test_several_unknown_domains_grow_nothing_because_none_of_them_is_the_subject(
    session_factory, revenue_slice
):
    """hy-8ivx: the all-unknown MULTI-domain directive, which nothing held.

    The arm above states only the MIXED case -- a configured domain and an
    unknown one -- and critic's mutation sweep at #211's landing tree showed the
    all-unknown multi case was pinned by nothing: dropping `and len(wanted) == 1`
    from `_select` gave 985 passed / 5 failed on tests/unit + tests/integration
    with all five failures identical to the unmutated control (rsync-copy
    artifacts), and the only multi-domain directive in the tree names a
    configured domain, so the first conjunct catches it.

    What is pinned here is the BEHAVIOUR and not that expression: a directive
    naming two or more domains, all of them unknown, gets no candidate list.
    That is hy-xq55 ground 1 arriving through argument position -- the assist
    section is a claim ABOUT a named domain, and with several unknown subjects
    `wanted[0]` picks one by INPUT ORDER and presents a list as being about it.
    Rounding up to a subject nobody named is the thing that ruling forbids, so
    the conjunct is that boundary rather than a hedge, and rewriting it is free
    as long as this stays true.

    THE CONTROL IS IN THE TEST because silence has two causes and they read the
    same at the wire. `single` is the same estate, the same fixture and one of
    the same two names, and it DOES grow a list -- so the silence below is
    plurality of subject, not an empty corpus, and this cannot pass by assist
    being broken outright.
    """
    single = _resolve(session_factory, domains=["marketing"], concepts=["campaigns"])
    several = _resolve(session_factory, domains=["marketing", "logistics"], concepts=["campaigns"])

    assert single.assist is not None, "the control: one unknown subject is answerable"
    assert several.assist is None
    assert "assist" not in several.to_dict()

    # And the refusal still names every subject, so nothing is silently dropped.
    refusal = next(
        entry for entry in several.resolution["warnings"] if entry["code"] == UNKNOWN_DOMAIN
    )
    assert "'marketing'" in refusal["message"]
    assert "'logistics'" in refusal["message"]
    assert several.resolution["status"] == "no_match"


@pytest.mark.postgres
def test_the_governed_answer_is_byte_for_byte_what_it_was_without_assist(
    session_factory, revenue_slice
):
    """ADR 0019 decision 2(b), as the property rather than as a promise: a
    caller reading the governed sections of ANY answer gets exactly what it
    would have got with assist switched off.

    A guard test, and it cannot go red against the pre-change code -- there
    was no assist output to leak. It was shown red against the plausible wrong
    change instead: appending a candidate to `linked_evidence.observed_assets`
    as `git_linked`, applied in process in a scratch worktree. That mutation is
    not in this file, and the critic reproduced it from this docstring and
    confirmed the guard reds under it -- so read this as the recipe, not as a
    pointer to code below.
    """
    governed = _resolve(session_factory)

    assert governed.assist is None
    assert set(governed.to_dict()) == set(_resolve(session_factory).to_dict())
    assert all(
        asset["governance"] == GIT_LINKED for asset in governed.linked_evidence["observed_assets"]
    )
    # The wrong change this guard exists to catch: a candidate laundered into
    # the citation surface the evaluator derives `source_refs` from.
    leaked = _resolve(session_factory, query="churn", concepts=["churn"])
    assert leaked.linked_evidence["observed_assets"] == []
    assert leaked.assist is not None
    for candidate in leaked.assist["candidates"]:
        assert candidate["ref"] not in leaked.provenance_refs
        assert f"observed_version:{candidate['observed_version_id']}" not in leaked.provenance_refs


@pytest.mark.postgres
def test_a_caller_that_declines_assist_gets_the_governed_answer_alone(
    session_factory, revenue_slice
):
    """ADR 0019 decision 1's refusal half, served (hy-c9mb).

    "A caller may opt out and receive the governed answer alone, including the
    silence." The same coverage refusal that carries three ranked candidates
    above carries none when the directive declines, and everything else about
    the answer is unchanged -- the point is that declining removes assist and
    nothing else, so a conformance run can compare the two.

    Compared by full payload rather than by the keys that looked relevant: a
    subset comparison would pass on a change that also moved `status`, and the
    claim is that the governed answer is untouched.
    """
    served = _resolve(session_factory, query=CHURN_QUESTION, concepts=["churn"])
    declined = _resolve(session_factory, query=CHURN_QUESTION, concepts=["churn"], assist=False)

    assert served.assist is not None, "the fixture is the refusal that has candidates"
    assert declined.assist is None
    assert "assist" not in declined.to_dict(), (
        "declined assist is an ABSENT section, not an empty one -- an empty section "
        "would read as assist having run and found nothing"
    )
    assert declined.status == served.status
    served_payload, declined_payload = served.to_dict(), declined.to_dict()
    served_payload.pop("assist")
    for payload in (served_payload, declined_payload):
        payload.pop("resolved_at")
    # `bundle_id` covers the REQUEST as well as the answer, and the request
    # genuinely differs -- so it moves, and it should. What ADR 0019 promises
    # byte-for-byte is the governed SECTIONS, not the identity of the answer to
    # a request nobody made. Asserted rather than excused, because the opposite
    # claim would be the easy thing to write here.
    assert declined_payload.pop("bundle_id") != served_payload.pop("bundle_id")
    # The whole directive, not one key (hy-4pjo). Popping `request` and checking
    # `assist` says a field moved; it does not say NOTHING ELSE moved, and a
    # second field riding along with the opt-out would have satisfied it.
    declined_directive = declined_payload.pop("request")["directive"]
    served_directive = served_payload.pop("request")["directive"]
    assert set(declined_directive) - set(served_directive) == {"assist"}
    assert declined_directive["assist"] is False
    assert {
        key: value for key, value in declined_directive.items() if key != "assist"
    } == served_directive, "the request differs in exactly the opt-out"
    assert declined_payload == served_payload, (
        "the governed answer, whole, is what the opt-out leaves behind"
    )


@pytest.mark.postgres
def test_asking_for_assist_cannot_produce_it_where_governance_speaks(
    session_factory, revenue_slice
):
    """The demand half stays unserved, checked rather than asserted (hy-c9mb).

    `assist=True` is the default, so the risk in adding the field is that it
    reads as a request. It is not one: on a request governance ANSWERS, asking
    for assist produces the same bundle as omitting it -- there is no state
    where a caller can conjure a section into an answer governance covers.
    """
    governed = _resolve(session_factory)
    demanded = _resolve(session_factory, assist=True)

    assert governed.assist is None
    assert demanded.assist is None
    governed_payload, demanded_payload = governed.to_dict(), demanded.to_dict()
    # Two resolves of one request differ in when they ran and nothing else, so
    # this is the whole payload minus the clock rather than a chosen subset.
    for payload in (governed_payload, demanded_payload):
        payload.pop("resolved_at")
    assert demanded_payload == governed_payload
    assert demanded.bundle_id == governed.bundle_id, (
        "asking for assist is not a different request, so it is not a different answer"
    )


def _git_authored_bytes(session_factory, snapshot_id: str) -> str:
    """The snapshot's own Git-authored half, canonically, as the INDEPENDENT
    instrument for hy-gh-118 slice 3.

    Comparing a reconciled bundle against a bundle recompiled from the same
    inputs cannot come out differently -- both are the same function of the same
    rows -- so the bundle is not evidence about whether the snapshot moved. The
    snapshot's own bytes are, and they are read back from the repository rather
    than held from before, so a rewrite in place is what this catches.

    `evidence_findings` is included deliberately: a reconciliation that
    "cleared" the gap by deleting the recorded finding would leave every other
    field identical, and that is precisely the world this must be able to tell
    apart.
    """
    record = PostgresContextRepository(session_factory).get_snapshot(snapshot_id)
    return json.dumps(
        {
            "commit_sha": record.commit_sha,
            "content_hash": record.content_hash,
            "normalized": record.normalized,
            "files": record.files,
            "owner_refs": record.owner_refs,
            "evidence_refs": record.evidence_refs,
            "evidence_findings": record.evidence_findings,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@pytest.mark.postgres
def test_late_arriving_evidence_reconciles_without_re_authoring_the_snapshot(
    session_factory, tmp_path
):
    """hy-gh-118 slice 3, the acceptance item: real estates sync out of order.

    Git first, evidence second -- the commit is read before any Superset sync
    exists, so the declared ref is snapshotted as `ref_awaiting_sync`: no
    Superset connection has read, so nothing measured whether the dataset is
    there (hy-lcgq). The connector then syncs, and the bundle must corroborate
    the ref WITHOUT the immutable snapshot being touched and without any
    re-approval.

    Git is the authority and connected systems are evidence (ADR 0017), so a
    reconciliation that re-authored the snapshot would put the authority
    inversion back, just later.
    """
    context = build_git_before_evidence(session_factory, tmp_path)["context"]
    before = _git_authored_bytes(session_factory, context.snapshot_id)

    assert REF_AWAITING_SYNC in [gap["code"] for gap in json.loads(before)["evidence_findings"]], (
        "the fixture is the out-of-order one: the commit is read before its evidence exists"
    )
    uncorroborated = _resolve(session_factory, asset_refs=[APPROVED_REF])
    assert [w["code"] for w in uncorroborated.resolution["warnings"]] == [REF_AWAITING_SYNC]

    # The evidence arrives, second.
    corroborate_superset(session_factory)
    reconciled = _resolve(session_factory, asset_refs=[APPROVED_REF])

    assert _git_authored_bytes(session_factory, context.snapshot_id) == before, (
        "late-arriving evidence must not re-author the Git-authored half of the snapshot"
    )
    linked = {
        asset["ref"]: asset["governance"] for asset in reconciled.linked_evidence["observed_assets"]
    }
    assert linked[APPROVED_REF] == GIT_LINKED, "the ref corroborates once the evidence lands"
    assert REF_AWAITING_SYNC not in [w["code"] for w in reconciled.resolution["warnings"]], (
        "and it is no longer served as uncorroborated"
    )


@pytest.mark.postgres
def test_the_reconciled_bundle_discloses_the_gap_it_closed_rather_than_dropping_it(
    session_factory, tmp_path
):
    """The half a recompiled-bundle comparison cannot see (hy-7ejr).

    Name the world where a bad reconciliation reports the same thing as a good
    one: a reconciliation that re-authored the snapshot to DELETE the finding
    would serve exactly the bundle a correct one serves -- same `git_linked`
    entry, same absence of `ref_not_observed`. The two are distinguishable only
    by the snapshot's own bytes, which the test above pins, and by whether the
    bundle says a gap closed, which is this one.

    So the disclosure GAINS a line rather than replacing one: the snapshot still
    records the gap, and the bundle states that a connector has since observed
    it. Dropping it silently would leave a caller unable to tell a reconciled
    bundle from one whose snapshot never had the gap, while the snapshot it
    names still lists it -- two records disagreeing with nothing saying which is
    current.
    """
    context = build_git_before_evidence(session_factory, tmp_path)["context"]
    corroborate_superset(session_factory)

    bundle = _resolve(session_factory, asset_refs=[APPROVED_REF])

    disclosed = [w for w in bundle.resolution["warnings"] if w["code"] == REF_CORROBORATED_LATE]
    assert len(disclosed) == 1, [w["code"] for w in bundle.resolution["warnings"]]
    assert APPROVED_REF in disclosed[0]["message"]
    assert "is not re-authored" in disclosed[0]["message"]
    # The snapshot still says what was true when the commit was read. Both
    # records stand, and the bundle is what reconciles them.
    recorded = PostgresContextRepository(session_factory).get_snapshot(context.snapshot_id)
    assert APPROVED_REF in [gap["ref"] for gap in recorded.evidence_findings]


def _signal(candidate: dict, name: str) -> dict:
    return next(entry for entry in candidate["signals"] if entry["signal"] == name)


_USAGE_FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "superset" / "6.1.0" / "usage"
)
_USAGE_MANIFEST = json.loads((_USAGE_FIXTURE / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.postgres
def test_the_reference_count_a_candidate_states_is_the_one_the_capture_declares(
    session_factory, tmp_path
):
    """hy-g1y8, end to end on the pinned 6.1.0 dashboard export.

    `tests/unit/bundle/test_discovery.py` proves the ranking moves with the
    count; this proves the count reaching it is the estate's own. Nothing is
    asserted from a literal: both the per-dataset counts and the chart uuids
    come from the capture's manifest, whose own reference claims are checked
    against the captured bytes by
    `tests/integration/test_usage_reference_evidence.py`.

    The export is synced INSTEAD of the REST captures rather than beside them.
    An export discloses only what the exported dashboard reaches, so syncing it
    into an estate the REST captures had already filled would soft-delete the
    dataset the manifest names as uncovered -- a real behaviour, and a
    `source_deleted` disagreement that would sink the candidate this test is
    about for a reason that has nothing to do with references.
    """
    from hyperset.connectors import run_sync
    from hyperset.connectors.superset import SupersetConnector

    expected = _USAGE_MANIFEST["expected_observed_references"]
    charts_by_dataset: dict[str, list[str]] = {}
    for chart_uuid, dataset_uuid in expected["chart_queries_dataset"].items():
        charts_by_dataset.setdefault(dataset_uuid, []).append(chart_uuid)

    connection_id = make_superset_connection(session_factory)
    observe_glossary_term(session_factory)
    run_sync(
        connector=SupersetConnector(bundle_path=str(_USAGE_FIXTURE / "official-export.zip")),
        connection_id=connection_id,
        session_factory=session_factory,
    )
    sync_revenue_context(session_factory, tmp_path)

    section = _resolve(session_factory, concepts=["churn"]).assist

    served = {candidate["ref"]: candidate for candidate in section["candidates"]}
    uncovered = set(expected["uncovered_by_this_capture"]["dataset_uuids"])
    for dataset_uuid, count in expected["dataset_reference_counts"].items():
        ref = f"superset:dataset:{dataset_uuid}"
        if dataset_uuid in uncovered:
            # Absent from the export, so absent from the estate this transport
            # built -- not a candidate with a count of zero.
            assert ref not in served
            continue
        counted = _signal(served[ref], DECLARED_REFERENCES)
        assert counted["value"]["count"] == count
        assert counted["value"]["by_relation"] == {"queries": count}
        assert [entry["ref"] for entry in counted["value"]["referenced_by"]] == sorted(
            f"superset:chart:{chart_uuid}" for chart_uuid in charts_by_dataset[dataset_uuid]
        )
        # Every ref named is an observed chart the same sync recorded, which is
        # what keeps the statement checkable rather than decorative.
        for entry in counted["value"]["referenced_by"]:
            assert entry["ref"] in counted["statement"]

    # What this estate can and cannot show, stated rather than implied. The two
    # survivors differ on approval AND on prohibition, so their order here is
    # settled before the count is read: asserting that the most-referenced one
    # leads would be a comparison that cannot come out differently, and it would
    # read as evidence for a claim this fixture does not make. The count doing
    # work is proved in tests/unit/bundle/test_discovery.py, where the other
    # three signals are held equal and only the count moves.
    leader, sunk = section["candidates"]
    assert _signal(leader, GIT_ENGAGEMENT)["value"]["approved_by"] == [
        {"domain": "revenue", "role": "primary"}
    ]
    assert [entry["kind"] for entry in sunk["disagrees_with_git"]] == [PROHIBITED_BY_CONTEXT]
    assert _signal(sunk, DECLARED_REFERENCES)["value"]["count"] > 0, (
        "the sunk candidate is referenced, so its position is the prohibition's doing "
        "and not an absence of references"
    )


@pytest.mark.postgres
def test_a_prohibited_source_the_estate_still_references_travels_as_a_conflict(
    session_factory, tmp_path
):
    """hy-llk4 end to end, on evidence recorded for another question.

    The capture this runs against was pinned for hy-g1y8's reference counting.
    It already contained a live chart querying the dataset the checked-in
    revenue commit prohibits -- a real disagreement, in a real 6.1.0 export,
    that nothing reported, because no rule had been written for this shape. That
    is what makes it an instrument rather than a demonstration: the fixture was
    not built to be found by this code, and the counts below are read from the
    capture's own manifest rather than written down here.

    What it does NOT show, stated so the green does not carry more than it can:
    the engine finds this without a bespoke PROCESSOR rule, which is the part
    ADR 0021 decision 4 left open. It is still a dimension with its own pairing
    function in `hyperset/bundle/reconcile.py`, so decision 1's "a contradiction
    is a JOIN, not a rule table" is not demonstrated by this test.
    """
    from hyperset.connectors import run_sync
    from hyperset.connectors.superset import SupersetConnector

    expected = _USAGE_MANIFEST["expected_observed_references"]
    prohibited_uuid = "6f4976c2-25ea-5d98-b714-9ca8e6c9b7e4"
    referring = sorted(
        f"superset:chart:{chart_uuid}"
        for chart_uuid, dataset_uuid in expected["chart_queries_dataset"].items()
        if dataset_uuid == prohibited_uuid
    )
    assert referring, "the capture no longer references the prohibited dataset"

    connection_id = make_superset_connection(session_factory)
    observe_glossary_term(session_factory)
    run_sync(
        connector=SupersetConnector(bundle_path=str(_USAGE_FIXTURE / "official-export.zip")),
        connection_id=connection_id,
        session_factory=session_factory,
    )
    sync_revenue_context(session_factory, tmp_path)

    bundle = _resolve(session_factory)

    (entry,) = [
        item
        for item in bundle.linked_evidence["conflicts"]
        if item["kind"] == PROHIBITED_BUT_REFERENCED
    ]
    assert entry["ref"] == PROHIBITED_SOURCE
    assert entry["produced_by"] == BUNDLE_RECONCILIATION
    # No finding stands behind it, and the entry says so rather than pointing at
    # a review task that does not exist. This is the value SCHEMA_VERSION moved
    # for.
    assert entry["finding_id"] is None
    assert entry["field"] is None
    reason = next(
        item["reason"]
        for item in bundle.instructions["prohibited_sources"]
        if item["ref"] == PROHIBITED_SOURCE
    )
    assert entry["context_says"] == f"prohibited: {reason}"
    assert entry["source_says"] == (
        f"{len(referring)} live reference"
        f"{'' if len(referring) == 1 else 's'}: {', '.join(referring)}"
    )
    assert entry["unresolved_since_commit"] == bundle.context_authority["commit_sha"]
    # Both records stand. The prohibition is still a deprecation, because it is
    # true about the source whether or not anything points at it.
    deprecations = {item["ref"]: item for item in bundle.linked_evidence["deprecations"]}
    assert deprecations[PROHIBITED_SOURCE]["kind"] == "prohibited_by_context"


@pytest.mark.postgres
def test_a_prohibited_source_nothing_points_at_is_reported_as_no_disagreement(
    session_factory, revenue_slice
):
    """The silence arm, on the estate the rest of this file resolves against.

    The REST captures record datasets and databases and no chart, so the same
    prohibition that produced a conflict above produces none here -- and that is
    the prohibition working rather than a gap. Without this arm the test above
    cannot distinguish a join that found something from a rule that reports every
    prohibited source it is given.
    """
    prohibited_ref = PROHIBITED_SOURCE

    bundle = _resolve(session_factory)

    assert prohibited_ref in [item["ref"] for item in bundle.instructions["prohibited_sources"]]
    deprecations = {item["ref"]: item for item in bundle.linked_evidence["deprecations"]}
    assert deprecations[prohibited_ref]["kind"] == "prohibited_by_context"
    assert [
        item for item in bundle.linked_evidence["conflicts"] if item["ref"] == prohibited_ref
    ] == []


@pytest.mark.postgres
def test_a_governed_source_the_connector_stopped_reporting_travels_as_a_conflict(
    session_factory, revenue_slice
):
    """The second reconciled dimension, beside the deprecation it does not
    replace (hy-llk4).

    `test_freshness_and_deprecation_travel_with_the_evidence` asserts the
    one-sided disclosure this pairs with: a `source_deleted` deprecation whose
    reason mentions in prose that Git still approves the ref. A reader who wants
    the pair had to parse that sentence, and `deprecations` carries no Git side
    to check it against.
    """
    gone = PostgresSyncRepository(session_factory).begin_run(
        revenue_slice["connection_id"], mode="full"
    )
    PostgresObservedAssetRepository(session_factory).mark_missing_deleted(
        connection_id=revenue_slice["connection_id"],
        asset_type="dataset",
        seen_external_ids=set(),
        sync_run_id=gone.id,
    )

    bundle = _resolve(session_factory)

    entries = {
        item["ref"]: item
        for item in bundle.linked_evidence["conflicts"]
        if item["kind"] == SOURCE_DELETED_WHILE_GOVERNED
    }
    entry = entries[APPROVED_REF]
    assert entry["produced_by"] == BUNDLE_RECONCILIATION
    assert entry["finding_id"] is None
    assert entry["context_says"] == "approved as a source of this domain"
    assert entry["source_says"].startswith("the dataset stopped being reported at ")
    assert entry["unresolved_since_commit"] == bundle.context_authority["commit_sha"]
    # The deprecation stays: the deletion is an observation, and it is true
    # whether or not a commit approves the ref.
    deprecations = {item["ref"]: item for item in bundle.linked_evidence["deprecations"]}
    assert deprecations[APPROVED_REF]["kind"] == "source_deleted"
    # Only refs the commit declares. Every observed_only ref this estate deleted
    # in the same sweep is an observation with no Git side, and none is served
    # as a disagreement.
    declared = {
        ref
        for item in bundle.instructions["approved_sources"]
        for ref in (item["ref"], item.get("bi_override", {}).get("ref"))
        if ref is not None
    }
    assert set(entries) <= declared


@pytest.mark.postgres
def test_the_unwired_reconciliation_kinds_never_reach_a_resolved_bundle(
    session_factory, revenue_slice
):
    """Bind the feature-parity audit's 'landed but inert' claim to the resolve path.

    `ownership_mismatch`, `grain_mismatch` and `freshness_stale` are implemented in
    `bundle/reconcile.py` but NO resolve-path call reaches them (deferred activation,
    hy-z3wy / hy-yjkv / hy-kh9k). The audit row states this. This asserts it
    BEHAVIORALLY, not by a grep: force the resolve path to actually produce a
    reconciliation conflict (delete a governed source), then assert that every
    conflict kind that reaches the bundle is one of the two WIRED kinds and none of
    the three inert kinds appears. If one gets wired, this reddens and the audit row
    (and its activation bead) must move that kind from inert to wired (hy-w8q2).
    """
    from hyperset.bundle.reconcile import (
        FRESHNESS_STALE,
        GRAIN_MISMATCH,
        OWNERSHIP_MISMATCH,
        PROHIBITED_BUT_REFERENCED,
    )

    gone = PostgresSyncRepository(session_factory).begin_run(
        revenue_slice["connection_id"], mode="full"
    )
    PostgresObservedAssetRepository(session_factory).mark_missing_deleted(
        connection_id=revenue_slice["connection_id"],
        asset_type="dataset",
        seen_external_ids=set(),
        sync_run_id=gone.id,
    )

    bundle = _resolve(session_factory)

    kinds = {item["kind"] for item in bundle.linked_evidence["conflicts"]}
    # The resolve path ran and produced a WIRED reconciliation conflict.
    assert SOURCE_DELETED_WHILE_GOVERNED in kinds, "the resolve path produced no wired conflict"
    # Only the wired kinds (plus any projected processor finding) ever appear.
    assert kinds <= {SOURCE_DELETED_WHILE_GOVERNED, PROHIBITED_BUT_REFERENCED} | {
        item["kind"]
        for item in bundle.linked_evidence["conflicts"]
        if item["produced_by"] != BUNDLE_RECONCILIATION
    }
    for inert in (OWNERSHIP_MISMATCH, GRAIN_MISMATCH, FRESHNESS_STALE):
        assert inert not in kinds, (
            f"{inert} reached a resolved bundle -- it is WIRED now; update the "
            "feature-parity audit's reconciliation row and its activation bead"
        )


@pytest.mark.postgres
def test_a_persisted_processor_finding_reaches_the_bundle_as_a_projected_conflict(
    session_factory, revenue_slice
):
    """Bind the feature-parity audit's THIRD reconciliation claim: a persisted
    processor finding whose Git and observed sides disagree on an expression is
    PROJECTED into `linked_evidence.conflicts` through `resolver._conflict` ->
    `reconcile()` (the generic-dispatch path, distinct from the two bundle-time
    producers).

    Seeds the finding for real (drift the source, run the processor), then asserts
    the projected conflict reaches the resolved bundle carrying the projection's
    identity: `produced_by == processor_finding` and the finding's own id. Gutting
    `emitted = reconcile(...)` in `_conflict` (so it returns None) turns this RED --
    mutation-verified -- so the audit's 'projected via the generic reconcile()'
    claim cannot rot to a false green (hy-w8q2).
    """
    from hyperset.bundle.reconcile import PROCESSOR_FINDING

    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    bundle = _resolve(session_factory)

    (finding,) = bundle.linked_evidence["findings"]
    projected = [
        item
        for item in bundle.linked_evidence["conflicts"]
        if item["produced_by"] == PROCESSOR_FINDING
    ]
    assert len(projected) == 1, "the persisted finding did not project a conflict into the bundle"
    conflict = projected[0]
    # Bound to its owner: the projected conflict carries the finding's own id, not
    # a sibling's (self-verifying-id must bind to its owner).
    assert conflict["finding_id"] == finding["finding_id"]
    assert conflict["ref"] == APPROVED_REF
    assert conflict["context_says"] == GIT_EXPRESSION
    assert conflict["source_says"] == DRIFTED_EXPRESSION
