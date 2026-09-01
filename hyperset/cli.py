"""Local v0 database, connection, connector, and Git-context commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from hyperset.bundle import ContextDirective, list_context_catalog, resolve_analytics_context
from hyperset.config import context_cache_dir
from hyperset.config.startup import apply_startup_config
from hyperset.connectors.build import build_connector
from hyperset.connectors.sync import run_sync
from hyperset.context.git import normalize_path
from hyperset.context.sync import sync_git_context
from hyperset.db.engine import create_session_factory, database_url_from_env, make_engine
from hyperset.ops.status import read_git_context, read_linked_evidence, read_sync_health
from hyperset.processor import run_sync_processing
from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresConnectorChangeRepository,
    PostgresContextRepository,
    PostgresSyncRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES
from hyperset.security.deployment import assert_network_bind_authenticated
from hyperset.transport.http import DEFAULT_HOST, DEFAULT_PORT, serve_http
from hyperset.transport.mcp import (
    DEFAULT_HTTP_HOST as MCP_DEFAULT_HTTP_HOST,
)
from hyperset.transport.mcp import (
    DEFAULT_HTTP_PORT as MCP_DEFAULT_HTTP_PORT,
)
from hyperset.transport.mcp import (
    serve_stdio,
    serve_streamable_http,
)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "db" / "migrations"


def _cache_dir() -> Path:
    # Fetch-only mirrors of configured context repositories, overridable so the container image
    # and a developer's shell keep their caches apart (the cache holds nothing authoritative).
    # Migrated to the settings object (hy-tc4o): the loaded `context.cache_dir`, else the legacy
    # env, else the home-based default -- the same value as before across set/unset/default.
    return Path(context_cache_dir())


def _alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.cmd_opts = argparse.Namespace(x=[f"db_url={database_url}"])
    return cfg


def cmd_db_upgrade(args: argparse.Namespace) -> int:
    command.upgrade(_alembic_config(database_url_from_env()), "head")
    print("Database upgraded to head.")
    return 0


def cmd_db_downgrade(args: argparse.Namespace) -> int:
    command.downgrade(_alembic_config(database_url_from_env()), args.revision)
    print(f"Database downgraded to {args.revision}.")
    return 0


def cmd_db_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        print("Refusing to reset without --yes (this drops all data).", file=sys.stderr)
        return 1
    cfg = _alembic_config(database_url_from_env())
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    print("Database reset: all data dropped, schema recreated at head.")
    return 0


def cmd_connections_create_superset_bundle(args: argparse.Namespace) -> int:
    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)
    repo = PostgresConnectionRepository(session_factory)
    # Bundle mode has no credential to encrypt -- the local export path is
    # not a secret, so it's stored in config_ref (the "external reference"
    # field), never config_encrypted.
    connection = repo.create_or_update(
        connector_type="superset", display_name=args.display_name, config_ref=args.path
    )
    print(connection.id)
    return 0


def cmd_connections_create_superset_rest(args: argparse.Namespace) -> int:
    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)
    repo = PostgresConnectionRepository(session_factory)
    # The base URL is configuration, not a secret, so it lives in
    # config_ref. Credentials are never stored here at all: `sync run`
    # reads them from the environment for the life of one sync.
    connection = repo.create_or_update(
        connector_type="superset", display_name=args.display_name, config_ref=args.base_url
    )
    print(connection.id)
    return 0


def cmd_connections_create_datahub(args: argparse.Namespace) -> int:
    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)
    repo = PostgresConnectionRepository(session_factory)
    # Same rule as the Superset REST connection: the base URL is
    # configuration and lives in config_ref, while the GMS token (if the
    # instance requires one) is read from the environment for the life of
    # one sync and never stored on the connection.
    connection = repo.create_or_update(
        connector_type="datahub", display_name=args.display_name, config_ref=args.base_url
    )
    print(connection.id)
    return 0


def cmd_connections_list(args: argparse.Namespace) -> int:
    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)
    # A local operator CLI legitimately spans every tenant: an EXPLICIT system opt-in,
    # never the silent global default (hq-t6nx).
    for connection in PostgresConnectionRepository(session_factory).list(workspace=ALL_WORKSPACES):
        print(f"{connection.id}\t{connection.connector_type}\t{connection.display_name}")
    return 0


def cmd_sync_run(args: argparse.Namespace) -> int:
    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)
    try:
        connection = PostgresConnectionRepository(session_factory).get(args.connection_id)
    except NotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    source = args.bundle_path or connection.config_ref
    if not source:
        print(
            "no source configured: pass --bundle-path, or register one via "
            "`hyperset connections create-superset-bundle --path ...` / "
            "`hyperset connections create-superset-rest --base-url ...` / "
            "`hyperset connections create-datahub --base-url ...`",
            file=sys.stderr,
        )
        return 1

    # The ONE connector-build path (hq-jedd): CLI sync and the admin probe construct
    # connectors the same way, so they cannot disagree on how a connection is built.
    try:
        connector = build_connector(connection.connector_type, source)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    result = run_sync(
        connector=connector, connection_id=connection.id, session_factory=session_factory
    )
    # Read the change events back out of Postgres instead of reporting the
    # in-memory tally, so the printed line is evidence of what was persisted.
    changes = PostgresConnectorChangeRepository(session_factory).list_for_run(result.sync_run_id)
    print(
        f"sync_run_id={result.sync_run_id} transport={result.transport} "
        f"source_version={result.source_version} counters={result.counters} "
        f"changes={len(changes)}"
    )
    for warning in result.warnings:
        print(f"  warning: {warning}")

    return 0


def cmd_sync_latest(args: argparse.Namespace) -> int:
    """Print the id of the most-recent COMPLETED sync run across all connections,
    or nothing when none has finished (hy-jp0gq).

    The deterministic source for the generic `make process`: it captures this id
    and runs `process sync <id>`, or no-ops when the output is empty. Prints only
    the bare id so a Makefile can capture it directly, matching how the demo's
    `sync run` line is read. Exit 0 either way -- "no completed sync yet" is a
    state to report, not a failure.
    """
    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)
    run = PostgresSyncRepository(session_factory).latest_finished_run_any()
    if run is not None:
        print(run.id)
    return 0


def cmd_process_sync(args: argparse.Namespace) -> int:
    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)
    result = run_sync_processing(sync_run_id=args.sync_run_id, session_factory=session_factory)
    if result.status == "already_running":
        print(f"another worker is already processing sync run {args.sync_run_id}", file=sys.stderr)
        return 1

    print(
        f"processor_run_id={result.processor_run_id} status={result.status} "
        f"counters={result.counters}"
    )
    for warning in result.warnings:
        print(f"  warning: {warning}")
    for finding in result.findings:
        print(f"  {finding.severity} {finding.finding_type} [{finding.id}] {finding.explanation}")
        # Marked as machine-generated wherever it is shown: nothing here has
        # been decided, and Hyperset cannot apply any of it.
        print(f"    proposal (not applied): {json.dumps(finding.proposed_action)}")
    return 0


def _context_repository():
    engine = make_engine(database_url_from_env())
    return PostgresContextRepository(create_session_factory(engine))


def cmd_context_add(args: argparse.Namespace) -> int:
    source = _context_repository().register_source(
        repository=args.repository,
        ref=args.ref,
        path=normalize_path(args.path),
        display_name=args.display_name,
    )
    print(source.id)
    return 0


def cmd_context_sync(args: argparse.Namespace) -> int:
    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)
    try:
        result = sync_git_context(
            source_id=args.source_id, session_factory=session_factory, cache_dir=_cache_dir()
        )
    except NotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(
        f"source_id={result.source_id} status={result.status} "
        f"commit={result.commit_sha} snapshot_id={result.snapshot_id}"
    )
    for reason in result.reasons:
        print(f"  rejected: {reason}", file=sys.stderr)
    for finding in result.findings:
        # The commit was recorded; this says what it could not be corroborated
        # against, so an operator reads a gap as a gap and not as a rejection.
        #
        # Dated, because the sentence is not. "matches no observed asset" and
        # "sync the superset connection to corroborate it" were computed when
        # the snapshot resolved and are replayed verbatim on every later run,
        # so on the unchanged path this line can be printed by a run that did
        # not compute it -- to an operator who just did what it asks. The
        # timestamp is what makes it a record of that moment rather than a
        # claim about this one (hy-5lgg). Clearing the gap needs a fresh commit
        # or hy-7ejr's reconcile.
        print(
            f"  uncorroborated [{finding['code']}] as of "
            f"{result.synced_at.isoformat()}: {finding['message']}",
            file=sys.stderr,
        )
    if not result.ok:
        # The previously valid snapshot keeps serving; say so, so an operator
        # does not read a failed sync as "context is gone".
        current = _context_repository().get_source(result.source_id).current_snapshot
        still = current.commit_sha if current else "none"
        print(f"  context unchanged; current commit remains {still}", file=sys.stderr)
        return 1
    return 0


def _print_source(source) -> None:
    current = source.current_snapshot
    print(f"{source.id}\t{source.repository}@{source.ref}:{source.path}")
    print(f"  current_commit={current.commit_sha if current else 'none'}")
    print(f"  last_attempt={source.last_attempt_status} at={source.last_attempt_at}")
    if source.last_attempted_commit_sha:
        print(f"  last_attempted_commit={source.last_attempted_commit_sha}")
    if source.last_error:
        print(f"  last_error={source.last_error}")


def cmd_context_status(args: argparse.Namespace) -> int:
    repository = _context_repository()
    try:
        sources = (
            [repository.get_source(args.source_id)] if args.source_id else repository.list_sources()
        )
    except NotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not sources:
        print("no Git context source configured; add one with `hyperset context add`")
        return 0
    for source in sources:
        _print_source(source)
    return 0


def cmd_context_show(args: argparse.Namespace) -> int:
    repository = _context_repository()
    try:
        source = repository.get_source(args.source_id)
    except NotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    snapshot = source.current_snapshot
    if snapshot is None:
        print("no context snapshot yet; run `hyperset context sync`", file=sys.stderr)
        return 1
    _print_source(source)
    print(f"  domain={snapshot.domain} title={snapshot.title}")
    print(f"  committed_at={snapshot.committed_at} content_sha256={snapshot.content_hash}")
    print(f"  owners={json.dumps(snapshot.owner_refs)}")
    print(f"  evidence={json.dumps(snapshot.evidence_refs)}")
    print(f"  uncorroborated={json.dumps(snapshot.evidence_findings)}")
    if args.files:
        # The original Git content, exactly as committed.
        for path, content in sorted(snapshot.files.items()):
            print(f"--- {path} ---")
            print(content)
    else:
        print(json.dumps(snapshot.normalized, indent=2, sort_keys=True))
    return 0


def cmd_context_history(args: argparse.Namespace) -> int:
    repository = _context_repository()
    try:
        snapshots = repository.history(args.source_id)
    except NotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for snapshot in snapshots:
        print(f"{snapshot.id}\t{snapshot.commit_sha}\t{snapshot.synced_at}")
    return 0


def cmd_context_disable(args: argparse.Namespace) -> int:
    """Soft-disable a source (hy-gh-282): the supported way to reconcile a domain
    collision or retire a source without hand-written SQL. It drops the source
    from catalog and resolve (both filter `enabled`) and keeps its history for a
    later `enable` or replay -- nothing is deleted (ADR 0012)."""
    repository = _context_repository()
    try:
        source = repository.get_source(args.source_id)
    except NotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    repository.set_enabled(args.source_id, enabled=False)
    domain = source.current_snapshot.domain if source.current_snapshot else "none"
    print(f"disabled {args.source_id} (domain={domain}); it no longer serves catalog or resolve")
    return 0


def cmd_context_enable(args: argparse.Namespace) -> int:
    """Re-enable a disabled source (hy-gh-282). Refuses if re-enabling would
    re-create a domain collision -- another enabled source already claims this
    source's domain -- so `enable` cannot silently re-introduce the ambiguity a
    disable resolved. The operator disables the other claimant first."""
    repository = _context_repository()
    try:
        source = repository.get_source(args.source_id)
    except NotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    snapshot = source.current_snapshot
    if snapshot is not None:
        claimant = repository.source_claiming_domain(
            snapshot.domain, exclude_source_id=args.source_id
        )
        if claimant is not None:
            print(
                f"cannot enable {args.source_id}: the domain {snapshot.domain!r} is already "
                f"claimed by enabled source {claimant.id} "
                f"({claimant.repository}@{claimant.ref}:{claimant.path} "
                f"commit {claimant.current_snapshot.commit_sha}). Disable that source first, "
                f"or change one manifest's domain",
                file=sys.stderr,
            )
            return 1
    repository.set_enabled(args.source_id, enabled=True)
    domain = snapshot.domain if snapshot else "none"
    print(f"enabled {args.source_id} (domain={domain})")
    return 0


def cmd_review_reconcile_sweep(args: argparse.Namespace) -> int:
    """Bounded reconcile SWEEP over open-PR review tasks (hq-3ta2): reconcile up to
    --limit tasks whose proposal PR is still open, oldest-first, each isolated. The
    same bounded, idempotent call the admin endpoint runs -- for an external
    scheduler (cron), not a daemon. It reads PR state and re-syncs a merged target's
    source; it never merges or approves (ADR 0012)."""
    from hyperset.flywheel import git_pr
    from hyperset.flywheel.lifecycle import SweepLimitError, reconcile_open_proposals

    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)
    try:
        summary = reconcile_open_proposals(
            session_factory=session_factory,
            cache_dir=_cache_dir(),
            read_pr_state=git_pr.read_pr_state,
            actor="cli",
            issuer=None,
            limit=args.limit,
        )
    except SweepLimitError as exc:
        # The SAME range the HTTP route enforces (hq-3ta2 #437 round 2): the CLI
        # rejects an out-of-range --limit rather than passing it through.
        print(str(exc), file=sys.stderr)
        return 1
    for task in summary["tasks"]:
        line = f"{task['task_id']}\t{task['state']}"
        if task.get("error"):
            line += f"\terror={task['error']}"
        print(line)
    print(f"reconciled {summary['count']} task(s) (limit {summary['limit']})")
    return 0


def cmd_bundle_catalog(args: argparse.Namespace) -> int:
    """The discovery surface, locally: what a planner would be given."""
    engine = make_engine(database_url_from_env())
    catalog = list_context_catalog(session_factory=create_session_factory(engine))
    print(json.dumps(catalog.to_dict(), indent=2, default=str))
    return 0


def cmd_bundle_resolve(args: argparse.Namespace) -> int:
    """The local adapter over the shared resolver. The HTTP and MCP adapters
    serialize the same object; none of them may reshape it."""
    engine = make_engine(database_url_from_env())
    try:
        directive = ContextDirective(
            domains=[args.domain] if args.domain else [],
            asset_refs=list(args.asset_ref or []),
            concepts=list(args.concept or []),
            max_hops=args.max_hops,
            context_budget=args.context_budget,
        )
    except ValueError as unpaired:
        print(str(unpaired), file=sys.stderr)
        return 1
    bundle = resolve_analytics_context(
        query=args.query,
        directive=directive,
        session_factory=create_session_factory(engine),
    )
    print(bundle.to_json(indent=2))
    # A no-match is a valid answer, not a failure -- but an operator piping
    # this into a script should be able to tell without parsing JSON.
    return 0 if bundle.status != "no_match" else 2


def _serving_session_factory():
    return create_session_factory(make_engine(database_url_from_env()))


def cmd_serve_http(args: argparse.Namespace) -> int:
    # WIRE THE LAYERED CONFIG FIRST (hy-ax9w, ADR-0035 section 5): load + validate the config,
    # fail closed on an unsafe bind/auth combination, and derive HYPERSET_AUTHZ_ENABLED /
    # HYPERSET_OIDC_* from `auth.*` so the gate below sees the config-configured verifier. A
    # config that would expose a non-loopback API without a verifier aborts here.
    apply_startup_config()
    # FAIL CLOSED before binding: a non-loopback listener must be authenticated
    # (hy-71mi, ADR-0035 section 5). This protects EVERY route -- including the
    # write-back-config and propose admin WRITES -- from answering unauthenticated on a
    # network bind.
    assert_network_bind_authenticated(args.host, surface="serve http", env=os.environ)
    serve_http(session_factory=_serving_session_factory(), host=args.host, port=args.port)
    return 0


def cmd_serve_mcp(args: argparse.Namespace) -> int:
    # The same config wiring as the HTTP listener: MCP over HTTP is a network surface, and the
    # stdio transport still runs the same authz-gated operations, so both consume the ONE
    # validated config + auth env (hy-ax9w).
    apply_startup_config()
    if getattr(args, "http", False):
        # A hosted, connectable endpoint at http://<host>:<port>/mcp for
        # testing and demos (hy-gh-117). Requires the `mcp` extra. MCP over HTTP is a
        # network surface exactly as the HTTP API is, so the same fail-closed bind rule
        # applies (hy-71mi).
        assert_network_bind_authenticated(args.host, surface="serve mcp --http", env=os.environ)
        serve_streamable_http(
            session_factory=_serving_session_factory(), host=args.host, port=args.port
        )
        return 0
    # Stdout is the protocol stream from here on; the server writes nothing
    # else to it.
    serve_stdio(session_factory=_serving_session_factory())
    return 0


def cmd_evals_score(args: argparse.Namespace) -> int:
    """GitHub #25's "exits nonzero on a critical governed-arm failure".

    Imported inside the command because the benchmark reads the case fixture
    and the committed recordings at import time, and none of that belongs to a
    customer running `hyperset serve`.

    The RAW arm failing a critical predicate is the measurement rather than a
    build failure: a baseline with no governed context is expected to miss
    predicates the governed arm passes, and exiting nonzero on that would make
    the comparison unrunnable.
    """
    from hyperset.evals.report import failed, render, score_recordings

    report = score_recordings()
    print(render(report))
    return 1 if failed(report) else 0


def cmd_eval_run(args: argparse.Namespace) -> int:
    """Run an Inspect AI eval of a CUSTOMER's own cases + recorded runs, scored by
    the governed predicates (hy-myn6). Nothing is bundled: `--cases` and
    `--recordings` are the customer's.

    Imported inside the command so the optional `inspect_ai` extra is required only
    when a customer opts into the eval tool -- a core `hyperset serve` install
    acquires no eval dependency. The testing model defaults to `gpt-5.6-luna`
    (`DEFAULT_TESTING_MODEL`); scoring recorded runs is deterministic and does not
    call it.
    """
    from pathlib import Path

    from inspect_ai import eval as inspect_eval

    from hyperset.eval.runner import DEFAULT_TESTING_MODEL, customer_eval_task

    model = args.model or DEFAULT_TESTING_MODEL
    task = customer_eval_task(Path(args.cases), recordings_dir=Path(args.recordings), model=model)
    logs = inspect_eval(task, model=model)
    return 0 if all(log.status == "success" for log in logs) else 1


def cmd_ops_status(args: argparse.Namespace) -> int:
    """The read-only operator view (hy-gh-72): sync health (S1) and Git context
    pin + last-attempt health (S2). A READ, so it always exits 0 -- it surfaces a
    failing sync or a stale pin, it does not turn one into a nonzero gate."""
    engine = make_engine(database_url_from_env())
    session_factory = create_session_factory(engine)

    print("SYNC HEALTH")
    health = read_sync_health(session_factory)
    if not health:
        print("  no connections configured; add one with `hyperset connections create-...`")
    for connection in health:
        print(
            f"  {connection.connection_id}\t{connection.connector_type}\t"
            f"{connection.display_name}\tenabled={connection.enabled}\t"
            f"last_sync={connection.outcome}"
        )
        run = connection.last_sync
        if run is not None:
            print(f"    finished_at={run.finished_at} counters={json.dumps(run.counters)}")
            if run.errors:
                print(f"    errors={json.dumps(run.errors)}")

    print("GIT CONTEXT")
    sources = read_git_context(session_factory)
    if not sources:
        print("  no Git context source configured; add one with `hyperset context add`")
    for source in sources:
        print(
            f"  {source.source_id}\t{source.repository}@{source.ref}:{source.path}\t"
            f"enabled={source.enabled}\tpinned={source.pinned}"
        )
        if source.pinned:
            print(
                f"    commit={source.commit_sha} snapshot={source.snapshot_id} "
                f"content_sha256={source.content_hash}"
            )
            print(
                f"    domain={source.domain} title={source.title} "
                f"committed_at={source.committed_at}"
            )
        else:
            print("    never pinned; run `hyperset context sync`")
        print(f"    last_attempt={source.last_attempt_status} at={source.last_attempt_at}")
        if source.last_attempted_commit_sha:
            print(f"    last_attempted_commit={source.last_attempted_commit_sha}")
        if source.last_error:
            print(f"    last_error={source.last_error}")

    print("LINKED EVIDENCE")
    evidence = read_linked_evidence(session_factory)
    if not evidence:
        print("  no governed context pinned; nothing to link")
    for domain in evidence:
        print(
            f"  {domain.source_id}\tdomain={domain.domain}\t"
            f"observed_assets={len(domain.observed_assets)}\t"
            f"deprecations={len(domain.deprecations)}"
        )
        for asset in domain.observed_assets:
            # `linked_version_id` is the exact version Git pinned; `observed_version_id` is
            # the current one. Surfacing both lets the operator see drift -- they differ once
            # the source has moved past what the commit linked against (hy-hske).
            print(
                f"    {asset['ref']}\tconnector={asset['connector']}\t"
                f"observed_version={asset['observed_version']}\t"
                f"observed_version_id={asset['observed_version_id']}\t"
                f"content_sha256={asset['content_sha256']}\t"
                f"linked_version_id={asset['linked_version_id']}\t"
                f"governance={asset['governance']}"
            )
        for item in domain.deprecations:
            print(f"    DEPRECATED {item.get('ref')}\tkind={item.get('kind')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hyperset")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser("db", help="Database migration and seed commands")
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)

    db_subparsers.add_parser("upgrade", help="Upgrade the database to head").set_defaults(
        func=cmd_db_upgrade
    )

    downgrade_parser = db_subparsers.add_parser("downgrade", help="Downgrade the database")
    downgrade_parser.add_argument("revision", nargs="?", default="-1")
    downgrade_parser.set_defaults(func=cmd_db_downgrade)

    reset_parser = db_subparsers.add_parser("reset", help="Drop and recreate all tables")
    reset_parser.add_argument("--yes", action="store_true", help="Required to actually reset")
    reset_parser.set_defaults(func=cmd_db_reset)

    connections_parser = subparsers.add_parser("connections", help="Connection management")
    connections_subparsers = connections_parser.add_subparsers(
        dest="connections_command", required=True
    )

    create_bundle_parser = connections_subparsers.add_parser(
        "create-superset-bundle", help="Register a bundle-mode Superset connection"
    )
    create_bundle_parser.add_argument("--path", required=True, help="Path to a Superset export ZIP")
    create_bundle_parser.add_argument(
        "--display-name", dest="display_name", default="Superset (bundle)"
    )
    create_bundle_parser.set_defaults(func=cmd_connections_create_superset_bundle)

    create_rest_parser = connections_subparsers.add_parser(
        "create-superset-rest", help="Register a live REST Superset connection"
    )
    create_rest_parser.add_argument(
        "--base-url",
        dest="base_url",
        required=True,
        help="Superset base URL, e.g. http://superset:8088",
    )
    create_rest_parser.add_argument(
        "--display-name", dest="display_name", default="Superset (live REST)"
    )
    create_rest_parser.set_defaults(func=cmd_connections_create_superset_rest)

    create_datahub_parser = connections_subparsers.add_parser(
        "create-datahub", help="Register a live GraphQL DataHub connection"
    )
    create_datahub_parser.add_argument(
        "--base-url",
        dest="base_url",
        required=True,
        help="DataHub GMS base URL, e.g. http://datahub-gms:8080",
    )
    create_datahub_parser.add_argument(
        "--display-name", dest="display_name", default="DataHub (live GraphQL)"
    )
    create_datahub_parser.set_defaults(func=cmd_connections_create_datahub)

    connections_subparsers.add_parser("list", help="List connections").set_defaults(
        func=cmd_connections_list
    )

    context_parser = subparsers.add_parser(
        "context", help="Git-owned context source commands (read-only with respect to Git)"
    )
    context_subparsers = context_parser.add_subparsers(dest="context_command", required=True)

    context_add_parser = context_subparsers.add_parser(
        "add", help="Configure one authoritative Git repository/ref/path"
    )
    context_add_parser.add_argument(
        "--repository", required=True, help="Git URL or local path, e.g. https://host/org/repo.git"
    )
    context_add_parser.add_argument("--ref", default="main", help="Branch or tag, e.g. main")
    context_add_parser.add_argument(
        "--path",
        required=True,
        help="Context directory in the repository, e.g. playground/examples/revenue",
    )
    context_add_parser.add_argument("--display-name", dest="display_name", default=None)
    context_add_parser.set_defaults(func=cmd_context_add)

    context_sync_parser = context_subparsers.add_parser(
        "sync", help="Read the configured ref and snapshot the exact commit"
    )
    context_sync_parser.add_argument("source_id")
    context_sync_parser.set_defaults(func=cmd_context_sync)

    context_status_parser = context_subparsers.add_parser(
        "status", help="Git context sync health and current commit"
    )
    context_status_parser.add_argument("source_id", nargs="?", default=None)
    context_status_parser.set_defaults(func=cmd_context_status)

    context_show_parser = context_subparsers.add_parser(
        "show", help="Current context contents, owners, evidence, and provenance"
    )
    context_show_parser.add_argument("source_id")
    context_show_parser.add_argument(
        "--files",
        action="store_true",
        help="Print the original Git files instead of the normalized representation",
    )
    context_show_parser.set_defaults(func=cmd_context_show)

    context_history_parser = context_subparsers.add_parser(
        "history", help="Every snapshotted commit, oldest first"
    )
    context_history_parser.add_argument("source_id")
    context_history_parser.set_defaults(func=cmd_context_history)

    context_disable_parser = context_subparsers.add_parser(
        "disable",
        help="Stop a source serving catalog/resolve, keeping its history (hy-gh-282)",
    )
    context_disable_parser.add_argument("source_id")
    context_disable_parser.set_defaults(func=cmd_context_disable)

    context_enable_parser = context_subparsers.add_parser(
        "enable",
        help="Re-enable a disabled source, unless it would re-create a domain collision",
    )
    context_enable_parser.add_argument("source_id")
    context_enable_parser.set_defaults(func=cmd_context_enable)

    review_parser = subparsers.add_parser("review", help="Interactive review commands")
    review_subparsers = review_parser.add_subparsers(dest="review_command", required=True)
    reconcile_sweep_parser = review_subparsers.add_parser(
        "reconcile-sweep",
        help="Reconcile up to --limit open-PR proposals (bounded, idempotent; for cron)",
    )
    reconcile_sweep_parser.add_argument(
        "--limit", type=int, default=50, help="Maximum tasks to reconcile this sweep (oldest-first)"
    )
    reconcile_sweep_parser.set_defaults(func=cmd_review_reconcile_sweep)

    sync_parser = subparsers.add_parser("sync", help="Connector sync commands")
    sync_subparsers = sync_parser.add_subparsers(dest="sync_command", required=True)

    sync_run_parser = sync_subparsers.add_parser("run", help="Run one sync against a connection")
    sync_run_parser.add_argument("connection_id")
    sync_run_parser.add_argument(
        "--bundle-path",
        dest="bundle_path",
        default=None,
        help="Override the connection's stored bundle path or base URL",
    )
    sync_run_parser.set_defaults(func=cmd_sync_run)

    sync_subparsers.add_parser(
        "latest",
        help="Print the id of the most-recent completed sync run (empty if none)",
    ).set_defaults(func=cmd_sync_latest)

    process_parser = subparsers.add_parser(
        "process", help="Offline processor commands (deterministic rules, no approval)"
    )
    process_subparsers = process_parser.add_subparsers(dest="process_command", required=True)

    process_sync_parser = process_subparsers.add_parser(
        "sync", help="Run the deterministic rules over one completed sync run"
    )
    process_sync_parser.add_argument("sync_run_id")
    process_sync_parser.set_defaults(func=cmd_process_sync)

    bundle_parser = subparsers.add_parser(
        "bundle", help="Resolve the public ContextBundle (read-only)"
    )
    bundle_subparsers = bundle_parser.add_subparsers(dest="bundle_command", required=True)

    bundle_resolve_parser = bundle_subparsers.add_parser(
        "resolve", help="Compile one ContextBundle for what a directive names"
    )
    bundle_subparsers.add_parser(
        "catalog", help="List the domains, concepts, and source kinds a planner can choose from"
    ).set_defaults(func=cmd_bundle_catalog)

    bundle_resolve_parser.add_argument("--query", required=True, help="The analytics question")
    bundle_resolve_parser.add_argument(
        "--domain", default=None, help="Retrieve one configured domain, e.g. revenue"
    )
    bundle_resolve_parser.add_argument(
        "--concept",
        dest="concept",
        action="append",
        default=None,
        help=(
            "A concept term the domain must declare, as the catalog lists it; repeatable. "
            "Required with --domain: governed context is not served on an unstated claim"
        ),
    )
    bundle_resolve_parser.add_argument(
        "--asset-ref",
        dest="asset_ref",
        action="append",
        default=None,
        help=(
            "Retrieve this exact ref; repeatable. A ref outside the domain comes back as "
            "observed-only evidence"
        ),
    )
    bundle_resolve_parser.add_argument(
        "--max-hops",
        dest="max_hops",
        type=int,
        default=None,
        help="Bound domain_graph to this many hops from the domain node",
    )
    bundle_resolve_parser.add_argument(
        "--context-budget",
        dest="context_budget",
        type=int,
        default=None,
        help="Byte ceiling for the answer; omissions are disclosed in resolution.warnings",
    )
    bundle_resolve_parser.set_defaults(func=cmd_bundle_resolve)

    serve_parser = subparsers.add_parser(
        "serve", help="Serve the agent-facing operations (read-only)"
    )
    serve_subparsers = serve_parser.add_subparsers(dest="serve_command", required=True)

    serve_http_parser = serve_subparsers.add_parser(
        "http", help="Serve the agent-facing v0 operations over HTTP"
    )
    serve_http_parser.add_argument("--host", default=DEFAULT_HOST)
    serve_http_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve_http_parser.set_defaults(func=cmd_serve_http)

    serve_mcp_parser = serve_subparsers.add_parser(
        "mcp", help="Serve the same operations over MCP (stdio, or Streamable HTTP with --http)"
    )
    serve_mcp_parser.add_argument(
        "--http",
        action="store_true",
        help="Serve a hosted Streamable HTTP endpoint at http://<host>:<port>/mcp "
        "instead of stdio (requires the 'mcp' extra)",
    )
    serve_mcp_parser.add_argument("--host", default=MCP_DEFAULT_HTTP_HOST)
    serve_mcp_parser.add_argument("--port", type=int, default=MCP_DEFAULT_HTTP_PORT)
    serve_mcp_parser.set_defaults(func=cmd_serve_mcp)

    evals_parser = subparsers.add_parser(
        "evals", help="Score the committed benchmark recordings (no model runs)"
    )
    evals_subparsers = evals_parser.add_subparsers(dest="evals_command", required=True)
    eval_parser = subparsers.add_parser(
        "eval",
        help="Run an Inspect AI eval of YOUR own cases + recorded runs (optional 'evals' extra)",
    )
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_run_parser = eval_subparsers.add_parser(
        "run", help="Score recorded runs of your cases with the governed predicates"
    )
    eval_run_parser.add_argument(
        "--cases", required=True, help="Directory holding your case suite (*.yaml)"
    )
    eval_run_parser.add_argument(
        "--recordings", required=True, help="Directory holding the recorded runs to score"
    )
    eval_run_parser.add_argument(
        "--model",
        default=None,
        help="Inspect eval model (default: gpt-5.6-luna); scoring recorded runs does not call it",
    )
    eval_run_parser.set_defaults(func=cmd_eval_run)

    evals_subparsers.add_parser(
        "score",
        help=(
            "Score every committed arm recording with the deterministic scorers and exit "
            "nonzero on a critical governed-arm failure. Scores a recording, not a live run "
            "(ADR 0013)."
        ),
    ).set_defaults(func=cmd_evals_score)

    ops_parser = subparsers.add_parser(
        "ops", help="Read-only operator view of sync health and governed state (hy-gh-72)"
    )
    ops_subparsers = ops_parser.add_subparsers(dest="ops_command", required=True)
    ops_subparsers.add_parser(
        "status", help="Sync health per connection: last outcome, when, and counters"
    ).set_defaults(func=cmd_ops_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
