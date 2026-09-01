import json
import subprocess
import zipfile

import pytest
import yaml
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from hyperset.cli import main
from hyperset.db.engine import make_engine
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresContextRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
)
from tests.integration.test_git_context_source import CONTEXT_PATH, make_repository
from tests.postgres.test_context_sync import (
    DATAHUB_GLOSSARY_TERM,
    SUPERSET_DATASETS,
    _seed_observed_evidence,
)


def _write_bundle_zip(tmp_path) -> str:
    src = tmp_path / "bundle"
    (src / "datasets" / "warehouse" / "public").mkdir(parents=True)
    (src / "metadata.yaml").write_text(yaml.safe_dump({"version": "1.0.0", "type": "Slice"}))
    (src / "datasets" / "warehouse" / "public" / "orders.yaml").write_text(
        yaml.safe_dump({"table_name": "orders", "uuid": "dataset-cli-1", "database_uuid": "db-1"})
    )
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in src.rglob("*.yaml"):
            archive.write(file_path, arcname=file_path.relative_to(src))
    return str(zip_path)


@pytest.fixture(autouse=True)
def _database_url_env(monkeypatch, postgres_dsn, db_engine):
    # CLI commands read $HYPERSET_DATABASE_URL directly (not the
    # session_factory fixture's savepoint-wrapped connection), so these
    # writes are real COMMITS against the shared session-scoped container
    # -- matching how a human actually runs the CLI twice in a row against
    # the same database. That means they must be cleaned up explicitly
    # (below), unlike every other tests/postgres/ file's writes, which the
    # session_factory fixture rolls back automatically.
    monkeypatch.setenv("HYPERSET_DATABASE_URL", postgres_dsn)
    yield
    with db_engine.connect() as connection:
        # `connections`/`context_sources` cascade to the observed estate and the
        # findings that reference it, so those are covered. `process sync` also
        # commits `processor_runs` and the human `review_tasks` the processor now
        # opens (hy-1jgw6), and neither has an FK back to `connections`, so a
        # cascade never reaches them -- a leaked task breaks the exact-count
        # assertions in test_interactive_review. Truncate them explicitly.
        connection.execute(
            text("TRUNCATE connections, context_sources, processor_runs, review_tasks CASCADE")
        )
        connection.commit()


@pytest.mark.postgres
def test_connections_create_and_list(capsys, tmp_path):
    zip_path = _write_bundle_zip(tmp_path)
    exit_code = main(["connections", "create-superset-bundle", "--path", zip_path])
    assert exit_code == 0
    connection_id = capsys.readouterr().out.strip()
    assert connection_id.startswith("conn-")

    main(["connections", "list"])
    listed = capsys.readouterr().out
    assert connection_id in listed
    assert "superset" in listed


@pytest.mark.postgres
def test_sync_run_uses_stored_bundle_path(capsys, tmp_path):
    zip_path = _write_bundle_zip(tmp_path)
    main(["connections", "create-superset-bundle", "--path", zip_path])
    connection_id = capsys.readouterr().out.strip()

    exit_code = main(["sync", "run", connection_id])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "sync_run_id=" in output
    assert "'created': 1" in output


@pytest.mark.postgres
def test_sync_run_missing_connection_id_fails_clearly(capsys):
    exit_code = main(["sync", "run", "conn-does-not-exist"])
    assert exit_code != 0


def _commit_observed_evidence(postgres_dsn) -> None:
    """Observed assets the checked-in revenue context refers to, committed
    for real because the CLI opens its own connection from
    $HYPERSET_DATABASE_URL.

    A function as well as a fixture because one test connects the sources
    part-way through, which is what an operator does after reading a finding."""
    engine = make_engine(postgres_dsn)
    try:
        _seed_observed_evidence(sessionmaker(bind=engine, expire_on_commit=False))
    finally:
        engine.dispose()


def _current_snapshot(postgres_dsn):
    """The snapshot the CLI just wrote, read back over its own connection: the
    command commits for real, so the shared per-test transaction cannot see
    it."""
    engine = make_engine(postgres_dsn)
    try:
        repository = PostgresContextRepository(sessionmaker(bind=engine, expire_on_commit=False))
        (source,) = repository.list_sources()
        return source.current_snapshot
    finally:
        engine.dispose()


@pytest.fixture
def committed_evidence(postgres_dsn):
    _commit_observed_evidence(postgres_dsn)


