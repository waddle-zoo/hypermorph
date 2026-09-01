"""Git context sync against real Postgres and a real Git repository (hy-gh-43).

Walking-skeleton step 5: read the configured Git context and persist an
immutable snapshot whose semantic fields stay traceable to
repository/ref/path/commit. Both halves are real here -- a repository built
with the `git` CLI from the checked-in revenue fixture, and the Postgres
schema the migrations produce -- because the acceptance criteria are about
exact commits and persisted state, which a fake proves nothing about.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from hyperset.bundle import ContextDirective, resolve_analytics_context
from hyperset.context.schema import REF_AMBIGUOUS, REF_AWAITING_SYNC, REF_NOT_OBSERVED
from hyperset.context.sync import sync_git_context
from hyperset.db.engine import make_engine
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresContextRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
)
from tests.integration.test_git_context_source import CONTEXT_PATH, git, make_repository

# The real identities the pinned-source fixtures carry, which the checked-in
# revenue context refers to. A ref matching none of them is uncorroborated,
# which is disclosed rather than rejected (ADR 0017).
SUPERSET_DATASETS = (
    "ae48881d-334f-54a7-94e8-1ffcc73866e2",
    "5bcf01e3-3f70-50d2-bb31-562b627b09b8",
    "6f4976c2-25ea-5d98-b714-9ca8e6c9b7e4",
)
DATAHUB_GLOSSARY_TERM = "urn:li:glossaryTerm:recognized_revenue"
# Well-formed and observed by nobody: the shape a ref has when the connected
# system is simply behind the commit that names it.
UNOBSERVED_DATASET = "0f2c7a91-64bd-5e3f-9a10-8c4d2e6b71f5"


def add_second_domain(repository, *, domain: str) -> str:
    """A sibling domain in the same repository, declaring one ref nothing has
    observed. Two domains are two configured sources, so this is what proves
    one domain's corroboration gap is not the other's."""
    path = f"domains/{domain}"
    (repository / path).mkdir(parents=True)
    for name in ("manifest.yaml", "context.md", "evals.yaml"):
        content = (repository / CONTEXT_PATH / name).read_text(encoding="utf-8")
        (repository / path / name).write_text(
            content.replace("domain: revenue", f"domain: {domain}").replace(
                SUPERSET_DATASETS[1], UNOBSERVED_DATASET
            ),
            encoding="utf-8",
        )
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", f"add {domain} context", cwd=repository)
    return path


def add_colliding_domain(repository, *, path_slug: str) -> str:
    """A SECOND source in the same repository whose manifest declares an
    already-claimed domain -- the hy-gh-282 collision. The `domain:` line is kept
    verbatim (unlike `add_second_domain`, which rewrites it), so two sources claim
    one domain."""
    path = f"domains/{path_slug}"
    (repository / path).mkdir(parents=True)
    for name in ("manifest.yaml", "context.md", "evals.yaml"):
        content = (repository / CONTEXT_PATH / name).read_text(encoding="utf-8")
        (repository / path / name).write_text(content, encoding="utf-8")
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", f"add colliding source {path_slug}", cwd=repository)
    return path


def add_cased_domain(repository, *, path_slug: str, domain: str) -> str:
    """A source declaring `domain` in a chosen case (hy-gh-282, panel): used to
    prove `Revenue` and `revenue` are ONE domain after casefolding, so they
    collide rather than silently becoming two."""
    path = f"domains/{path_slug}"
    (repository / path).mkdir(parents=True)
    for name in ("manifest.yaml", "context.md", "evals.yaml"):
        content = (repository / CONTEXT_PATH / name).read_text(encoding="utf-8")
        (repository / path / name).write_text(
            content.replace("domain: revenue", f"domain: {domain}"), encoding="utf-8"
        )
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", f"add {domain} source {path_slug}", cwd=repository)
    return path


def _seed_observed_evidence(
    session_factory,
    *,
    datasets: tuple[str, ...] = SUPERSET_DATASETS,
    glossary: bool = True,
    read: tuple[str, ...] = ("superset", "datahub"),
) -> dict:
    """The Superset and DataHub observations the context claims link to.

    `datasets`/`glossary` narrow what was observed, which is how a partially
    corroborated domain is built: the Git context is unchanged and the
    connected systems are simply behind.

    `read` names the connectors whose sync run FINISHED, and it is separate
    from what they observed because those are two different facts about one
    absence (hy-lcgq). A connector that read and does not carry the asset says
    the asset is not in the estate; a connector that never finished a run says
    nothing about it at all, and `ObservedEvidenceResolver` reports the two
    with different codes. Leaving a run in `running` -- which every caller of
    this helper used to do -- models the second, so it has to be asked for
    rather than defaulted into."""
    connections = PostgresConnectionRepository(session_factory)
    syncs = PostgresSyncRepository(session_factory)
    assets = PostgresObservedAssetRepository(session_factory)

    superset = connections.create_or_update(connector_type="superset", display_name="Superset")
    datahub = connections.create_or_update(connector_type="datahub", display_name="DataHub")
    superset_run = syncs.begin_run(superset.id, mode="full")
    datahub_run = syncs.begin_run(datahub.id, mode="full")
    for external_id in datasets:
        assets.upsert(
            connection_id=superset.id,
            external_id=external_id,
            asset_type="dataset",
            sync_run_id=superset_run.id,
            raw_payload={"uuid": external_id},
        )
    if glossary:
        assets.upsert(
            connection_id=datahub.id,
            external_id=DATAHUB_GLOSSARY_TERM,
            asset_type="glossary_term",
            sync_run_id=datahub_run.id,
            raw_payload={"urn": DATAHUB_GLOSSARY_TERM},
        )
    for connector, run in (("superset", superset_run), ("datahub", datahub_run)):
        if connector in read:
            syncs.finish_run(run.id, counters={})
    return {"superset": superset.id, "datahub": datahub.id}


@pytest.fixture
def observed_evidence(session_factory):
    return _seed_observed_evidence(session_factory)


@pytest.fixture
def context_source(session_factory, tmp_path):
    repository = make_repository(tmp_path)
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repository), ref="main", path=CONTEXT_PATH
    )
    return source, repository


def run_sync(session_factory, tmp_path, source_id):
    return sync_git_context(
        source_id=source_id, session_factory=session_factory, cache_dir=tmp_path / "cache"
    )


def edit_and_commit(repository, message: str, *, replace: tuple[str, str]) -> str:
    manifest = repository / CONTEXT_PATH / "manifest.yaml"
    manifest.write_text(manifest.read_text().replace(*replace), encoding="utf-8")
    git("commit", "--quiet", "-am", message, cwd=repository)
    return git("rev-parse", "HEAD", cwd=repository)


@pytest.mark.postgres
def test_sync_pins_exact_repository_ref_path_and_commit(
    session_factory, tmp_path, observed_evidence, context_source
):
    source, repository = context_source

    result = run_sync(session_factory, tmp_path, source.id)

    assert result.status == "synced"
    assert result.commit_sha == git("rev-parse", "HEAD", cwd=repository)

    contexts = PostgresContextRepository(session_factory)
    stored = contexts.get_source(source.id)
    snapshot = stored.current_snapshot
    assert (stored.repository, stored.ref, stored.path) == (str(repository), "main", CONTEXT_PATH)
    assert snapshot.commit_sha == result.commit_sha
    assert snapshot.domain == "revenue"
    assert stored.last_attempt_status == "synced"
    assert stored.last_error is None


@pytest.mark.postgres
def test_snapshot_keeps_original_git_content_and_a_normalized_projection(
    session_factory, tmp_path, observed_evidence, context_source
):
    source, repository = context_source
    run_sync(session_factory, tmp_path, source.id)

    snapshot = PostgresContextRepository(session_factory).get_source(source.id).current_snapshot
    assert (
        snapshot.files["manifest.yaml"] == (repository / CONTEXT_PATH / "manifest.yaml").read_text()
    )
    assert snapshot.normalized["grain"] == "order_date by customer_dim.region"
    assert snapshot.owner_refs == [{"ref": "team:finance-data", "source": "manifest"}]


@pytest.mark.postgres
def test_declared_refs_link_to_the_observed_assets_that_carry_those_identities(
    session_factory, tmp_path, observed_evidence, context_source
):
    source, _ = context_source
    run_sync(session_factory, tmp_path, source.id)

    snapshot = PostgresContextRepository(session_factory).get_source(source.id).current_snapshot
    by_ref = {entry["ref"]: entry for entry in snapshot.evidence_refs}
    assert len(by_ref) == 3
    primary = by_ref[f"superset:dataset:{SUPERSET_DATASETS[0]}"]
    assert primary["connection_id"] == observed_evidence["superset"]
    assert primary["asset_id"].startswith("oa-")
    assert primary["observed_version_id"].startswith("oav-")
    assert primary["governed_source_ref"] == (
        "table:postgres:analytics.public.finance_orders_daily"
    )


@pytest.mark.postgres
def test_an_unchanged_commit_is_a_no_op(
    session_factory, tmp_path, observed_evidence, context_source
):
    source, _ = context_source
    first = run_sync(session_factory, tmp_path, source.id)

    second = run_sync(session_factory, tmp_path, source.id)

    contexts = PostgresContextRepository(session_factory)
    assert second.status == "unchanged"
    assert second.snapshot_id == first.snapshot_id
    assert len(contexts.history(source.id)) == 1
    assert contexts.get_source(source.id).last_attempt_status == "unchanged"


@pytest.mark.postgres
def test_a_new_commit_creates_a_new_snapshot_and_keeps_the_prior_one_replayable(
    session_factory, tmp_path, observed_evidence, context_source
):
    source, repository = context_source
    first = run_sync(session_factory, tmp_path, source.id)
    new_sha = edit_and_commit(repository, "widen the grain", replace=("inner", "left"))

    second = run_sync(session_factory, tmp_path, source.id)

    contexts = PostgresContextRepository(session_factory)
    assert second.status == "synced"
    assert second.commit_sha == new_sha
    assert second.snapshot_id != first.snapshot_id
    history = contexts.history(source.id)
    assert [snapshot.commit_sha for snapshot in history] == [first.commit_sha, new_sha]
    # The superseded snapshot is untouched, not rewritten in place.
    prior = contexts.get_snapshot(first.snapshot_id)
    assert prior.normalized["joins"][0]["type"] == "inner"
    assert contexts.get_snapshot(second.snapshot_id).normalized["joins"][0]["type"] == "left"


@pytest.mark.postgres
def test_invalid_context_fails_safely_without_replacing_the_last_valid_snapshot(
    session_factory, tmp_path, observed_evidence, context_source
):
    source, repository = context_source
    good = run_sync(session_factory, tmp_path, source.id)
    broken_sha = edit_and_commit(
        repository, "break the schema", replace=("schema_version: 1", "schema_version: 9")
    )

    result = run_sync(session_factory, tmp_path, source.id)

    contexts = PostgresContextRepository(session_factory)
    stored = contexts.get_source(source.id)
    assert result.status == "failed"
    assert any("schema_version must be 1" in reason for reason in result.reasons)
    assert stored.current_snapshot.commit_sha == good.commit_sha
    assert stored.last_attempt_status == "failed"
    assert stored.last_attempted_commit_sha == broken_sha
    assert "schema_version must be 1" in stored.last_error
    assert len(contexts.history(source.id)) == 1


@pytest.mark.postgres
def test_git_context_persists_when_no_declared_ref_resolves(
    session_factory, tmp_path, context_source
):
    """The worst corroboration case there is -- nothing observed at all --
    and Git is still the authority (ADR 0017). No connector has been synced,
    so no observed asset carries any identity the commit claims."""
    source, repository = context_source

    result = run_sync(session_factory, tmp_path, source.id)

    stored = PostgresContextRepository(session_factory).get_source(source.id)
    snapshot = stored.current_snapshot
    assert result.status == "synced"
    assert snapshot is not None
    assert snapshot.commit_sha == git("rev-parse", "HEAD", cwd=repository)
    assert stored.last_attempt_status == "synced"
    assert stored.last_error is None
    # Nothing corroborated, and the snapshot says exactly that: no link was
    # invented, and every declared ref carries its own coded finding.
    #
    # `ref_awaiting_sync` rather than `ref_not_observed`, and the docstring is
    # why: no connector has been synced, so nothing here measured whether these
    # assets exist (hy-lcgq). `ref_not_observed` in this world would report an
    # absence from an estate nobody read.
    assert snapshot.evidence_refs == []
    assert [(entry["code"], entry["ref"]) for entry in snapshot.evidence_findings] == [
        *(
            (REF_AWAITING_SYNC, f"superset:dataset:{external_id}")
            for external_id in sorted(SUPERSET_DATASETS)
        ),
    ]
    assert result.findings == snapshot.evidence_findings


@pytest.mark.postgres
def test_an_unchanged_commit_still_reports_what_it_could_not_corroborate(
    session_factory, tmp_path, context_source
):
    """ADR 0017's first motivating case, walked to its second step (hy-f462).

    The operator syncs Git with nothing connected, reads "sync the superset
    connection to corroborate it", does exactly that, and re-runs. The commit
    has not moved, so this is the unchanged path -- and the gap has NOT closed,
    because clearing a finding when evidence arrives late is hy-7ejr and needs
    a fresh commit or a reconcile. Silence here is indistinguishable from the
    gap having closed, so the second run must disclose exactly what the first
    did.
    """
    source, _ = context_source
    first = run_sync(session_factory, tmp_path, source.id)
    _seed_observed_evidence(session_factory)

    second = run_sync(session_factory, tmp_path, source.id)

    snapshot = PostgresContextRepository(session_factory).get_source(source.id).current_snapshot
    assert second.status == "unchanged"
    assert second.snapshot_id == first.snapshot_id
    assert second.findings == first.findings
    # Reported from the row rather than recomputed: the stored state is what
    # the catalog and the bundle read, so the report has to be the same list.
    assert second.findings == snapshot.evidence_findings
    # Both runs report `ref_awaiting_sync`: the first ran with nothing
    # connected, and the second replays the first's stored findings rather than
    # recomputing them, which is the point being made here.
    assert [entry["code"] for entry in second.findings] == [REF_AWAITING_SYNC] * 3
    assert snapshot.evidence_refs == []
    # hy-5lgg: those findings are present-tense sentences about the world,
    # computed at one moment and replayed byte-identically forever. What makes
    # the replay honest is the moment travelling with them -- the result names
    # when the snapshot resolved, which is the first run, not this one.
    assert second.synced_at == snapshot.synced_at
    assert second.synced_at == first.synced_at


@pytest.mark.postgres
def test_an_ambiguous_source_identity_is_disclosed_and_never_auto_picked(
    session_factory, tmp_path, observed_evidence, context_source
):
    source, _ = context_source
    # The same native UUID observed on a second Superset connection is two
    # different assets in the real world; Hyperset must not pick one.
    second = PostgresConnectionRepository(session_factory).create_or_update(
        connector_type="superset", display_name="Superset (staging)"
    )
    run = PostgresSyncRepository(session_factory).begin_run(second.id, mode="full")
    PostgresObservedAssetRepository(session_factory).upsert(
        connection_id=second.id,
        external_id=SUPERSET_DATASETS[0],
        asset_type="dataset",
        sync_run_id=run.id,
        raw_payload={"uuid": SUPERSET_DATASETS[0]},
    )
    ambiguous = f"superset:dataset:{SUPERSET_DATASETS[0]}"

    result = run_sync(session_factory, tmp_path, source.id)

    snapshot = PostgresContextRepository(session_factory).get_source(source.id).current_snapshot
    assert result.status == "synced"
    # The invariant this bead must not trade away: an ambiguous identity
    # resolves to NOTHING. Disclosing the ambiguity is not licence to guess.
    assert [entry["code"] for entry in snapshot.evidence_findings] == [REF_AMBIGUOUS]
    assert snapshot.evidence_findings[0]["ref"] == ambiguous
    assert "is ambiguous" in snapshot.evidence_findings[0]["message"]
    assert ambiguous not in {ref["ref"] for ref in snapshot.evidence_refs}
    # The other two overrides are unaffected: one bad ref is not a domain verdict.
    assert len(snapshot.evidence_refs) == 2


@pytest.mark.postgres
def test_unrelated_datahub_state_does_not_gate_explicit_superset_overrides(
    session_factory, tmp_path, context_source
):
    source, _ = context_source
    # Superset is synced and DataHub is not. Definitions no longer embed a
    # DataHub identity, so unrelated catalog state cannot gate Git context.
    _seed_observed_evidence(session_factory, glossary=False, read=("superset",))

    result = run_sync(session_factory, tmp_path, source.id)

    snapshot = PostgresContextRepository(session_factory).get_source(source.id).current_snapshot
    assert result.status == "synced"
    assert sorted(ref["ref"] for ref in snapshot.evidence_refs) == sorted(
        f"superset:dataset:{external_id}" for external_id in SUPERSET_DATASETS
    )
    assert snapshot.evidence_findings == []


@pytest.mark.postgres
def test_an_uncorroborated_ref_in_one_domain_does_not_touch_a_sibling_domain(
    session_factory, tmp_path, context_source
):
    source, repository = context_source
    sibling_path = add_second_domain(repository, domain="logistics")
    contexts = PostgresContextRepository(session_factory)
    sibling = contexts.register_source(repository=str(repository), ref="main", path=sibling_path)
    _seed_observed_evidence(session_factory)

    revenue = run_sync(session_factory, tmp_path, source.id)
    logistics = run_sync(session_factory, tmp_path, sibling.id)

    assert (revenue.status, logistics.status) == ("synced", "synced")
    revenue_snapshot = contexts.get_source(source.id).current_snapshot
    logistics_snapshot = contexts.get_source(sibling.id).current_snapshot
    # The sibling declares one ref nothing observed; the revenue domain
    # declares none, and neither domain's outcome is the other's.
    assert revenue_snapshot.evidence_findings == []
    assert len(revenue_snapshot.evidence_refs) == 3
    assert [(entry["code"], entry["ref"]) for entry in logistics_snapshot.evidence_findings] == [
        (REF_NOT_OBSERVED, f"superset:dataset:{UNOBSERVED_DATASET}")
    ]
    assert UNOBSERVED_DATASET not in {
        ref["external_id"] for ref in logistics_snapshot.evidence_refs
    }


@pytest.mark.postgres
def test_a_structurally_unlinkable_ref_is_still_rejected_at_parse_time(
    session_factory, tmp_path, observed_evidence, context_source
):
    """Unobserved is not malformed. A ref that cannot name an asset in any
    possible world is a defect in the commit itself, so it still fails the
    sync and leaves the last valid snapshot serving."""
    source, repository = context_source
    good = run_sync(session_factory, tmp_path, source.id)
    broken_sha = edit_and_commit(
        repository,
        "break a ref",
        replace=(f"superset:dataset:{SUPERSET_DATASETS[1]}", "superset:teapot:whatever"),
    )

    result = run_sync(session_factory, tmp_path, source.id)

    stored = PostgresContextRepository(session_factory).get_source(source.id)
    assert result.status == "failed"
    assert any("unknown superset asset type" in reason for reason in result.reasons)
    assert stored.current_snapshot.commit_sha == good.commit_sha
    assert stored.last_attempted_commit_sha == broken_sha


@pytest.mark.postgres
def test_a_snapshotted_commit_can_never_be_rewritten(
    session_factory, tmp_path, observed_evidence, context_source
):
    source, _ = context_source
    first = run_sync(session_factory, tmp_path, source.id)
    contexts = PostgresContextRepository(session_factory)

    # The only write path into context content, called again for the same
    # commit with different content: the commit's snapshot wins.
    snapshot, created = contexts.record_snapshot(
        source_id=source.id,
        commit_sha=first.commit_sha,
        committed_at=None,
        domain="revenue",
        title="Rewritten outside Git",
        files={"manifest.yaml": "schema_version: 1\n"},
        normalized={"grain": "rewritten"},
    )

    assert created is False
    assert snapshot.id == first.snapshot_id
    assert snapshot.title != "Rewritten outside Git"
    assert contexts.get_snapshot(first.snapshot_id).normalized["grain"] == (
        "order_date by customer_dim.region"
    )


@pytest.mark.postgres
def test_a_ref_moved_back_to_an_earlier_commit_replays_that_snapshot(
    session_factory, tmp_path, observed_evidence, context_source
):
    source, repository = context_source
    first = run_sync(session_factory, tmp_path, source.id)
    edit_and_commit(repository, "widen the grain", replace=("inner", "left"))
    run_sync(session_factory, tmp_path, source.id)

    git("reset", "--hard", "--quiet", first.commit_sha, cwd=repository)
    result = run_sync(session_factory, tmp_path, source.id)

    contexts = PostgresContextRepository(session_factory)
    assert result.status == "unchanged"
    assert result.snapshot_id == first.snapshot_id
    assert contexts.get_source(source.id).current_snapshot.commit_sha == first.commit_sha
    # Both commits remain in history; a rollback adds no third snapshot.
    assert len(contexts.history(source.id)) == 2


@pytest.mark.postgres
def test_repin_snapshot_rolls_the_serving_pin_back_without_a_new_snapshot(
    session_factory, tmp_path, observed_evidence, context_source
):
    """hy-bo5p: `repin_snapshot` re-points current_snapshot_id at a PRIOR stored snapshot --
    an integration re-pin, not a re-read: the pin moves, history is unchanged, and it is
    fail-closed on an unknown commit and workspace-scoped."""
    source, repository = context_source
    first = run_sync(session_factory, tmp_path, source.id)
    second_sha = edit_and_commit(repository, "widen the grain", replace=("inner", "left"))
    run_sync(session_factory, tmp_path, source.id)
    contexts = PostgresContextRepository(session_factory)
    assert contexts.get_source(source.id).current_snapshot.commit_sha == second_sha
    assert len(contexts.history(source.id)) == 2

    # RE-PIN back to the first commit.
    rolled = contexts.repin_snapshot(source.id, commit_sha=first.commit_sha)
    assert rolled.current_snapshot.commit_sha == first.commit_sha
    assert contexts.get_source(source.id).current_snapshot.commit_sha == first.commit_sha
    assert len(contexts.history(source.id)) == 2  # no new snapshot: an integration re-pin

    # FAIL CLOSED: a commit this source never snapshotted cannot be rolled back to.
    with pytest.raises(NotFoundError):
        contexts.repin_snapshot(source.id, commit_sha="0" * 40)
    # WORKSPACE-SCOPED: another tenant cannot re-pin this source (reported as not found).
    with pytest.raises(NotFoundError):
        contexts.repin_snapshot(source.id, commit_sha=first.commit_sha, workspace="other-tenant")
    # The serving pin is unchanged after both fail-closed calls.
    assert contexts.get_source(source.id).current_snapshot.commit_sha == first.commit_sha


@pytest.mark.postgres
def test_the_current_snapshot_and_checkpoint_survive_a_restart(postgres_dsn, db_engine, tmp_path):
    """Restart proxy, same shape as `test_persistence.py`: this test commits
    for real (no savepoint fixture) and then opens a brand new engine after
    the writing one is disposed. The current commit and the Git checkpoint
    come back from Postgres, not from process memory."""
    write_engine = make_engine(postgres_dsn)
    write_factory = sessionmaker(bind=write_engine, expire_on_commit=False)
    try:
        _seed_observed_evidence(write_factory)
        repository = make_repository(tmp_path)
        source = PostgresContextRepository(write_factory).register_source(
            repository=str(repository), ref="main", path=CONTEXT_PATH
        )
        result = run_sync(write_factory, tmp_path, source.id)
        assert result.status == "synced"
    finally:
        write_engine.dispose()

    read_engine = make_engine(postgres_dsn)
    read_factory = sessionmaker(bind=read_engine, expire_on_commit=False)
    try:
        reread = PostgresContextRepository(read_factory).get_source(source.id)
        assert reread.current_snapshot.commit_sha == result.commit_sha
        assert reread.current_snapshot.normalized["domain"] == "revenue"
        assert reread.last_attempted_commit_sha == result.commit_sha
        # A resync after the "restart" still sees an unchanged commit.
        assert run_sync(read_factory, tmp_path, source.id).status == "unchanged"
    finally:
        read_engine.dispose()
        with db_engine.connect() as connection:
            connection.execute(text("TRUNCATE connections, context_sources CASCADE"))
            connection.commit()


# --- Domain collision: refuse at sync, disclose at resolve, recover by disable (hy-gh-282) ---


def _revenue_directive():
    return ContextDirective(domains=["revenue"], concepts=["recognized_revenue"])


def _collide(session_factory, tmp_path, context_source):
    """Two ENABLED sources both claiming `revenue`, the pre-existing-collision
    state an estate reaches before the sync guard existed. Built by disabling the
    first so the second can sync, then re-enabling the first directly through the
    repository (the CLI `enable` refuses this on purpose; the repository method
    does not, which is what lets this model a legacy collision)."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"
    contexts.set_enabled(source.id, enabled=False)
    colliding_path = add_colliding_domain(repository, path_slug="revenue-copy")
    second = contexts.register_source(repository=str(repository), ref="main", path=colliding_path)
    assert run_sync(session_factory, tmp_path, second.id).status == "synced"
    contexts.set_enabled(source.id, enabled=True)
    return source, second, contexts


