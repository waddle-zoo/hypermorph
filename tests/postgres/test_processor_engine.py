"""Walking-skeleton step 8 against real evidence (hy-gh-38).

Every input is the real thing: the pinned Superset 6.1.0 REST payloads under
`tests/fixtures/superset/6.1.0/revenue/{baseline,drift}` served through
`tests.fake_superset`, a Git repository built with the `git` CLI from the
checked-in revenue context, and the Postgres schema the migrations produce.
The controlled drift is the one the fixtures already carry -- the approved
dataset's `recognized_revenue` metric changing from
`SUM(gross_amount - tax_amount)` to `SUM(gross_amount)` -- so the finding is
about a source change that really happened, not one staged for the test.

The scenario fixtures live in `conftest.py`: the processor and the
`ContextBundle` are proven against the same slice, not two similar ones.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, text

from hyperset.processor import RULE_ID, run_sync_processing
from hyperset.processor.rules import FINDING_TYPES, UNDECIDABLE_ID
from hyperset.repositories.postgres import (
    PostgresConnectorChangeRepository,
    PostgresObservedAssetRepository,
    PostgresProcessorRepository,
    PostgresReviewRepository,
    PostgresSyncRepository,
)
from tests.postgres.conftest import (
    APPROVED_DATASET,
    DRIFTED_EXPRESSION,
    GIT_EXPRESSION,
    build_revenue_slice,
    sync_superset,
)


@pytest.mark.postgres
def test_agreement_between_git_and_the_sources_produces_no_finding(session_factory, revenue_slice):
    result = run_sync_processing(
        sync_run_id=revenue_slice["baseline_sync_run_id"], session_factory=session_factory
    )

    assert result.status == "succeeded"
    assert result.findings == []
    assert result.counters["context_snapshots"] == 1
    assert result.counters["fields_evaluated"] == 2
    assert result.counters["findings_created"] == 0


@pytest.mark.postgres
def test_one_real_source_change_becomes_one_explainable_finding(session_factory, revenue_slice):
    """Step 7 into step 8: the drift capture changes the approved dataset's
    metric, the connector records it, and the rule explains the
    disagreement with exact provenance on both sides."""
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    changes = PostgresConnectorChangeRepository(session_factory).list_for_run(drift.sync_run_id)
    assert [change.change_type for change in changes] == ["updated"]

    result = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    assert result.counters["findings_created"] == 1
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.finding_type == RULE_ID
    assert finding.severity == "error"
    assert finding.state == "current"
    assert GIT_EXPRESSION in finding.explanation
    assert DRIFTED_EXPRESSION in finding.explanation
    assert finding.proposed_reviewer == "team:finance-data"

    # Provenance is exact: the Git commit on one side, the observed version
    # and the connector event on the other.
    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=revenue_slice["connection_id"],
        external_id=APPROVED_DATASET,
        asset_type="dataset",
    )
    assert finding.affected_asset_id == dataset.id
    assert finding.affected_context_snapshot_id == revenue_slice["context"].snapshot_id
    assert finding.evidence["git"]["commit_sha"] == revenue_slice["context"].commit_sha
    assert finding.evidence["git"]["expression"] == GIT_EXPRESSION
    assert finding.evidence["observed"]["expression"] == DRIFTED_EXPRESSION
    assert finding.evidence["observed"]["observed_version_id"] == dataset.current_version.id
    assert [c["id"] for c in finding.evidence["connector_changes"]] == [changes[0].id]

    run = PostgresProcessorRepository(session_factory).get_run(result.processor_run_id)
    assert run.status == "succeeded"
    assert run.trigger_type == "sync"
    assert run.trigger_ref == drift.sync_run_id
    # The register the run was judged under, and it moved with the judgement:
    # comparing computations instead of characters changes the output for the
    # same inputs, which is exactly what `rule_version` exists to record. Old
    # findings keep the version they were found under (hy-803q, ADR 0021).
    assert run.rule_versions == {RULE_ID: 2, UNDECIDABLE_ID: 2}
    assert set(run.rule_versions) == set(FINDING_TYPES), (
        "the run records every type it could have produced, so a reader can tell "
        "a quiet pass from a pass under a narrower vocabulary"
    )


@pytest.mark.postgres
def test_no_finding_creates_governed_context_or_a_decision(session_factory, revenue_slice):
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")

    run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    with session_factory() as session:
        for table in ("governed_context", "governed_context_versions", "review_decisions"):
            count = session.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
            assert count == 0, f"processing wrote {table}"


@pytest.mark.postgres
def test_reprocessing_the_same_sync_adds_nothing(session_factory, revenue_slice):
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    first = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    second = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    assert second.counters["findings_created"] == 0
    assert second.counters["findings_deduplicated"] == 1
    assert second.findings[0].id == first.findings[0].id
    processor = PostgresProcessorRepository(session_factory)
    assert len(processor.list_findings(finding_type=RULE_ID, state="current")) == 1
    # Two runs, one question for the human.
    assert second.processor_run_id != first.processor_run_id


@pytest.mark.postgres
def test_a_real_finding_opens_one_idempotent_human_review_task(session_factory, revenue_slice):
    """hy-1jgw6 (real #38): a real disagreement does not just record a Finding,
    it opens the human ReviewTask that `list_review_tasks` serves -- one task per
    finding, keyed on the same (rule, asset, commit) subject, so a rerun asks the
    human once and `propose_review_to_git` has a queue to act on."""
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")

    first = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)
    assert len(first.findings) == 1
    finding = first.findings[0]

    reviews = PostgresReviewRepository(session_factory)
    tasks = reviews.list_tasks(status="open")
    assert len(tasks) == 1, "one real disagreement opens exactly one review task"
    task = tasks[0]

    # The finding carries the task it opened, and the task carries the finding's
    # subject, evidence, and proposal at the severity-derived priority.
    assert finding.review_task_id == task.id
    assert task.reason == finding.explanation
    assert task.priority == 1, "an `error` contradiction is top-of-queue"
    assert task.affected_asset_ids == [finding.affected_asset_id]
    assert task.proposal_payload["finding_type"] == RULE_ID
    assert task.processor_evidence == finding.evidence
    assert task.status == "open"

    # Idempotent: re-running the same sync opens no second task and the
    # (deduplicated) finding still points at the one task.
    second = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)
    assert second.findings[0].review_task_id == task.id
    assert [t.id for t in reviews.list_tasks(status="open")] == [task.id]


@pytest.mark.postgres
def test_agreement_between_git_and_the_sources_opens_no_review_task(session_factory, revenue_slice):
    """The task is opened only for a real finding: a clean sync leaves the human
    review queue empty, so `list_review_tasks` never surfaces a non-question."""
    run_sync_processing(
        sync_run_id=revenue_slice["baseline_sync_run_id"], session_factory=session_factory
    )

    assert PostgresReviewRepository(session_factory).list_tasks() == []


@pytest.mark.postgres
def test_more_than_one_change_per_asset_in_one_run_is_still_one_finding(
    session_factory, revenue_slice
):
    """`upsert` is a public repository call that may run twice under one
    `sync_run_id`, so dedup cannot assume one change per asset per run."""
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=revenue_slice["connection_id"],
        external_id=APPROVED_DATASET,
        asset_type="dataset",
    )
    # A second, differently-worded payload for the same asset in the same run.
    payload = dict(dataset.current_version.raw_payload)
    payload["description"] = "re-upserted under the same sync run"
    assets.upsert(
        connection_id=revenue_slice["connection_id"],
        external_id=APPROVED_DATASET,
        asset_type="dataset",
        sync_run_id=drift.sync_run_id,
        raw_payload=payload,
        normalized=dataset.current_version.normalized,
    )
    changes = PostgresConnectorChangeRepository(session_factory).list_for_run(drift.sync_run_id)
    assert len(changes) == 2

    result = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    assert result.counters["findings_created"] == 1
    assert len(result.findings) == 1
    # Both events travel with the finding as evidence.
    assert len(result.findings[0].evidence["connector_changes"]) == 2


@pytest.mark.postgres
def test_a_reappeared_asset_is_judged_live_not_deleted(session_factory, revenue_slice):
    """A soft-deleted asset that comes back with unchanged content writes no
    new version, so its `restored` change carries no `to_version_id`
    (hy-y8g.1). The rule reads `observed_assets.deleted_at` and the current
    version, so the reappearance is judged on its content."""
    sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=revenue_slice["connection_id"],
        external_id=APPROVED_DATASET,
        asset_type="dataset",
    )
    syncs = PostgresSyncRepository(session_factory)
    gone = syncs.begin_run(revenue_slice["connection_id"], mode="full")
    assets.mark_missing_deleted(
        connection_id=revenue_slice["connection_id"],
        asset_type="dataset",
        seen_external_ids=set(),
        sync_run_id=gone.id,
    )

    # While it is gone, the drift rule stays quiet: a deleted approved source
    # is a different question, and answering this one would mislead.
    while_deleted = run_sync_processing(sync_run_id=gone.id, session_factory=session_factory)
    assert while_deleted.findings == []

    back = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    changes = PostgresConnectorChangeRepository(session_factory)
    restored = [
        change
        for change in changes.list_for_run(back.sync_run_id)
        if change.change_type == "restored"
    ]
    assert [change.to_version_id for change in restored] == [None] * len(restored)
    assert any(change.asset_id == dataset.id for change in restored)

    result = run_sync_processing(sync_run_id=back.sync_run_id, session_factory=session_factory)

    assert result.counters["findings_created"] == 1
    assert result.findings[0].affected_asset_id == dataset.id


@pytest.mark.postgres
def test_the_finding_closes_when_the_disagreement_stops_reproducing(session_factory, revenue_slice):
    """Either side can end it -- here the source goes back to what Git
    approves. Hyperset closes the finding because the rule found nothing,
    never because it decided the context was right."""
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    opened = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    reverted = sync_superset(revenue_slice["connection_id"], session_factory, "baseline")
    result = run_sync_processing(sync_run_id=reverted.sync_run_id, session_factory=session_factory)

    assert result.counters["findings_resolved"] == 1
    processor = PostgresProcessorRepository(session_factory)
    assert processor.list_findings(finding_type=RULE_ID, state="current") == []
    (closed,) = [
        finding
        for finding in processor.list_findings(state="resolved")
        if finding.id == opened.findings[0].id
    ]
    # The history stays readable: what was asked, and against which commit.
    assert closed.evidence["git"]["commit_sha"] == revenue_slice["context"].commit_sha


@pytest.mark.postgres
def test_a_second_worker_cannot_process_the_same_sync_at_once(session_factory, revenue_slice):
    processor = PostgresProcessorRepository(session_factory)
    sync_run_id = revenue_slice["baseline_sync_run_id"]
    processor.claim_run(trigger_type="sync", trigger_ref=sync_run_id, rule_versions={RULE_ID: 1})

    result = run_sync_processing(sync_run_id=sync_run_id, session_factory=session_factory)

    assert result.status == "already_running"
    assert result.processor_run_id is None


@pytest.mark.postgres
def test_two_sync_runs_processed_at_once_still_ask_one_question(
    committed_session_factory, db_engine, tmp_path
):
    """`claim_run` only serializes one sync run against itself, so two sync
    runs really can be processed at the same moment and both evaluate the
    same asset under the same commit (hy-nnq).

    Both passes are held at the `findings` insert until the other arrives, so
    the interleaving is the racy one every time rather than when the machine
    happens to be slow. Before the fix the loser raised IntegrityError out of
    `run_sync_processing`; now it re-reads the winner.
    """
    slice_ = build_revenue_slice(committed_session_factory, tmp_path)
    first = sync_superset(slice_["connection_id"], committed_session_factory, "drift")
    # The second sync observes the same drifted content, so it writes no new
    # version -- but its run still evaluates the context, which is what puts
    # two passes on the same subject.
    second = sync_superset(slice_["connection_id"], committed_session_factory, "drift")

    at_the_insert = threading.Barrier(2, timeout=30)

    def hold_both_at_the_finding_insert(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("INSERT INTO FINDINGS"):
            at_the_insert.wait()

    event.listen(db_engine, "before_cursor_execute", hold_both_at_the_finding_insert)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            passes = [
                pool.submit(
                    run_sync_processing,
                    sync_run_id=sync_run_id,
                    session_factory=committed_session_factory,
                )
                for sync_run_id in (first.sync_run_id, second.sync_run_id)
            ]
            results = [pass_.result(timeout=60) for pass_ in passes]
    finally:
        event.remove(db_engine, "before_cursor_execute", hold_both_at_the_finding_insert)

    assert [result.status for result in results] == ["succeeded", "succeeded"]
    # One insert wins, the other reads the winner back: one question, asked
    # once, whichever pass got there first.
    assert sum(result.counters["findings_created"] for result in results) == 1
    assert sum(result.counters["findings_deduplicated"] for result in results) == 1
    assert len({finding.id for result in results for finding in result.findings}) == 1

    processor = PostgresProcessorRepository(committed_session_factory)
    assert len(processor.list_findings(finding_type=RULE_ID, state="current")) == 1
    runs = [processor.get_run(result.processor_run_id) for result in results]
    assert [run.status for run in runs] == ["succeeded", "succeeded"]
    assert [run.errors for run in runs] == [[], []]


def _reword_the_approved_expression(session_factory, connection_id, expression: str):
    """Republish the approved dataset with one metric expression rewritten.

    The pinned captures carry a baseline and one real drift, and neither can
    produce a qualifier-only difference -- so the third comparison outcome has to
    be staged. Everything else about the asset stays byte-identical, which is
    what makes the outcome attributable to the expression alone.
    """
    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=APPROVED_DATASET, asset_type="dataset"
    )
    normalized = {
        **dataset.current_version.normalized,
        "metrics": [
            {**metric, "expression": expression}
            if metric["name"] == "recognized_revenue"
            else metric
            for metric in dataset.current_version.normalized["metrics"]
        ],
    }
    payload = dict(dataset.current_version.raw_payload)
    payload["description"] = f"republished for {expression}"
    syncs = PostgresSyncRepository(session_factory)
    run = syncs.begin_run(connection_id, mode="full")
    assets.upsert(
        connection_id=connection_id,
        external_id=APPROVED_DATASET,
        asset_type="dataset",
        sync_run_id=run.id,
        raw_payload=payload,
        normalized=normalized,
    )
    return run.id, dataset.id


@pytest.mark.postgres
def test_a_reformatted_expression_produces_no_finding_end_to_end(session_factory, revenue_slice):
    """hy-803q on the real path, not in isolation.

    The approved dataset republishes the SAME computation, differently typed. The
    processor used to compare characters, so this produced an `error` finding, a
    `linked_evidence.conflicts` entry, and a sunk candidate in discovery. It is
    the case the pinned captures cannot stage -- which is why the whole suite
    passed unchanged when the comparison was fixed.
    """
    sync_run_id, _ = _reword_the_approved_expression(
        session_factory, revenue_slice["connection_id"], "sum( gross_amount-tax_amount )"
    )

    result = run_sync_processing(sync_run_id=sync_run_id, session_factory=session_factory)

    assert result.findings == []
    assert result.counters["findings_created"] == 0


@pytest.mark.postgres
def test_a_qualifier_only_difference_is_a_warning_not_an_error(session_factory, revenue_slice):
    """The third outcome, persisted: its own type, `warning`, both forms in the
    explanation, and the comparator's verdict on the evidence."""
    sync_run_id, dataset_id = _reword_the_approved_expression(
        session_factory, revenue_slice["connection_id"], "SUM(o.gross_amount - o.tax_amount)"
    )

    result = run_sync_processing(sync_run_id=sync_run_id, session_factory=session_factory)

    assert [(f.finding_type, f.severity) for f in result.findings] == [(UNDECIDABLE_ID, "warning")]
    assert result.findings[0].affected_asset_id == dataset_id
    assert result.findings[0].evidence["comparison"] == "undecided"


@pytest.mark.postgres
def test_the_finding_says_which_side_moved_against_the_version_the_commit_linked(
    session_factory, revenue_slice
):
    """ADR 0021 decision 3 on the real path (hy-qfyn).

    The controlled drift is the source moving, and the evidence says so with the
    version the context sync pinned for the ref and what that version computed --
    the third expression, without which a reader cannot check the claim. Nothing
    here is staged: the link point is the baseline capture, the current version
    is the drift capture, and the commit is the same one throughout.
    """
    assets = PostgresObservedAssetRepository(session_factory)
    baseline = assets.get_by_external_id(
        connection_id=revenue_slice["connection_id"],
        external_id=APPROVED_DATASET,
        asset_type="dataset",
    ).current_version
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")

    result = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)

    moved = result.findings[0].evidence["moved"]
    assert moved["side"] == "observed"
    assert moved["linked_version_id"] == baseline.id
    assert moved["expression_at_link"] == GIT_EXPRESSION
    assert moved["basis"] in result.findings[0].explanation
    # The link point is a real earlier version, not the one being reported on.
    assert (
        moved["linked_version_id"] != result.findings[0].evidence["observed"]["observed_version_id"]
    )


