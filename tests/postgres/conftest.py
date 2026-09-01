"""Postgres-backed repository test infrastructure.

Every test in this directory needs a real Postgres instance (hy-gh-26
"Required tests: repository contract tests against real Postgres"). A
session-scoped fixture starts one ephemeral `postgres:16` container via the
`docker` CLI directly -- no docker-compose dependency, because a bare
container is enough for repository contract tests
-- runs migrations against it once, and each individual test gets its own
rolled-back-after-use transaction (`join_transaction_mode=
"create_savepoint"`, the standard SQLAlchemy 2.0 test-isolation pattern),
so tests don't interfere with each other without truncating tables between
runs.

Skips the whole directory (not each test individually, and not the rest of
the suite) if Docker isn't reachable, so `pytest tests/unit` and any CI
runner without Docker are unaffected.

The container is named per session, never by a shared constant (hy-rm3):
several worktrees run this suite at once, and a fixed name meant each new
session force-removed the container the others were still querying, so a
healthy branch failed with over a hundred connection errors caused entirely
by a neighbour. Only this session's own container is ever removed.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import time
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from hyperset.db.base import Base
from hyperset.db.engine import make_engine
from tests.docker_probe import _docker_available

_IMAGE = "postgres:16"
_CONTAINER_PREFIX = "hyperset-pg-test"
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "hyperset" / "db" / "migrations"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("", 0))
        return sock.getsockname()[1]


def _container_name(pid: int, port: int) -> str:
    """`<prefix>-<pid>-<port>`: unique per session, and readable enough that
    `docker ps` says which pytest run owns a container. The pid is what makes
    an abandoned container identifiable later."""
    return f"{_CONTAINER_PREFIX}-{pid}-{port}"


def _owner_pid(name: str) -> int | None:
    prefix, _, rest = name.partition(f"{_CONTAINER_PREFIX}-")
    if prefix or not rest:
        return None
    pid, _, port = rest.partition("-")
    if not (pid.isdigit() and port.isdigit()):
        return None
    return int(pid)


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Someone else's process, so the pid is taken: not ours to reap.
        return True
    return True


def _abandoned_containers(names: list[str], *, is_alive=_process_is_alive) -> list[str]:
    """Containers from earlier sessions whose pytest process no longer exists.

    A killed session (`ctrl-c`, a crashed agent) leaves its container running,
    and unique names mean nothing reclaims it. Reaping by dead owner keeps
    that cleanup without ever touching a container a live session is using --
    a reused pid reads as alive, which only ever leaves a container behind."""
    return [name for name in names if (pid := _owner_pid(name)) is not None and not is_alive(pid)]


def _list_test_containers() -> list[str]:
    """The containers this suite has ever named, or a refusal.

    The return code is read because an empty listing is not self-describing
    (hy-9vb3): a `docker ps` that could not run writes nothing to stdout, and
    an unread rc turns that into "there are no abandoned containers". Reaping
    then silently does nothing, forever, and the leak this whole naming scheme
    exists to bound (hy-rm3) comes back with no symptom at the point of
    failure -- a fresh unique name is used regardless, so the session passes
    and only the container count grows.
    """
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", f"name={_CONTAINER_PREFIX}-", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"`docker ps` exited {result.returncode}, so the abandoned-container reaping "
            f"cannot run; an empty listing here would read as 'nothing to reap': "
            f"{result.stderr.strip()}"
        )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture(scope="session")
def postgres_dsn():
    if not _docker_available():
        pytest.skip("Docker is not available; skipping Postgres-backed tests")

    for abandoned in _abandoned_containers(_list_test_containers()):
        subprocess.run(["docker", "rm", "-f", abandoned], capture_output=True)

    port = _free_port()
    container = _container_name(os.getpid(), port)
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            container,
            "-p",
            f"{port}:5432",
            "-e",
            "POSTGRES_PASSWORD=hyperset",
            "-e",
            "POSTGRES_DB=hyperset",
            _IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    dsn = f"postgresql+psycopg://postgres:hyperset@localhost:{port}/hyperset"

    deadline = time.monotonic() + 30
    ready = False
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "exec", container, "pg_isready", "-U", "postgres"],
            capture_output=True,
        )
        if result.returncode == 0:
            # pg_isready only proves the container's internal socket is up.
            # The host-mapped port (what tests actually connect through) can
            # lag a moment behind on Docker Desktop's network proxy -- retry
            # a real connection over the mapped port before declaring ready,
            # or every other test in the session can hit a flaky refused
            # connection despite this loop having "succeeded".
            try:
                psycopg.connect(
                    host="localhost",
                    port=port,
                    user="postgres",
                    password="hyperset",
                    dbname="hyperset",
                ).close()
                ready = True
                break
            except psycopg.OperationalError:
                pass
        time.sleep(0.5)

    if not ready:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        pytest.fail("Postgres test container did not become ready in time")

    yield dsn

    subprocess.run(["docker", "rm", "-f", container], capture_output=True)


@pytest.fixture(scope="session")
def db_engine(postgres_dsn):
    engine = make_engine(postgres_dsn)
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.cmd_opts = argparse.Namespace(x=[f"db_url={postgres_dsn}"])
    command.upgrade(cfg, "head")
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    """Per-test sessionmaker bound to one connection wrapped in an outer
    transaction that's rolled back on teardown -- every `session.begin()`
    a repository issues becomes a SAVEPOINT instead of a real commit."""
    connection = db_engine.connect()
    trans = connection.begin()
    factory = sessionmaker(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    yield factory
    trans.rollback()
    connection.close()


@pytest.fixture
def committed_session_factory(db_engine):
    """A sessionmaker whose commits are real, for the few tests that need two
    connections at once: the shared `session_factory` keeps everything inside
    one connection's outer transaction, which a second connection cannot see,
    and savepoints on one connection cannot race each other.

    Isolation is by truncation on the way out instead of rollback, so nothing
    a concurrency test commits leaks into the next test."""
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        tables = ", ".join(table.name for table in Base.metadata.sorted_tables)
        with db_engine.begin() as connection:
            connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))


# --------------------------------------------------------------------------
# The canonical revenue slice: real pinned-Superset payloads, a real Git
# commit, and the observations the checked-in context declares. Shared here
# because both the processor (hy-gh-38) and the bundle (hy-gh-31) have to be
# proven against the *same* scenario, not two similar ones.
# --------------------------------------------------------------------------

APPROVED_DATASET = "ae48881d-334f-54a7-94e8-1ffcc73866e2"
GLOSSARY_TERM = "urn:li:glossaryTerm:recognized_revenue"
GIT_EXPRESSION = "SUM(gross_amount - tax_amount)"
DRIFTED_EXPRESSION = "SUM(gross_amount)"


def sync_superset(connection_id, session_factory, capture="baseline"):
    """One real sync of the pinned Superset 6.1.0 REST captures."""
    from hyperset.connectors import run_sync
    from hyperset.connectors.superset import SupersetConnector
    from tests.fake_superset import BASE_URL, FakeSupersetSession

    return run_sync(
        connector=SupersetConnector(
            base_url=BASE_URL,
            username="admin",
            password="s3cret",
            session=FakeSupersetSession(capture),
        ),
        connection_id=connection_id,
        session_factory=session_factory,
    )


def sync_revenue_context(session_factory, tmp_path):
    """Snapshot the checked-in revenue context from a real Git commit."""
    from hyperset.context.sync import sync_git_context
    from hyperset.repositories.postgres import PostgresContextRepository
    from tests.integration.test_git_context_source import CONTEXT_PATH, make_repository

    repository = make_repository(tmp_path)
    source = PostgresContextRepository(session_factory).register_source(
        repository=str(repository), ref="main", path=CONTEXT_PATH
    )
    result = sync_git_context(
        source_id=source.id, session_factory=session_factory, cache_dir=tmp_path / "cache"
    )
    assert result.status == "synced", result.reasons
    return result


def make_superset_connection(session_factory):
    from hyperset.repositories.postgres import PostgresConnectionRepository
    from tests.fake_superset import BASE_URL

    return (
        PostgresConnectionRepository(session_factory)
        .create_or_update(
            connector_type="superset", display_name="Local Superset (REST)", config_ref=BASE_URL
        )
        .id
    )


def observe_glossary_term(session_factory):
    """The one DataHub identity the revenue context refers to. Its Superset
    refs come from the real sync -- observing those here as well would make
    every ref ambiguous across two connections.

    The run is FINISHED, not merely begun: this models a DataHub sync that
    completed, and since hy-lcgq a run left in `running` means the opposite --
    a connector that has never read, whose absences are unmeasured rather than
    established. `run_sync` finishes its own run, so only hand-built
    observations like this one have to say so."""
    from hyperset.repositories.postgres import (
        PostgresConnectionRepository,
        PostgresObservedAssetRepository,
        PostgresSyncRepository,
    )

    syncs = PostgresSyncRepository(session_factory)
    datahub = PostgresConnectionRepository(session_factory).create_or_update(
        connector_type="datahub", display_name="DataHub"
    )
    run = syncs.begin_run(datahub.id, mode="full")
    PostgresObservedAssetRepository(session_factory).upsert(
        connection_id=datahub.id,
        external_id=GLOSSARY_TERM,
        asset_type="glossary_term",
        sync_run_id=run.id,
        raw_payload={"urn": GLOSSARY_TERM},
    )
    syncs.finish_run(run.id, counters={"created": 1})
    return datahub.id


def build_revenue_slice(session_factory, tmp_path):
    """Walking-skeleton steps 4-6: the sources are observed, the Git context
    is pinned, and its refs are linked to real observations.

    A function as well as a fixture because a concurrency test has to build
    the same slice through `committed_session_factory` instead."""
    connection_id = make_superset_connection(session_factory)
    observe_glossary_term(session_factory)
    baseline = sync_superset(connection_id, session_factory)
    context = sync_revenue_context(session_factory, tmp_path)
    return {
        "connection_id": connection_id,
        "baseline_sync_run_id": baseline.sync_run_id,
        "context": context,
    }


@pytest.fixture
def revenue_slice(session_factory, tmp_path):
    return build_revenue_slice(session_factory, tmp_path)


def build_git_before_evidence(session_factory, tmp_path):
    """`build_revenue_slice` with the two halves in the order real estates
    actually deliver them: the Git commit is read while no Superset connection
    exists at all (hy-gh-118).

    Named rather than inlined because the ORDER is the fixture -- the same
    rows in the other order are `build_revenue_slice`, and a test that builds
    it inline can silently stop being the out-of-order case when its setup is
    edited. `corroborate_superset` is the second half, so a test that wants
    the arrival can call it and one that wants the gap standing does not."""
    observe_glossary_term(session_factory)
    return {"context": sync_revenue_context(session_factory, tmp_path)}


def corroborate_superset(session_factory):
    """The evidence arriving, second: a real Superset sync after the fact."""
    return sync_superset(make_superset_connection(session_factory), session_factory)


def build_unread_superset(session_factory, *, outcome=None):
    """A Superset connection whose absences are unmeasured: it is configured
    and has read nothing.

    `outcome=None` leaves the run in `running` -- a connector mid-first-sync or
    one whose process died. `outcome="failed"` marks it failed, which is the
    connector-down case: a connection that HAS read before can still be behind
    now. Both are "come back later" rather than "the asset does not exist",
    and `finish_run` on the same id is how a test turns either into an estate
    that has genuinely been read (hy-lcgq)."""
    from hyperset.repositories.postgres import PostgresSyncRepository

    connection_id = make_superset_connection(session_factory)
    syncs = PostgresSyncRepository(session_factory)
    run = syncs.begin_run(connection_id, mode="full")
    if outcome == "failed":
        syncs.fail_run(run.id, errors=["connection refused"])
    return {"connection_id": connection_id, "sync_run_id": run.id}


def build_wide_context(session_factory, tmp_path, *, domains: int, entries: int):
    """A real Git context repository wide enough to trip the catalog's bounds
    (hy-5b1).

    The checked-in revenue fixture has single-digit lists and one domain, so
    it can prove that slicing works but never that the caps bind or that
    `page.next_offset` walks more than one page. This builds `domains`
    manifests, each declaring `entries` concepts, source refs, BI overrides and
    `entries` owners, from real commits through the real sync -- generated
    content, but not a stubbed repository.

    The Superset datasets are inserted before the manifests point at them as
    explicit BI overrides because an unobserved ref is uncorroborated and is not
    offered as a seed in the catalog -- the very lists whose bounds this is
    built to trip.
    """
    from hyperset.context.sync import sync_git_context
    from hyperset.repositories.postgres import (
        PostgresConnectionRepository,
        PostgresContextRepository,
        PostgresObservedAssetRepository,
        PostgresSyncRepository,
    )
    from tests.integration.test_git_context_source import git

    terms = [f"urn:li:glossaryTerm:wide_term_{index:03d}" for index in range(entries)]
    # A separate set, because a ref cannot be both cited evidence and
    # prohibited. There are as many prohibitions as concepts so that a bound
    # applied to them would be visible.
    prohibited = [f"urn:li:glossaryTerm:wide_banned_{index:03d}" for index in range(entries)]
    superset = PostgresConnectionRepository(session_factory).create_or_update(
        connector_type="superset", display_name="Superset"
    )
    run = PostgresSyncRepository(session_factory).begin_run(superset.id, mode="full")
    for term in terms + prohibited:
        PostgresObservedAssetRepository(session_factory).upsert(
            connection_id=superset.id,
            external_id=term,
            asset_type="dataset",
            sync_run_id=run.id,
            raw_payload={"uuid": term},
        )

    repository = tmp_path / "wide-repo"
    paths = [f"domains/wide_{index:03d}" for index in range(domains)]
    for index, path in enumerate(paths):
        directory = repository / path
        directory.mkdir(parents=True)
        (directory / "context.md").write_text(f"# wide domain {index}\n")
        (directory / "evals.yaml").write_text(
            "schema_version: 1\n"
            "cases:\n"
            f"  - name: wide_case_{index:03d}\n"
            f"    question: What does wide domain {index} govern?\n"
            "    expected:\n"
            "      grain: generated\n"
        )
        (directory / "manifest.yaml").write_text(
            _wide_manifest(index=index, terms=terms, prohibited=prohibited, owners=entries)
        )
    git("init", "--quiet", "--initial-branch=main", ".", cwd=repository)
    git("config", "user.email", "context@example.test", cwd=repository)
    git("config", "user.name", "Context Owner", cwd=repository)
    git("add", "-A", cwd=repository)
    git("commit", "--quiet", "-m", "add wide context", cwd=repository)

    context_repository = PostgresContextRepository(session_factory)
    for path in paths:
        source = context_repository.register_source(
            repository=str(repository), ref="main", path=path
        )
        result = sync_git_context(
            source_id=source.id, session_factory=session_factory, cache_dir=tmp_path / "wide-cache"
        )
        assert result.status == "synced", result.reasons
    return {
        "domains": domains,
        "entries": entries,
        "terms": terms,
        "prohibited": prohibited,
    }


def _wide_manifest(*, index: int, terms: list[str], prohibited: list[str], owners: int) -> str:
    import yaml

    return yaml.safe_dump(
        {
            "schema_version": 1,
            "domain": f"wide_{index:03d}",
            "title": f"Wide domain {index}",
            "owners": [f"team:wide-owner-{owner:03d}" for owner in range(owners)],
            "context_doc": "context.md",
            "evals": "evals.yaml",
            "definitions": [
                {
                    "term": f"wide_term_{position:03d}",
                    "statement": f"Generated definition {position} for bound testing.",
                }
                for position, _term in enumerate(terms)
            ],
            "approved_sources": [
                {
                    "ref": f"table:postgres:wide.source_{position:03d}",
                    "role": "source",
                    "bi_override": {
                        "ref": f"superset:dataset:{term}",
                        "reason": "Generated explicit BI override for bound testing.",
                    },
                }
                for position, term in enumerate(terms)
            ],
            "prohibited_sources": [
                {
                    "ref": f"table:postgres:wide.banned_{position:03d}",
                    "reason": f"Generated prohibition {position} for bound testing.",
                    "bi_override": {
                        "ref": f"superset:dataset:{term}",
                        "reason": "Generated explicit BI override for bound testing.",
                    },
                }
                for position, term in enumerate(prohibited)
            ],
            "grain": "generated",
        },
        sort_keys=False,
    )


@pytest.fixture
def wide_context(session_factory, tmp_path):
    """Two pages of domains and lists past `INNER_LIMIT`, so the bounds bind."""
    from hyperset.bundle import CATALOG_INNER_LIMIT

    return build_wide_context(session_factory, tmp_path, domains=3, entries=CATALOG_INNER_LIMIT + 2)
