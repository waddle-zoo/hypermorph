"""The demo's producer end, deterministically and hermetically (hy-y1ng8).

`make up-demo` has to show an evaluator the REAL processor producing a real
`Finding` and a populated human review queue -- not a seeded row (a prior mayor
ruling forbids direct-seeding, because a manufactured row misrepresents the
product). This proves that exact sequence in Postgres, with no Docker and no
hosted keys, so the demo's claim is verified before the Makefile wires it:

  1. observe a checked-in Superset export bundle whose `finance_orders_daily`
     metric MATCHES the playground revenue manifest's approved expression;
  2. sync the playground revenue Git context AFTER that observation, so the
     manifest's `bi_override` for the approved dataset corroborates against the
     observed version -- the evidence ref the drift rule reads (a context sync
     that runs BEFORE the observation records no such ref, and the processor
     then finds nothing, which is the trap this ordering avoids);
  3. RE-observe the drifted bundle on the SAME connection, so the approved
     dataset's metric moves to `SUM(gross_amount)` -- one real source change;
  4. run the REAL processor over that drift sync and see one explainable
     `approved_expression_drift` finding whose `moved.side` is `observed` (the
     source left the link point Git still approves), plus the idempotent human
     `ReviewTask` it opens, visible through the SERVED `list_review_tasks`.

The two bundles are the SAME fixture but for the one metric expression
(`tests/fixtures/superset/6.1.0/usage/official-export{,-drift}.zip`), so the
drift is a single controlled edit rather than two unrelated estates.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from hyperset.connectors import run_sync
from hyperset.connectors.superset import SupersetConnector
from hyperset.processor import RULE_ID, run_sync_processing
from hyperset.repositories.postgres import (
    PostgresConnectorChangeRepository,
    PostgresContextRepository,
    PostgresObservedAssetRepository,
    PostgresReviewRepository,
)
from hyperset.transport.operations import run_operation

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYGROUND_REVENUE = REPO_ROOT / "playground" / "examples" / "revenue"
USAGE_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "superset" / "6.1.0" / "usage"
BASELINE_BUNDLE = USAGE_FIXTURES / "official-export.zip"
DRIFT_BUNDLE = USAGE_FIXTURES / "official-export-drift.zip"

APPROVED_DATASET = "ae48881d-334f-54a7-94e8-1ffcc73866e2"
GIT_EXPRESSION = "SUM(gross_amount - tax_amount)"
DRIFTED_EXPRESSION = "SUM(gross_amount)"
CONTEXT_PATH = "domains/revenue"


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


def _playground_revenue_repository(root: Path) -> Path:
    """A real Git commit of the ACTUAL playground revenue context the demo
    snapshots -- not a test fixture that only resembles it -- so this proves the
    manifest `make up-demo` ships."""
    repository = root / "playground-repo"
    (repository / CONTEXT_PATH).mkdir(parents=True)
    _git("init", "--quiet", "--initial-branch=main", ".", cwd=repository)
    _git("config", "user.email", "context@example.test", cwd=repository)
    _git("config", "user.name", "Context Owner", cwd=repository)
    for path in sorted(PLAYGROUND_REVENUE.iterdir()):
        shutil.copy(path, repository / CONTEXT_PATH / path.name)
    _git("add", "-A", cwd=repository)
    _git("commit", "--quiet", "-m", "add playground revenue context", cwd=repository)
    return repository


def _make_bundle_connection(session_factory):
    from hyperset.repositories.postgres import PostgresConnectionRepository

    return (
        PostgresConnectionRepository(session_factory)
        .create_or_update(
            connector_type="superset",
            display_name="Playground: observed (Superset bundle)",
            config_ref=str(BASELINE_BUNDLE),
        )
        .id
    )


def _observe(connection_id, bundle: Path, session_factory):
    return run_sync(
        connector=SupersetConnector(bundle_path=str(bundle)),
        connection_id=connection_id,
        session_factory=session_factory,
    )


def _sync_playground_revenue_context(session_factory, tmp_path):
    from hyperset.context.sync import sync_git_context

    repository = _playground_revenue_repository(tmp_path)
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repository), ref="main", path=CONTEXT_PATH
    )
    result = sync_git_context(
        source_id=source.id, session_factory=session_factory, cache_dir=tmp_path / "cache"
    )
    assert result.status == "synced", result.reasons
    return result


@pytest.mark.postgres
def test_up_demo_produces_a_real_finding_and_a_review_task(session_factory, tmp_path):
    connection_id = _make_bundle_connection(session_factory)

    # 1-2. Observe the approved dataset at the expression Git approves, THEN sync
    # the context, so the manifest's bi_override corroborates -- the evidence ref
    # the drift rule reads. Doing this in the other order records no ref and the
    # processor would find nothing (the sync-ordering trap this demo avoids).
    _observe(connection_id, BASELINE_BUNDLE, session_factory)
    context = _sync_playground_revenue_context(session_factory, tmp_path)

    assets = PostgresObservedAssetRepository(session_factory)
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=APPROVED_DATASET, asset_type="dataset"
    )
    linked_version = dataset.current_version.id
    assert dataset.current_version.normalized["metrics"][0]["expression"] == GIT_EXPRESSION

    # 3. One real source change: the same estate re-exported with the metric
    # drifted. On the SAME connection, so it is an UPDATE of the corroborated
    # asset, not a second asset the context never linked.
    drift = _observe(connection_id, DRIFT_BUNDLE, session_factory)
    changes = PostgresConnectorChangeRepository(session_factory).list_for_run(drift.sync_run_id)
    assert [c.change_type for c in changes] == ["updated"]
    dataset = assets.get_by_external_id(
        connection_id=connection_id, external_id=APPROVED_DATASET, asset_type="dataset"
    )
    assert dataset.current_version.id != linked_version
    assert dataset.current_version.normalized["metrics"][0]["expression"] == DRIFTED_EXPRESSION

    # 4. The REAL processor over the drift sync -> one explainable finding.
    result = run_sync_processing(sync_run_id=drift.sync_run_id, session_factory=session_factory)
    assert result.status == "succeeded"
    assert result.counters["findings_created"] == 1
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.finding_type == RULE_ID
    assert finding.severity == "error"
    assert finding.state == "current"
    assert finding.affected_asset_id == dataset.id
    assert finding.affected_context_snapshot_id == context.snapshot_id
    assert finding.evidence["field"] == "recognized_revenue"
    assert finding.evidence["git"]["expression"] == GIT_EXPRESSION
    assert finding.evidence["observed"]["expression"] == DRIFTED_EXPRESSION
    # The source left the link point Git still approves -- the canonical drift
    # narrative, not "Git moved": Git matches the version this commit linked, and
    # the source has changed since.
    assert finding.evidence["moved"]["side"] == "observed"
    assert finding.evidence["moved"]["expression_at_link"] == GIT_EXPRESSION
    assert finding.proposed_reviewer == "team:finance-data"

    # ...and the human review queue is populated by that same processor run,
    # visible through the SERVED read the review UI and MCP clients use.
    tasks = PostgresReviewRepository(session_factory).list_tasks()
    assert len(tasks) == 1
    assert tasks[0].affected_asset_ids == [dataset.id]
    assert tasks[0].idempotency_key == f"processor:{RULE_ID}:{dataset.id}:{context.snapshot_id}"

    served = run_operation("list_review_tasks", {}, session_factory=session_factory)
    assert [t["id"] for t in served["tasks"]] == [tasks[0].id]
    assert served["tasks"][0]["reason"] == finding.explanation


@pytest.mark.postgres
def test_no_finding_without_the_drift(session_factory, tmp_path):
    """The control: the SAME sequence with the baseline bundle re-observed
    instead of the drift one produces no finding and no task -- so the finding
    above is the source change, not the wiring."""
    connection_id = _make_bundle_connection(session_factory)
    _observe(connection_id, BASELINE_BUNDLE, session_factory)
    _sync_playground_revenue_context(session_factory, tmp_path)
    steady = _observe(connection_id, BASELINE_BUNDLE, session_factory)

    result = run_sync_processing(sync_run_id=steady.sync_run_id, session_factory=session_factory)

    assert result.counters["findings_created"] == 0
    assert result.findings == []
    assert PostgresReviewRepository(session_factory).list_tasks() == []