@pytest.mark.postgres
def test_a_disagreement_that_changes_shape_does_not_leave_both_findings_current(
    session_factory, revenue_slice
):
    """The defect the third outcome would otherwise have introduced.

    Dedup is keyed on (finding type, asset, snapshot), so a real drift becoming a
    qualifier-only difference writes a second row under a second type. If
    settling were keyed on the asset alone -- as it was -- the old `error` would
    stay `current` beside the new `warning`, and a reader would see one asset
    both contradicting Git and undecided against it at once.
    """
    drift = sync_superset(revenue_slice["connection_id"], session_factory, "drift")
    first = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)
    assert [f.finding_type for f in first.findings] == [RULE_ID]

    sync_run_id, dataset_id = _reword_the_approved_expression(
        session_factory, revenue_slice["connection_id"], "SUM(o.gross_amount - o.tax_amount)"
    )
    second = run_sync_processing(sync_run_id=sync_run_id, session_factory=session_factory)

    assert [f.finding_type for f in second.findings] == [UNDECIDABLE_ID]
    assert second.counters["findings_resolved"] == 1
    current = PostgresProcessorRepository(session_factory).list_findings(state="current")
    assert [(f.finding_type, f.severity) for f in current if f.affected_asset_id == dataset_id] == [
        (UNDECIDABLE_ID, "warning")
    ]