@pytest.mark.postgres
def test_a_second_source_claiming_a_synced_domain_is_refused_at_sync(
    session_factory, tmp_path, context_source
):
    """Acceptance: sync refuses a manifest whose domain another source claims,
    names both, leaves the previous snapshot serving, and fails (hy-gh-282)."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"

    colliding_path = add_colliding_domain(repository, path_slug="revenue-copy")
    second = contexts.register_source(repository=str(repository), ref="main", path=colliding_path)
    result = run_sync(session_factory, tmp_path, second.id)

    assert result.status == "failed"
    reason = result.reasons[0]
    assert "revenue" in reason
    assert source.id in reason  # names the existing claimant
    assert "hyperset context disable" in reason  # the supported recovery
    # The collision never became a served snapshot; the first source still serves.
    assert contexts.get_source(second.id).current_snapshot is None
    assert contexts.get_source(source.id).current_snapshot.domain == "revenue"


@pytest.mark.postgres
def test_a_source_re_syncing_its_own_domain_is_not_a_collision(
    session_factory, tmp_path, context_source
):
    """The detection excludes the source itself: re-syncing an unchanged domain
    must not be read as a second claimant (the reason it is a query, not a column
    uniqueness constraint on the versioned snapshot)."""
    source, _ = context_source
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"
    # A second sync of the SAME source: unchanged commit, no collision.
    assert run_sync(session_factory, tmp_path, source.id).status == "unchanged"


@pytest.mark.postgres
def test_two_enabled_sources_on_one_domain_resolve_to_domain_ambiguous(
    session_factory, tmp_path, context_source
):
    """A legacy collision is DISCLOSED at resolve with `domain_ambiguous` -- its
    own code, message naming the sources, and no authority silently chosen --
    distinct from a caller naming multiple domains (hy-gh-282 findings 1-2)."""
    source, second, _ = _collide(session_factory, tmp_path, context_source)

    bundle = resolve_analytics_context(
        query="recognized revenue", directive=_revenue_directive(), session_factory=session_factory
    ).to_dict()

    assert bundle["resolution"]["status"] == "no_match"
    assert bundle["context_authority"] is None  # no winner picked between two commits
    codes = [warning["code"] for warning in bundle["resolution"]["warnings"]]
    assert codes == ["domain_ambiguous"]  # NOT multiple_domains
    message = bundle["resolution"]["warnings"][0]["message"]
    assert source.id in message and second.id in message
    assert "the directive is correct" in message


@pytest.mark.postgres
def test_disabling_a_colliding_source_lets_the_domain_resolve_again(
    session_factory, tmp_path, context_source, monkeypatch
):
    """The supported recovery works end to end through the CLI command: disabling
    one claimant clears the ambiguity and the domain resolves governed again --
    no hand-written SQL (hy-gh-282)."""
    import argparse

    from hyperset import cli

    source, second, contexts = _collide(session_factory, tmp_path, context_source)

    monkeypatch.setattr(
        cli, "_context_repository", lambda: PostgresContextRepository(session_factory)
    )
    assert cli.cmd_context_disable(argparse.Namespace(source_id=second.id)) == 0
    assert contexts.get_source(second.id).enabled is False

    bundle = resolve_analytics_context(
        query="recognized revenue", directive=_revenue_directive(), session_factory=session_factory
    ).to_dict()
    assert bundle["resolution"]["status"] in ("governed", "mixed")
    assert bundle["context_authority"] is not None


@pytest.mark.postgres
def test_the_enable_command_refuses_to_re_create_a_collision(
    session_factory, tmp_path, context_source, monkeypatch
):
    """`hyperset context enable` re-checks the collision so it cannot silently
    re-introduce the ambiguity a disable resolved (hy-gh-282). The disabled source
    stays disabled and the command exits non-zero."""
    import argparse

    from hyperset import cli

    source, second, contexts = _collide(session_factory, tmp_path, context_source)
    # Reconcile down to one, then try to re-enable the other.
    contexts.set_enabled(second.id, enabled=False)
    monkeypatch.setattr(
        cli, "_context_repository", lambda: PostgresContextRepository(session_factory)
    )

    code = cli.cmd_context_enable(argparse.Namespace(source_id=second.id))

    assert code == 1  # refused
    assert contexts.get_source(second.id).enabled is False  # stayed disabled
    # And the domain still resolves from the one remaining claimant.
    bundle = resolve_analytics_context(
        query="q", directive=_revenue_directive(), session_factory=session_factory
    ).to_dict()
    assert bundle["resolution"]["status"] in ("governed", "mixed")


# --- Panel bounce: no crash on an already-multi-claimant estate; casefold (hy-gh-282) ---


def _revenue_estate(session_factory, tmp_path, context_source, extra_slugs):
    """N enabled sources all claiming `revenue` -- a legacy multi-claimant estate.

    Each extra source is synced with the others temporarily disabled (sync would
    otherwise refuse the collision), then all are enabled, reproducing the
    >=2-enabled state that `source_claiming_domain` must handle without raising
    `MultipleResultsFound`."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"
    sources = [source]
    for slug in extra_slugs:
        for existing in sources:
            contexts.set_enabled(existing.id, enabled=False)
        path = add_colliding_domain(repository, path_slug=slug)
        new = contexts.register_source(repository=str(repository), ref="main", path=path)
        assert run_sync(session_factory, tmp_path, new.id).status == "synced"
        sources.append(new)
    for existing in sources:
        contexts.set_enabled(existing.id, enabled=True)
    return contexts, sources