@pytest.mark.postgres
def test_context_add_sync_status_and_show(capsys, tmp_path, monkeypatch, committed_evidence):
    monkeypatch.setenv("HYPERSET_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    repository = make_repository(tmp_path)

    assert main(["context", "add", "--repository", str(repository), "--path", CONTEXT_PATH]) == 0
    source_id = capsys.readouterr().out.strip()
    assert source_id.startswith("ctxsrc-")

    assert main(["context", "sync", source_id]) == 0
    assert "status=synced" in capsys.readouterr().out

    # Health: which repo/ref/path/commit is current, and how the last read went.
    assert main(["context", "status"]) == 0
    status = capsys.readouterr().out
    assert f"{repository}@main:{CONTEXT_PATH}" in status
    assert "last_attempt=synced" in status

    assert main(["context", "show", source_id]) == 0
    shown = capsys.readouterr().out
    assert '"domain": "revenue"' in shown
    assert "team:finance-data" in shown

    # Inspecting the original Git content, not a Hyperset rendering of it.
    assert main(["context", "show", source_id, "--files"]) == 0
    assert "--- manifest.yaml ---" in capsys.readouterr().out

    assert main(["context", "sync", source_id]) == 0
    assert "status=unchanged" in capsys.readouterr().out


@pytest.mark.postgres
def test_context_sync_failure_reports_the_context_that_still_serves(
    capsys, tmp_path, monkeypatch, committed_evidence
):
    monkeypatch.setenv("HYPERSET_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    repository = make_repository(tmp_path)
    main(["context", "add", "--repository", str(repository), "--path", CONTEXT_PATH])
    source_id = capsys.readouterr().out.strip()
    main(["context", "sync", source_id])
    good_commit = capsys.readouterr().out.split("commit=")[1].split()[0]

    manifest = repository / CONTEXT_PATH / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace("schema_version: 1", "schema_version: 9"))
    subprocess.run(
        ["git", "commit", "--quiet", "-am", "break the schema"], cwd=repository, check=True
    )

    assert main(["context", "sync", source_id]) == 1
    captured = capsys.readouterr()
    assert "status=failed" in captured.out
    assert "schema_version must be 1" in captured.err
    assert f"current commit remains {good_commit}" in captured.err


@pytest.mark.postgres
def test_context_sync_reports_an_uncorroborated_commit_as_synced_not_rejected(
    capsys, tmp_path, monkeypatch
):
    """No connector has been synced, so nothing the commit declares can be
    corroborated. The operator gets the commit, exit 0, and the gaps named
    one code at a time -- not a rejection (ADR 0017).

    The code is `ref_awaiting_sync` because of the first sentence: an operator
    who has connected nothing yet is being told what is missing from Hyperset,
    not what is missing from their estate (hy-lcgq)."""
    monkeypatch.setenv("HYPERSET_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    repository = make_repository(tmp_path)
    main(["context", "add", "--repository", str(repository), "--path", CONTEXT_PATH])
    source_id = capsys.readouterr().out.strip()

    assert main(["context", "sync", source_id]) == 0
    captured = capsys.readouterr()
    assert "status=synced" in captured.out
    assert "rejected:" not in captured.err
    assert captured.err.count("uncorroborated [ref_awaiting_sync]") == 3

    assert main(["context", "show", source_id]) == 0
    shown = capsys.readouterr().out
    assert '"code": "ref_awaiting_sync"' in shown
    assert "  evidence=[]" in shown


@pytest.mark.postgres
def test_context_sync_keeps_reporting_the_gaps_after_the_operator_acts_on_them(
    capsys, tmp_path, monkeypatch, postgres_dsn
):
    """hy-f462: the second run is where an operator looks.

    The finding says "sync the superset connection to corroborate it". Doing
    that and re-running must not produce a silent `status=unchanged` -- the
    snapshot still has zero resolved refs, and an absent warning would be
    indistinguishable from the gap having closed."""
    monkeypatch.setenv("HYPERSET_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    repository = make_repository(tmp_path)
    main(["context", "add", "--repository", str(repository), "--path", CONTEXT_PATH])
    source_id = capsys.readouterr().out.strip()
    main(["context", "sync", source_id])
    capsys.readouterr()

    _commit_observed_evidence(postgres_dsn)

    assert main(["context", "sync", source_id]) == 0
    captured = capsys.readouterr()
    assert "status=unchanged" in captured.out
    assert captured.err.count("uncorroborated [ref_awaiting_sync]") == 3
    # hy-5lgg: this run did not compute those findings, and their sentences --
    # "matches no observed asset", "sync the superset connection to
    # corroborate it" -- are now false and already-done respectively. The
    # operator is told which moment they describe, so a replayed line reads as
    # a record rather than as a claim about the run that printed it.
    synced_at = _current_snapshot(postgres_dsn).synced_at
    assert captured.err.count(f"as of {synced_at.isoformat()}") == 3


@pytest.mark.postgres
def test_context_status_without_a_source_says_so(capsys):
    assert main(["context", "status"]) == 0
    assert "no Git context source configured" in capsys.readouterr().out


def _write_revenue_bundle_zip(tmp_path, *, expression: str) -> str:
    """A Superset export carrying the same dataset UUIDs the checked-in
    revenue context approves, so the CLI path links real declared refs. The
    approved dataset's metric expression is the drift knob."""
    src = tmp_path / "revenue-bundle"
    datasets = src / "datasets" / "warehouse" / "public"
    datasets.mkdir(parents=True)
    (src / "metadata.yaml").write_text(yaml.safe_dump({"version": "1.0.0", "type": "Slice"}))
    for name, uuid, metrics in (
        ("finance_orders_daily", SUPERSET_DATASETS[0], [("recognized_revenue", expression)]),
        ("customer_dim", SUPERSET_DATASETS[1], []),
        ("raw_payments", SUPERSET_DATASETS[2], []),
    ):
        (datasets / f"{name}.yaml").write_text(
            yaml.safe_dump(
                {
                    "table_name": name,
                    "uuid": uuid,
                    "database_uuid": "db-revenue",
                    "metrics": [
                        {"metric_name": metric, "expression": expr} for metric, expr in metrics
                    ],
                }
            )
        )
    zip_path = tmp_path / "revenue-bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for file_path in src.rglob("*.yaml"):
            archive.write(file_path, arcname=file_path.relative_to(src))
    return str(zip_path)


@pytest.fixture
def committed_glossary_term(postgres_dsn):
    """Only the DataHub identity the revenue context refers to. Its Superset
    refs are left to the sync under test -- observing them twice, on two
    connections, would make every ref ambiguous."""
    engine = make_engine(postgres_dsn)
    try:
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        datahub = PostgresConnectionRepository(session_factory).create_or_update(
            connector_type="datahub", display_name="DataHub"
        )
        run = PostgresSyncRepository(session_factory).begin_run(datahub.id, mode="full")
        PostgresObservedAssetRepository(session_factory).upsert(
            connection_id=datahub.id,
            external_id=DATAHUB_GLOSSARY_TERM,
            asset_type="glossary_term",
            sync_run_id=run.id,
            raw_payload={"urn": DATAHUB_GLOSSARY_TERM},
        )
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_process_sync_reports_one_finding_as_an_unapplied_git_proposal(
    capsys, tmp_path, monkeypatch, committed_glossary_term
):
    """The operator path for walking-skeleton step 8: sync, pin the Git
    context, process, read the finding."""
    monkeypatch.setenv("HYPERSET_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    zip_path = _write_revenue_bundle_zip(tmp_path, expression="SUM(gross_amount)")
    main(["connections", "create-superset-bundle", "--path", zip_path])
    connection_id = capsys.readouterr().out.strip()
    main(["sync", "run", connection_id])
    sync_run_id = capsys.readouterr().out.split("sync_run_id=")[1].split()[0]

    repository = make_repository(tmp_path)
    main(["context", "add", "--repository", str(repository), "--path", CONTEXT_PATH])
    source_id = capsys.readouterr().out.strip()
    assert main(["context", "sync", source_id]) == 0
    capsys.readouterr()

    assert main(["process", "sync", sync_run_id]) == 0
    output = capsys.readouterr().out

    assert "processor_run_id=pr-" in output
    assert "'findings_created': 1" in output
    assert "error approved_expression_drift" in output
    assert "SUM(gross_amount - tax_amount)" in output  # what Git approves
    assert "SUM(gross_amount)" in output  # what the source now computes
    # The proposal is disclosed as machine-generated and not applied.
    assert "proposal (not applied)" in output
    assert '"applied_by_hyperset": false' in output
    assert '"requires_human_git_commit": true' in output

    # Rerunning asks the same human the same question zero more times.
    assert main(["process", "sync", sync_run_id]) == 0
    assert "'findings_created': 0" in capsys.readouterr().out


@pytest.mark.postgres
def test_bundle_resolve_serves_the_governed_revenue_bundle(
    capsys, tmp_path, monkeypatch, committed_glossary_term
):
    """The operator adapter over the shared resolver (walking-skeleton step
    9). The HTTP and MCP adapters serialize this same object."""
    monkeypatch.setenv("HYPERSET_CONTEXT_CACHE_DIR", str(tmp_path / "cache"))
    zip_path = _write_revenue_bundle_zip(tmp_path, expression="SUM(gross_amount - tax_amount)")
    main(["connections", "create-superset-bundle", "--path", zip_path])
    connection_id = capsys.readouterr().out.strip()
    main(["sync", "run", connection_id])
    capsys.readouterr()

    repository = make_repository(tmp_path)
    main(["context", "add", "--repository", str(repository), "--path", CONTEXT_PATH])
    source_id = capsys.readouterr().out.strip()
    assert main(["context", "sync", source_id]) == 0
    commit = capsys.readouterr().out.split("commit=")[1].split()[0]

    assert main(["bundle", "catalog"]) == 0
    catalog = json.loads(capsys.readouterr().out)
    # The operator sees what a planner would choose from, and chooses from it.
    assert [entry["domain"] for entry in catalog["domains"]] == ["revenue"]

    assert (
        main(
            [
                "bundle",
                "resolve",
                "--query",
                "recognized revenue by region",
                "--domain",
                catalog["domains"][0]["domain"],
                "--concept",
                catalog["domains"][0]["concepts"][0],
            ]
        )
        == 0
    )
    bundle = json.loads(capsys.readouterr().out)

    assert bundle["resolution"]["status"] == "governed"
    assert bundle["context_authority"]["commit_sha"] == commit
    assert bundle["context_authority"]["path"] == CONTEXT_PATH
    assert bundle["execution"] == {
        "performed_by_hyperset": False,
        "result_validated_by_hyperset": False,
    }
    assert bundle["bundle_id"].startswith("cb-")
    assert (
        f"git_context:{bundle['context_authority']['context_snapshot_id']}@{commit}"
        in (bundle["provenance_refs"])
    )


@pytest.mark.postgres
def test_bundle_resolve_says_no_match_without_inventing_guidance(capsys):
    assert (
        main(
            [
                "bundle",
                "resolve",
                "--query",
                "how many support tickets closed",
                "--domain",
                "support",
                "--concept",
                "tickets_closed",
            ]
        )
        == 2
    )
    bundle = json.loads(capsys.readouterr().out)

    assert bundle["resolution"]["status"] == "no_match"
    assert bundle["context_authority"] is None
    assert bundle["instructions"]["approved_sources"] == []


@pytest.mark.postgres
def test_bundle_resolve_refuses_a_domain_with_no_coverage_claim_before_resolving(capsys):
    """The malformed request is a usage error, not an answer (hy-bdff): exit
    1 with nothing on stdout, so an operator piping this cannot mistake it
    for the `no_match` that means no context covers the question."""
    assert (
        main(
            [
                "bundle",
                "resolve",
                "--query",
                "how many support tickets closed",
                "--domain",
                "support",
            ]
        )
        == 1
    )
    streams = capsys.readouterr()

    assert streams.out == ""
    assert "without saying what it must cover" in streams.err
    assert "list_context_catalog" in streams.err


@pytest.mark.postgres
def test_review_reconcile_sweep_command_runs_and_reports(capsys):
    """The `review reconcile-sweep` CLI wires the parser to the bounded sweep and
    connects to the database (hq-3ta2). With no open-PR tasks it is a clean no-op
    that reports zero -- the substantive reconcile behaviour is proven against the
    real server in tests/postgres/test_reconcile_sweep.py."""
    exit_code = main(["review", "reconcile-sweep", "--limit", "5"])
    assert exit_code == 0
    assert "reconciled 0 task(s) (limit 5)" in capsys.readouterr().out


@pytest.mark.postgres
def test_review_reconcile_sweep_rejects_an_out_of_range_limit(capsys):
    """The CLI enforces the SAME limit bounds as the HTTP route via the one shared
    validator (hq-3ta2 #437 round 2): an out-of-range --limit is rejected, not
    passed through to the sweep."""
    for bad in ("0", "-3", "250"):
        exit_code = main(["review", "reconcile-sweep", "--limit", bad])
        assert exit_code == 1
        assert "limit" in capsys.readouterr().err