@pytest.mark.postgres
def test_source_claiming_domain_does_not_raise_on_two_enabled_claimants(
    session_factory, tmp_path, context_source
):
    """The crash both reviewers found: `scalar_one_or_none()` would raise
    `MultipleResultsFound` on the exact >=2-enabled estate this feature targets.
    `.first()` returns one deterministic claimant instead (hy-gh-282, panel)."""
    contexts, sources = _revenue_estate(session_factory, tmp_path, context_source, ["copy2"])

    claimant = contexts.source_claiming_domain("revenue")  # must not raise
    assert claimant is not None
    assert claimant.id in {source.id for source in sources}


@pytest.mark.postgres
def test_syncing_into_a_two_enabled_domain_refuses_gracefully_not_a_crash(
    session_factory, tmp_path, context_source
):
    """Driving SYNC into an already-2-enabled domain: a graceful named refusal,
    never a crash (hy-gh-282, panel MAJOR path a)."""
    contexts, _ = _revenue_estate(session_factory, tmp_path, context_source, ["copy2"])
    _, repository = context_source

    third_path = add_colliding_domain(repository, path_slug="copy3")
    third = contexts.register_source(repository=str(repository), ref="main", path=third_path)
    result = run_sync(session_factory, tmp_path, third.id)

    assert result.status == "failed"
    assert "revenue" in result.reasons[0]
    assert "commit" in result.reasons[0]  # message parity: names the claimant's commit
    assert contexts.get_source(third.id).current_snapshot is None


@pytest.mark.postgres
def test_enabling_into_a_two_enabled_domain_refuses_gracefully_not_a_crash(
    session_factory, tmp_path, context_source, monkeypatch
):
    """Driving ENABLE into an already-2-enabled domain: a graceful refusal, never
    a crash (hy-gh-282, panel MAJOR path b)."""
    import argparse

    from hyperset import cli

    contexts, sources = _revenue_estate(
        session_factory, tmp_path, context_source, ["copy2", "copy3"]
    )
    third = sources[2]
    contexts.set_enabled(third.id, enabled=False)  # re-enabling it faces 2 claimants
    monkeypatch.setattr(
        cli, "_context_repository", lambda: PostgresContextRepository(session_factory)
    )

    code = cli.cmd_context_enable(argparse.Namespace(source_id=third.id))  # must not raise

    assert code == 1
    assert contexts.get_source(third.id).enabled is False


@pytest.mark.postgres
def test_a_case_variant_domain_is_the_same_domain_and_collides_at_sync(
    session_factory, tmp_path, context_source
):
    """`Revenue` and `revenue` are ONE domain after casefolding, so a second
    source declaring the case-variant is refused as a collision rather than
    silently becoming a second, unresolvable domain (hy-gh-282, panel MINOR)."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"
    assert contexts.get_source(source.id).current_snapshot.domain == "revenue"

    cased_path = add_cased_domain(repository, path_slug="revenue-cased", domain="Revenue")
    cased = contexts.register_source(repository=str(repository), ref="main", path=cased_path)
    result = run_sync(session_factory, tmp_path, cased.id)

    assert result.status == "failed"
    assert source.id in result.reasons[0]


@pytest.mark.postgres
def test_a_mixed_case_domain_request_resolves_against_the_casefolded_store(
    session_factory, tmp_path, context_source
):
    """The request side agrees with the store (hy-gh-282, re-panel): a directive
    naming 'Revenue' resolves the stored 'revenue' domain, because both are
    casefolded. Without the request-side casefold this returned unknown_domain --
    a previously-resolvable domain broken by the store-side normalization."""
    source, _ = context_source
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"

    bundle = resolve_analytics_context(
        query="recognized revenue",
        directive=ContextDirective(domains=["Revenue"], concepts=["recognized_revenue"]),
        session_factory=session_factory,
    ).to_dict()

    assert bundle["resolution"]["status"] in ("governed", "mixed")
    assert bundle["context_authority"] is not None


@pytest.mark.postgres
def test_an_adapter_domain_discloses_its_projection_on_the_governed_bundle(
    session_factory, tmp_path
):
    """A domain synced through a context adapter carries `resolution.projection`
    disclosing what the adapter did (283-5, hy-13b8), computed from the real
    persisted snapshot's stored corpus.

    The governed bundle is built by calling `_governed` on the synced source
    directly, deliberately NOT through `resolve_analytics_context`. A directive
    that NAMES a domain must also name the `concepts` that domain declares
    (directive.py coverage claim), and THIS adapter maps only
    `domain/title/owners` and declares no `definitions` block, so it declares no
    concept and cannot be SELECTED by a directive. That is a selection concern,
    orthogonal to what a governed answer DISCLOSES: this test isolates the
    disclosure wiring against a real adapter-sourced snapshot. The end-to-end
    resolve path -- an adapter domain that DOES declare concepts and is selected
    through `resolve_analytics_context` -- is exercised by
    `test_an_adapter_domain_is_selectable_via_resolve_once_it_declares_concepts`
    (283-4b, hy-1xc6)."""
    adapter = (
        "schema_version: 1\nadapter: acme-pipeline-docs-v2\n"
        "discover:\n  unit: 'docs/*'\n  manifest: project.md\n  context_doc: project.md\n"
        "map:\n  domain: '$.urn | slug'\n  title: '$.title'\n"
        "  owners: \"$.owners[*] | prefix('team:')\"\n"
    )
    project = (
        "---\nurn: Adapter Domain\ntitle: Adapter Domain\nowners:\n  - finance-data\n---\n"
        "# Adapter Domain\n\nHow this domain is governed.\n"
    )
    repository = tmp_path / "customer-repo"
    path = "docs/adapter-domain"
    (repository / path).mkdir(parents=True)
    git("init", "--quiet", "--initial-branch=main", ".", cwd=repository)
    git("config", "user.email", "context@example.test", cwd=repository)
    git("config", "user.name", "Context Owner", cwd=repository)
    (repository / path / "context-adapter.yaml").write_text(adapter, encoding="utf-8")
    (repository / path / "project.md").write_text(project, encoding="utf-8")
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "add adapter corpus", cwd=repository)

    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repository), ref="main", path=path
    )
    result = sync_git_context(
        source_id=source.id, session_factory=session_factory, cache_dir=tmp_path / "cache"
    )
    assert result.status == "synced"

    from hyperset.bundle.resolver import _empty_evidence, _governed

    governed_source = PostgresContextRepository(session_factory).get_source(source.id)
    directive = ContextDirective(domains=["adapter-domain"], concepts=["governed"])
    request = {"query": "what governs adapter-domain?", "directive": directive.to_dict()}
    bundle = _governed(request, governed_source, _empty_evidence(), [], directive, session_factory)

    assert bundle.resolution["status"] in ("governed", "mixed")
    assert bundle.resolution["projection"] == {
        "adapter": "acme-pipeline-docs-v2",
        "adapter_version": 1,
        "fields_unmapped": [],
        "fields_lossy": [],
        "fields_derived": [],
    }
    # And the provenance win holds: authority is the corpus commit, not a build.
    assert bundle.context_authority["commit_sha"] == git("rev-parse", "HEAD", cwd=repository)


@pytest.mark.postgres
def test_an_adapter_domain_is_selectable_via_resolve_once_it_declares_concepts(
    session_factory, tmp_path
):
    """283-4b (hy-1xc6): applying `map.definitions` gives an adapter domain its
    concepts, so a directive naming a declared term SELECTS it through the
    ORDINARY resolve path -- and `resolution.projection` (283-5) is reachable
    end-to-end, not only by driving `_governed` directly (the #310 boundary this
    bead closes). The concepts are the customer's own words, read from a separate
    file set through the whitelist; nothing is authored, so the projection still
    reports empty substrate lists."""
    adapter = (
        "schema_version: 1\nadapter: acme-pipeline-docs-v2\n"
        "discover:\n  unit: 'docs/*'\n  manifest: project.md\n  context_doc: project.md\n"
        "map:\n  domain: '$.urn | slug'\n  title: '$.title'\n"
        "  owners: \"$.owners[*] | prefix('team:')\"\n"
        "  definitions:\n    from: 'concepts/*.md'\n"
        "    term: '$.term'\n    statement: '$.statement'\n"
    )
    project = (
        "---\nurn: Adapter Domain\ntitle: Adapter Domain\nowners:\n  - finance-data\n---\n"
        "# Adapter Domain\n\nHow this domain is governed.\n"
    )
    concept = (
        "---\nterm: recognized_revenue\n"
        "statement: Revenue recognized under ASC 606.\n---\n# recognized_revenue\n"
    )
    repository = tmp_path / "customer-repo"
    path = "docs/adapter-domain"
    (repository / path / "concepts").mkdir(parents=True)
    git("init", "--quiet", "--initial-branch=main", ".", cwd=repository)
    git("config", "user.email", "context@example.test", cwd=repository)
    git("config", "user.name", "Context Owner", cwd=repository)
    (repository / path / "context-adapter.yaml").write_text(adapter, encoding="utf-8")
    (repository / path / "project.md").write_text(project, encoding="utf-8")
    (repository / path / "concepts" / "revenue.md").write_text(concept, encoding="utf-8")
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "add adapter corpus with a concept", cwd=repository)

    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repository), ref="main", path=path
    )
    result = sync_git_context(
        source_id=source.id, session_factory=session_factory, cache_dir=tmp_path / "cache"
    )
    assert result.status == "synced"

    # Named through the ORDINARY resolve entrypoint, claiming the concept the
    # adapter's definitions declared -- no `_governed`-direct shortcut.
    bundle = resolve_analytics_context(
        query="what governs recognized revenue?",
        directive=ContextDirective(domains=["adapter-domain"], concepts=["recognized_revenue"]),
        session_factory=session_factory,
    )
    assert bundle.resolution["status"] in ("governed", "mixed")
    assert bundle.resolution["projection"] == {
        "adapter": "acme-pipeline-docs-v2",
        "adapter_version": 1,
        "fields_unmapped": [],
        "fields_lossy": [],
        "fields_derived": [],
    }
    # The concept the customer wrote is declared and served, and authority is the
    # corpus commit, not a build artifact.
    assert any(
        definition["term"] == "recognized_revenue"
        for definition in bundle.instructions["definitions"]
    )
    assert bundle.context_authority["commit_sha"] == git("rev-parse", "HEAD", cwd=repository)


@pytest.mark.postgres
def test_a_hand_written_domain_carries_no_projection_key(
    session_factory, tmp_path, observed_evidence, context_source
):
    """Back-compat: a v0 (non-adapter) domain's resolution is byte-identical to
    before the field existed -- no `projection` key at all."""
    source, _ = context_source
    run_sync(session_factory, tmp_path, source.id)
    bundle = resolve_analytics_context(
        query="what governs revenue?",
        directive=ContextDirective(domains=["revenue"], concepts=["recognized_revenue"]),
        session_factory=session_factory,
    )
    assert bundle.resolution["status"] in ("governed", "mixed")
    assert "projection" not in bundle.resolution


def _synced_adapter_governed_source(session_factory, tmp_path):
    """Build, commit, sync a minimal adapter domain and return its governed source
    -- the seam the 283-6 status-degrade tests drive `_governed` against."""
    adapter = (
        "schema_version: 1\nadapter: acme-pipeline-docs-v2\n"
        "discover:\n  unit: 'docs/*'\n  manifest: project.md\n  context_doc: project.md\n"
        "map:\n  domain: '$.urn | slug'\n  title: '$.title'\n"
        "  owners: \"$.owners[*] | prefix('team:')\"\n"
    )
    project = (
        "---\nurn: Derived Domain\ntitle: Derived Domain\nowners:\n  - finance-data\n---\n"
        "# Derived Domain\n\nHow this domain is governed.\n"
    )
    repository = tmp_path / "customer-repo"
    path = "docs/derived-domain"
    (repository / path).mkdir(parents=True)
    git("init", "--quiet", "--initial-branch=main", ".", cwd=repository)
    git("config", "user.email", "context@example.test", cwd=repository)
    git("config", "user.name", "Context Owner", cwd=repository)
    (repository / path / "context-adapter.yaml").write_text(adapter, encoding="utf-8")
    (repository / path / "project.md").write_text(project, encoding="utf-8")
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "add adapter corpus", cwd=repository)
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repository), ref="main", path=path
    )
    assert (
        sync_git_context(
            source_id=source.id, session_factory=session_factory, cache_dir=tmp_path / "cache"
        ).status
        == "synced"
    )
    return PostgresContextRepository(session_factory).get_source(source.id)


@pytest.mark.postgres
def test_an_unreviewed_adapter_derived_field_degrades_governed_to_mixed(
    session_factory, tmp_path, monkeypatch
):
    """283-6 (hy-v5iy), Brandon's fork-3 = REUSE governed->mixed. An adapter that
    DERIVED a field with no `reviewed_by` degrades the domain governed->mixed
    (reusing the existing status value); a derived field WITH a reviewer stays
    governed; and a governed domain whose overlay derived NOTHING stays governed
    and authoritative. `adapter_projection` serves an empty `fields_derived` today,
    so this stubs the seam a future producer will fill -- the wiring is what 283-6
    delivers."""
    from hyperset.bundle import resolver

    governed_source = _synced_adapter_governed_source(session_factory, tmp_path)
    directive = ContextDirective(domains=["derived-domain"], concepts=["governed"])
    request = {"query": "what governs derived-domain?", "directive": directive.to_dict()}

    def _bundle_with_projection(projection):
        monkeypatch.setattr(resolver, "adapter_projection", lambda files: projection)
        return resolver._governed(
            request, governed_source, resolver._empty_evidence(), [], directive, session_factory
        )

    base = {"adapter": "acme", "adapter_version": 1, "fields_unmapped": [], "fields_lossy": []}

    # ARM 1: derived + unreviewed -> mixed, and the summary says why.
    mixed = _bundle_with_projection({**base, "fields_derived": [{"field": "region"}]})
    assert mixed.resolution["status"] == "mixed"
    assert "no human has reviewed" in mixed.resolution["summary"]

    # ARM 2: derived + reviewed_by set -> stays governed.
    reviewed = _bundle_with_projection(
        {**base, "fields_derived": [{"field": "region", "reviewed_by": "alice@acme"}]}
    )
    assert reviewed.resolution["status"] == "governed"

    # ARM 3: a governed field with an adapter overlay that derived NOTHING
    # (fields_derived empty, even with a lossy overlay) -> governed authoritative,
    # never downgraded by the overlay.
    authoritative = _bundle_with_projection(
        {**base, "fields_lossy": [{"field": "title"}], "fields_derived": []}
    )
    assert authoritative.resolution["status"] == "governed"


# --- Governed hierarchy: validate the parent forest at sync (hy-2zoy, ADR-0031) ---


def add_domain(repository, *, path_slug: str, domain: str, parent: str | None = None) -> str:
    """A second source declaring a chosen `domain` and an optional governed `parent`,
    built from the revenue fixture. Never emits a served edge -- this slice validates
    the hierarchy at sync only."""
    path = f"domains/{path_slug}"
    (repository / path).mkdir(parents=True)
    for name in ("manifest.yaml", "context.md", "evals.yaml"):
        content = (repository / CONTEXT_PATH / name).read_text(encoding="utf-8")
        if name == "manifest.yaml":
            content = content.replace("domain: revenue", f"domain: {domain}")
            if parent is not None:
                content = content.replace(
                    f"domain: {domain}", f"domain: {domain}\nparent: {parent}"
                )
        (repository / path / name).write_text(content, encoding="utf-8")
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", f"add {domain} (parent={parent})", cwd=repository)
    return path


@pytest.mark.postgres
def test_a_domain_declaring_an_unknown_parent_is_refused_at_sync(
    session_factory, tmp_path, context_source
):
    """ADR-0031 fail-closed: a manifest naming a parent that no declared domain
    provides is refused at sync (the parent map spans the whole estate), and no
    snapshot is recorded -- the child does not serve pointing at a phantom parent."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"

    child = add_domain(repository, path_slug="orphan", domain="orphan", parent="ghost")
    orphan = contexts.register_source(repository=str(repository), ref="main", path=child)
    result = run_sync(session_factory, tmp_path, orphan.id)

    assert result.status == "failed"
    assert any("unknown parent" in reason for reason in result.reasons)
    assert "ghost" in result.reasons[0]
    assert contexts.get_source(orphan.id).current_snapshot is None


@pytest.mark.postgres
def test_a_valid_parent_child_forest_syncs(session_factory, tmp_path, context_source):
    """A child whose parent IS a declared domain is a valid forest and syncs. The
    served bundle is unchanged (parent is not emitted) -- only the sync is validated."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"  # revenue, a root

    child = add_domain(repository, path_slug="regional", domain="regional", parent="revenue")
    regional = contexts.register_source(repository=str(repository), ref="main", path=child)
    assert run_sync(session_factory, tmp_path, regional.id).status == "synced"
    assert contexts.get_source(regional.id).current_snapshot.normalized["parent"] == "revenue"


@pytest.mark.postgres
def test_a_parent_cycle_introduced_by_an_update_is_refused_at_sync(
    session_factory, tmp_path, context_source
):
    """A cycle cannot form by first-sync (a child before its parent is an unknown
    parent), so it is introduced by an UPDATE: revenue is a root, regional declares
    revenue as parent, then revenue is re-synced declaring regional as ITS parent --
    a 2-cycle. That re-sync is refused, and revenue's prior root snapshot keeps
    serving."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"
    child = add_domain(repository, path_slug="regional", domain="regional", parent="revenue")
    regional = contexts.register_source(repository=str(repository), ref="main", path=child)
    assert run_sync(session_factory, tmp_path, regional.id).status == "synced"

    # Close the loop: revenue now declares regional as its parent.
    manifest = repository / CONTEXT_PATH / "manifest.yaml"
    manifest.write_text(
        manifest.read_text().replace("domain: revenue", "domain: revenue\nparent: regional"),
        encoding="utf-8",
    )
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "revenue now nests under regional (cycle)", cwd=repository)
    result = run_sync(session_factory, tmp_path, source.id)

    assert result.status == "failed"
    assert any("cycle" in reason for reason in result.reasons)
    # The cycle never served: revenue's previous ROOT snapshot is intact.
    assert contexts.get_source(source.id).current_snapshot.normalized["parent"] is None


@pytest.mark.postgres
def test_a_self_parent_is_refused_at_sync(session_factory, tmp_path, context_source):
    """The one forest rule a single manifest can violate (self-cycle) is caught at
    parse and surfaces at sync as a validation failure, no snapshot recorded."""
    _, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    path = add_domain(repository, path_slug="selfie", domain="selfie", parent="selfie")
    selfie = contexts.register_source(repository=str(repository), ref="main", path=path)
    result = run_sync(session_factory, tmp_path, selfie.id)

    assert result.status == "failed"
    assert any("its parent" in reason for reason in result.reasons)
    assert contexts.get_source(selfie.id).current_snapshot is None


@pytest.mark.postgres
def test_a_dangling_parent_elsewhere_does_not_wedge_an_unrelated_sync(
    session_factory, tmp_path, context_source
):
    """The check is SCOPED to the incoming domain. If a parent is later disabled, its
    child's stored parent dangles -- but that must NOT fail an UNRELATED source's sync
    with a misattributed reason (which a whole-estate check would, wedging every
    write). A new root `sales` still syncs while `regional` dangles."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"  # revenue root
    child = add_domain(repository, path_slug="regional", domain="regional", parent="revenue")
    regional = contexts.register_source(repository=str(repository), ref="main", path=child)
    assert run_sync(session_factory, tmp_path, regional.id).status == "synced"

    # Disable the parent -> regional's stored parent now dangles across the estate.
    contexts.set_enabled(source.id, enabled=False)

    sales_path = add_domain(repository, path_slug="sales", domain="sales")  # a root, unrelated
    sales = contexts.register_source(repository=str(repository), ref="main", path=sales_path)
    result = run_sync(session_factory, tmp_path, sales.id)

    assert result.status == "synced", result.reasons  # not wedged by regional's dangling parent
    assert contexts.get_source(sales.id).current_snapshot.domain == "sales"


# --- Governed hierarchy: EMIT the contains edge into the served graph (hy-gh-230, SV17) ---


def _served_graph(session_factory, domain: str) -> dict:
    bundle = resolve_analytics_context(
        query="recognized revenue",
        directive=ContextDirective(domains=[domain], concepts=["recognized_revenue"]),
        session_factory=session_factory,
    )
    assert bundle.status == "governed", bundle.resolution
    return bundle.domain_graph


def _contains(graph: dict) -> list[dict]:
    return [edge for edge in graph["edges"] if edge["relation"] == "contains"]


@pytest.mark.postgres
def test_a_root_domain_that_parents_nothing_serves_no_contains_edge(
    session_factory, tmp_path, context_source
):
    """Every shipped playground context is a root that parents nothing: its served graph
    carries no `contains` edge and no extra `domain` node -- byte-identical but for the
    SCHEMA_VERSION bump. This is the guard that the emit did not disturb the common path."""
    source, _ = context_source
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"  # revenue, a root

    graph = _served_graph(session_factory, "revenue")
    assert _contains(graph) == []
    # The only `domain` node is revenue itself; no parent or child domain was invented.
    assert [n for n in graph["nodes"] if n["kind"] == "domain"] == [
        {"id": "domain:revenue", "kind": "domain", "label": "revenue"}
    ]


@pytest.mark.postgres
def test_a_parent_child_pair_serves_contains_edges_both_ways(
    session_factory, tmp_path, context_source
):
    """A declared `parent` reaches the served graph as one immediate `contains` edge each
    way: resolving the parent shows its child, resolving the child shows its parent, and
    both name the OTHER domain as a `domain` node keyed by governed slug (ADR-0031)."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"  # revenue root
    child = add_domain(repository, path_slug="regional", domain="regional", parent="revenue")
    regional = contexts.register_source(repository=str(repository), ref="main", path=child)
    assert run_sync(session_factory, tmp_path, regional.id).status == "synced"

    edge = {
        "from": "domain:revenue",
        "to": "domain:regional",
        "relation": "contains",
        "evidence": "git",
    }

    parent_graph = _served_graph(session_factory, "revenue")
    assert _contains(parent_graph) == [edge]  # the child, one level down
    assert {"id": "domain:regional", "kind": "domain", "label": "regional"} in parent_graph["nodes"]

    child_graph = _served_graph(session_factory, "regional")
    assert _contains(child_graph) == [edge]  # the SAME edge, seen from below
    assert {"id": "domain:revenue", "kind": "domain", "label": "revenue"} in child_graph["nodes"]


@pytest.mark.postgres
def test_a_transitive_unknown_ancestor_is_never_served(session_factory, tmp_path, context_source):
    """The whole-estate guard on the served path (the mayor's #352 requirement). A three
    level chain metro -> regional -> revenue syncs cleanly; then the ROOT revenue is
    disabled. metro's DIRECT parent regional is still enabled, so the per-sync
    `validate_domain` would clear it -- but metro's ancestor chain now dangles at revenue.
    `validate_forest` run at emit catches it, and metro serves NO `contains` edge: a
    dangling ancestor never reaches the served graph."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"  # revenue root
    regional_path = add_domain(
        repository, path_slug="regional", domain="regional", parent="revenue"
    )
    regional = contexts.register_source(repository=str(repository), ref="main", path=regional_path)
    assert run_sync(session_factory, tmp_path, regional.id).status == "synced"
    metro_path = add_domain(repository, path_slug="metro", domain="metro", parent="regional")
    metro = contexts.register_source(repository=str(repository), ref="main", path=metro_path)
    assert run_sync(session_factory, tmp_path, metro.id).status == "synced"

    # Disable the ROOT: metro's grandparent is gone, though its direct parent remains.
    contexts.set_enabled(source.id, enabled=False)

    # Fail-closed: neither the transitively-dangling child nor the directly-dangling one
    # serves an edge, and no phantom `domain:revenue` node leaks into either graph.
    assert _contains(_served_graph(session_factory, "metro")) == []
    assert _contains(_served_graph(session_factory, "regional")) == []
    assert not [
        n for n in _served_graph(session_factory, "metro")["nodes"] if n["id"] == "domain:revenue"
    ]


# --- Multi-domain resolve: lift multiple_domains, N independent authorities (#230 slice 3) ---


def _resolve_directive(session_factory, **kwargs):
    return resolve_analytics_context(
        query="recognized revenue",
        directive=ContextDirective(**kwargs),
        session_factory=session_factory,
    )


def _two_governed_domains(session_factory, tmp_path, context_source):
    """revenue (the fixture) plus a second governed domain `marketing`, cloned from it
    so it declares the same `recognized_revenue` concept -- two independently governed
    domains in one estate."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"
    other = add_domain(repository, path_slug="marketing", domain="marketing")
    marketing = contexts.register_source(repository=str(repository), ref="main", path=other)
    assert run_sync(session_factory, tmp_path, marketing.id).status == "synced"


@pytest.mark.postgres
def test_a_directive_naming_two_domains_resolves_into_a_domains_envelope(
    session_factory, tmp_path, context_source
):
    """The refusal is lifted: two governed domains resolve into a `domains[]` envelope
    whose flat governed fields are the documented null/empty envelope (hy-cnto Fork-1)."""
    _two_governed_domains(session_factory, tmp_path, context_source)

    bundle = _resolve_directive(
        session_factory, domains=["revenue", "marketing"], concepts=["recognized_revenue"]
    )
    payload = bundle.to_dict()

    assert bundle.status == "governed"
    assert payload["context_authority"] is None  # authority is per-domain
    assert payload["instructions"] == {}
    assert payload["provenance_refs"] == []
    assert payload["domain_graph"] == {"nodes": [], "edges": []}
    assert len(payload["domains"]) == 2
    # Each entry is an independent governed answer with its OWN authority/commit/path.
    authorities = {entry["context_authority"]["path"] for entry in payload["domains"]}
    assert len(authorities) == 2  # no domain's authority bled into another's
    for entry in payload["domains"]:
        assert entry["context_authority"]["type"] == "git"
        assert entry["resolution"]["status"] in ("governed", "mixed")


@pytest.mark.postgres
def test_each_domains_entry_is_byte_identical_to_that_domains_solo_resolve(
    session_factory, tmp_path, context_source
):
    """No cross-contamination, proved by content identity: every `domains[]` entry has
    the SAME bundle_id as resolving that one domain alone."""
    _two_governed_domains(session_factory, tmp_path, context_source)

    solo_ids = {
        _resolve_directive(
            session_factory, domains=[domain], concepts=["recognized_revenue"]
        ).bundle_id
        for domain in ("revenue", "marketing")
    }
    multi = _resolve_directive(
        session_factory, domains=["revenue", "marketing"], concepts=["recognized_revenue"]
    )
    assert {entry["bundle_id"] for entry in multi.to_dict()["domains"]} == solo_ids


@pytest.mark.postgres
def test_a_single_named_domain_is_byte_identical_to_before(
    session_factory, tmp_path, context_source
):
    """N=1 additivity: one domain carries no `domains` key at all."""
    _two_governed_domains(session_factory, tmp_path, context_source)

    solo = _resolve_directive(session_factory, domains=["revenue"], concepts=["recognized_revenue"])
    assert solo.domains is None
    assert "domains" not in solo.to_dict()
    assert solo.status == "governed"
    assert solo.context_authority is not None  # a single domain keeps its flat authority


@pytest.mark.postgres
def test_multi_domain_coverage_is_by_union_not_per_domain(
    session_factory, tmp_path, context_source
):
    """A concept only ONE named domain declares still resolves BOTH domains (union
    coverage), and a concept NO named domain declares fails the whole request."""
    _two_governed_domains(session_factory, tmp_path, context_source)

    covered = _resolve_directive(
        session_factory, domains=["revenue", "marketing"], concepts=["recognized_revenue"]
    )
    assert covered.status == "governed" and len(covered.to_dict()["domains"]) == 2

    uncovered = _resolve_directive(
        session_factory, domains=["revenue", "marketing"], concepts=["nobody_declares_this"]
    )
    assert uncovered.status == "no_match"
    assert uncovered.domains is None
    assert uncovered.resolution["warnings"][0]["code"] == "domain_does_not_declare"


@pytest.mark.postgres
def test_the_multi_domain_envelope_bundle_id_is_deterministic(
    session_factory, tmp_path, context_source
):
    """`domains` is GOVERNED content in `_content()`, so the outer `bundle_id` must be
    stable across resolves of the same commit + directive -- a per-entry wall-clock
    `resolved_at` would break that, so entries omit it and share the envelope's."""
    _two_governed_domains(session_factory, tmp_path, context_source)

    first = _resolve_directive(
        session_factory, domains=["revenue", "marketing"], concepts=["recognized_revenue"]
    )
    second = _resolve_directive(
        session_factory, domains=["revenue", "marketing"], concepts=["recognized_revenue"]
    )
    assert first.bundle_id == second.bundle_id  # deterministic despite differing clocks
    assert first.to_dict()["resolved_at"] != second.to_dict()["resolved_at"]
    assert all("resolved_at" not in entry for entry in first.to_dict()["domains"])


@pytest.mark.postgres
def test_a_multi_domain_answer_over_budget_is_disclosed_not_dropped(
    session_factory, tmp_path, context_source
):
    """The per-domain governed answers are never dropped to fit a budget; an envelope
    over the `context_budget` discloses it with `over_context_budget`."""
    _two_governed_domains(session_factory, tmp_path, context_source)

    bundle = _resolve_directive(
        session_factory,
        domains=["revenue", "marketing"],
        concepts=["recognized_revenue"],
        context_budget=200,  # far below a two-domain governed answer
    )
    assert len(bundle.to_dict()["domains"]) == 2  # nothing governed dropped
    assert bundle.resolution["warnings"][0]["code"] == "over_context_budget"


# --- Composed multi-domain bundle: the cross-domain graph (#230 slice 5, hy-uaks) ---


@pytest.mark.postgres
def test_a_multi_domain_answer_composes_a_cross_domain_contains_graph(
    session_factory, tmp_path, context_source
):
    """A directive naming a parent and its child composes a DOMAIN-LEVEL graph: a node per
    resolved domain and the governed `contains` edge between them (evidence:git), in a
    top-level `composition` -- while the flat `domain_graph` stays empty and each domain's
    own authority/content stays in `domains[]` (no authority flattened)."""
    _three_level_chain(session_factory, tmp_path, context_source)  # revenue -> regional -> metro

    payload = _resolve_directive(
        session_factory, domains=["revenue", "regional"], concepts=["recognized_revenue"]
    ).to_dict()

    graph = payload["composition"]["graph"]
    # Node ORDER is asserted (a list, not a set): nodes feed `bundle_id`, so a stable
    # sorted order is what keeps the composed answer deterministic across resolves.
    assert [n["id"] for n in graph["nodes"]] == ["domain:regional", "domain:revenue"]
    assert all(n["kind"] == "domain" for n in graph["nodes"])  # domain-level only
    assert graph["edges"] == [
        {
            "from": "domain:revenue",
            "to": "domain:regional",
            "relation": "contains",
            "evidence": "git",
        }
    ]
    # metro was not resolved, so it is not composed in.
    assert "domain:metro" not in {n["id"] for n in graph["nodes"]}
    # The slice-3 null-envelope guard is UNRELAXED: the flat graph is empty, authority null.
    assert payload["domain_graph"] == {"nodes": [], "edges": []}
    assert payload["context_authority"] is None
    # Per-domain authority stays independent in domains[]: two distinct git authorities.
    assert len({e["context_authority"]["path"] for e in payload["domains"]}) == 2


@pytest.mark.postgres
def test_composition_has_no_edge_between_unrelated_domains(
    session_factory, tmp_path, context_source
):
    """Two governed roots with no declared relationship compose to two nodes and NO edge --
    Hyperset invents no cross-domain relationship."""
    _two_governed_domains(session_factory, tmp_path, context_source)  # revenue + marketing (roots)

    graph = _resolve_directive(
        session_factory, domains=["revenue", "marketing"], concepts=["recognized_revenue"]
    ).to_dict()["composition"]["graph"]
    assert {n["id"] for n in graph["nodes"]} == {"domain:revenue", "domain:marketing"}
    assert graph["edges"] == []


@pytest.mark.postgres
def test_a_single_domain_answer_has_no_composition(session_factory, tmp_path, context_source):
    """N=1 is byte-identical to before: no `domains`, no `composition`."""
    _two_governed_domains(session_factory, tmp_path, context_source)
    solo = _resolve_directive(session_factory, domains=["revenue"], concepts=["recognized_revenue"])
    assert solo.composition is None
    assert "composition" not in solo.to_dict()


# --- Governed progressive expansion: bounded navigation over contains (#230 slice 4) ---


def _three_level_chain(session_factory, tmp_path, context_source):
    """revenue (root) -> regional -> metro, each cloned from the revenue fixture so it
    declares recognized_revenue. Three governed domains in one contains chain."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"
    for slug, parent in (("regional", "revenue"), ("metro", "regional")):
        path = add_domain(repository, path_slug=slug, domain=slug, parent=parent)
        sid = contexts.register_source(repository=str(repository), ref="main", path=path)
        assert run_sync(session_factory, tmp_path, sid.id).status == "synced"
    return contexts


def _expand(session_factory, domain, *, concepts=("recognized_revenue",), **bounds):
    from hyperset.bundle.expansion import expand_analytics_context

    return expand_analytics_context(
        query="what relates to this?",
        domain=domain,
        concepts=list(concepts),
        session_factory=session_factory,
        **bounds,
    )


@pytest.mark.postgres
def test_expansion_follows_the_governed_contains_chain_as_navigation(
    session_factory, tmp_path, context_source
):
    """From revenue, expansion reaches its whole contains subtree over evidence:git edges,
    labelled NAVIGATION and carrying no governed authority/instructions/evidence."""
    _three_level_chain(session_factory, tmp_path, context_source)

    result = _expand(session_factory, "revenue").to_dict()

    assert result["result_kind"] == "navigation"
    assert "context_authority" not in result and "instructions" not in result
    assert {d["domain"] for d in result["domains"]} == {"revenue", "regional", "metro"}
    assert all(d["available"] for d in result["domains"])
    assert {"relation", "evidence"} <= set(result["edges"][0])
    assert all(
        edge["relation"] == "contains" and edge["evidence"] == "git" for edge in result["edges"]
    )
    assert {(e["from"], e["to"]) for e in result["edges"]} == {
        ("domain:revenue", "domain:regional"),
        ("domain:regional", "domain:metro"),
    }


@pytest.mark.postgres
def test_expansion_max_hops_bounds_and_discloses(session_factory, tmp_path, context_source):
    """A bound drops the far domain and DISCLOSES it -- never a silent truncation."""
    _three_level_chain(session_factory, tmp_path, context_source)

    result = _expand(session_factory, "revenue", max_hops=1).to_dict()
    assert {d["domain"] for d in result["domains"]} == {"revenue", "regional"}  # metro dropped
    assert any(w["code"] == "expansion_bounded" for w in result["warnings"])


@pytest.mark.postgres
def test_expansion_refuses_an_unknown_or_uncovered_start(session_factory, tmp_path, context_source):
    _three_level_chain(session_factory, tmp_path, context_source)

    unknown = _expand(session_factory, "ghost").to_dict()
    assert unknown["domains"] == [] and unknown["edges"] == []
    assert unknown["warnings"][0]["code"] == "expansion_start_unknown"

    uncovered = _expand(session_factory, "revenue", concepts=["nobody_declares_this"]).to_dict()
    assert uncovered["warnings"][0]["code"] == "expansion_start_uncovered"


@pytest.mark.postgres
def test_expansion_max_components_caps_breadth_and_discloses(
    session_factory, tmp_path, context_source
):
    _three_level_chain(session_factory, tmp_path, context_source)
    result = _expand(session_factory, "revenue", max_components=2).to_dict()
    assert len(result["domains"]) == 2
    assert any(w["code"] == "expansion_bounded" for w in result["warnings"])


def _wide_star(session_factory, tmp_path, context_source, *, children=6):
    """revenue with N sibling children (a wide, shallow forest), so a byte budget can drop
    the far siblings and still return a non-empty partial that FITS."""
    source, repository = context_source
    contexts = PostgresContextRepository(session_factory)
    assert run_sync(session_factory, tmp_path, source.id).status == "synced"
    for i in range(children):
        slug = f"child{i}"
        path = add_domain(repository, path_slug=slug, domain=slug, parent="revenue")
        sid = contexts.register_source(repository=str(repository), ref="main", path=path)
        assert run_sync(session_factory, tmp_path, sid.id).status == "synced"


@pytest.mark.postgres
def test_expansion_over_budget_binds_the_result_never_returning_an_over_budget_graph(
    session_factory, tmp_path, context_source
):
    """The byte budget BINDS the result at every budget: a returned graph ALWAYS fits (it
    is never returned over-budget), a non-empty result is a valid partial, and somewhere
    in the range the far siblings are dropped to a strictly-smaller partial that still
    fits and discloses the drop."""
    from hyperset.bundle.expansion import MIN_CONTEXT_BUDGET
    from hyperset.transport.operations import serialize

    _wide_star(session_factory, tmp_path, context_source, children=8)
    full = _expand(session_factory, "revenue").to_dict()
    assert len(full["domains"]) == 9  # revenue + 8 children
    full_bytes = len(serialize(full).encode())

    saw_fitting_partial = False
    for budget in range(full_bytes, MIN_CONTEXT_BUDGET - 1, -60):
        result = _expand(session_factory, "revenue", context_budget=budget).to_dict()
        if result["domains"]:
            # INVARIANT: a served navigation graph never exceeds the budget it was given.
            assert len(serialize(result).encode()) <= budget
            if len(result["domains"]) < len(full["domains"]):
                # A real drop-to-fit partial: strictly smaller, fits, and discloses it.
                assert any(w["code"] == "expansion_over_context_budget" for w in result["warnings"])
                saw_fitting_partial = True
        else:
            # FAIL CLOSED, never a populated over-budget graph.
            assert result["warnings"][0]["code"] == "expansion_over_context_budget"
    assert saw_fitting_partial  # the drop-to-fit path is actually exercised


@pytest.mark.postgres
def test_expansion_fails_closed_when_even_the_start_cannot_fit_the_budget(
    session_factory, tmp_path, context_source
):
    """FAIL CLOSED: an impossibly small budget returns NO navigation graph -- an empty
    result carrying the over-budget code, never a populated over-budget one."""
    _three_level_chain(session_factory, tmp_path, context_source)
    with pytest.raises(ValueError, match="context_budget must be at least"):
        _expand(session_factory, "revenue", context_budget=10)


@pytest.mark.postgres
def test_expansion_discloses_an_unavailable_declared_neighbour(
    session_factory, tmp_path, context_source
):
    """A domain the estate DECLARES adjacent to a reached domain but that is disabled is
    DISCLOSED as unavailable, never traversed through: with the middle domain disabled,
    metro is unreachable (not entered) and regional is surfaced available:false with why,
    without hiding the governed start (Fork-4)."""
    contexts = _three_level_chain(session_factory, tmp_path, context_source)
    regional = next(s for s in contexts.list_sources() if s.current_snapshot.domain == "regional")
    contexts.set_enabled(regional.id, enabled=False)

    result = _expand(session_factory, "revenue").to_dict()
    by_domain = {d["domain"]: d for d in result["domains"]}
    assert by_domain["revenue"]["available"] is True  # the governed start is not hidden
    assert by_domain["regional"]["available"] is False  # the disabled neighbour is surfaced
    assert "metro" not in by_domain  # never traversed THROUGH the disabled middle
    assert result["edges"] == []  # no edge into an ungoverned domain
    unavailable = [w for w in result["warnings"] if w["code"] == "expansion_domain_unavailable"]
    assert unavailable and "regional" in unavailable[0]["message"]


@pytest.mark.postgres
def test_expansion_is_served_as_an_operation(session_factory, tmp_path, context_source):
    """The op is reachable through run_operation and returns the navigation shape."""
    from hyperset.transport.operations import run_operation

    _three_level_chain(session_factory, tmp_path, context_source)
    result = run_operation(
        "expand_analytics_context",
        {"query": "q", "domain": "revenue", "concepts": ["recognized_revenue"], "max_hops": 1},
        session_factory=session_factory,
    )
    assert result["result_kind"] == "navigation"
    assert {d["domain"] for d in result["domains"]} == {"revenue", "regional"}


@pytest.mark.postgres
def test_expansion_refuses_an_empty_concepts_claim(session_factory, tmp_path, context_source):
    """The coverage bar is real: `concepts` must be non-empty, so expansion cannot start
    from a governed domain with no claim (the hy-9lct 'say nothing, get an answer' path)."""
    from hyperset.transport.operations import OperationError, run_operation

    _three_level_chain(session_factory, tmp_path, context_source)
    with pytest.raises(OperationError) as caught:
        run_operation(
            "expand_analytics_context",
            {"query": "q", "domain": "revenue", "concepts": []},
            session_factory=session_factory,
        )
    assert caught.value.code == "invalid_params"
