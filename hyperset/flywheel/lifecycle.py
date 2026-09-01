"""Reconcile a proposal's PR lifecycle, and re-sync on merge (hq-ci92, ADR 0012).

The proposal writer (`git_pr`) opens a PR and STOPS; a human reviews and merges
in GitHub. This is the BOUNDED, EXPLICIT reconcile that closes the loop AFTER
that human merge -- not a background daemon. Given one review task whose proposal
opened a PR, it READS the PR's state from GitHub (injected, never a mutating
call) and:

- MERGED (by a human, in GitHub) -> record `merged`, then re-sync ONLY the routed
  write-back target's own context source (matched by the target's exact
  (repository, ref, path) identity, so no other target is touched) -> `synced`.
- CLOSED, not merged -> record `closed_unmerged`; nothing is applied.
- OPEN -> record `pr_open`... (recorded as `open`); nothing is applied.
- unknown / unreachable / no routed target / missing PR -> an EXPLICIT state,
  never a silent 'done'.

It NEVER merges, approves, or writes a governed version -- GitHub is the merge
authority (ADR 0012). It returns the computed lifecycle and the task's payload;
the caller persists the lifecycle on the task (coupled with its audit).
"""

from __future__ import annotations

from collections.abc import Callable

# The bounded reconcile SWEEP's limit bounds, owned here so EVERY entry point (the
# admin HTTP route and the CLI) enforces the same range through one validator --
# not just the transport that happens to check (hq-3ta2 #437 round 2).
SWEEP_LIMIT_MIN = 1
SWEEP_LIMIT_MAX = 200
DEFAULT_SWEEP_LIMIT = 50


class SweepLimitError(ValueError):
    """A reconcile-sweep `limit` outside `SWEEP_LIMIT_MIN..SWEEP_LIMIT_MAX` (or not
    an integer). Raised at the sweep entry point so both callers reject it."""


def validate_sweep_limit(limit) -> int:
    """The ONE reconcile-sweep limit validator (hq-3ta2). Returns a bounded int or
    raises `SweepLimitError`, so the CLI and the HTTP route cannot diverge."""
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise SweepLimitError(
            f"limit must be an integer between {SWEEP_LIMIT_MIN} and {SWEEP_LIMIT_MAX}"
        ) from exc
    if value < SWEEP_LIMIT_MIN or value > SWEEP_LIMIT_MAX:
        raise SweepLimitError(f"limit must be between {SWEEP_LIMIT_MIN} and {SWEEP_LIMIT_MAX}")
    return value


def reconcile_proposal(
    *,
    task_id: str,
    session_factory,
    cache_dir,
    read_pr_state: Callable[..., dict],
    workspace: str = "default",
    session=None,
    now=None,
) -> tuple[dict, dict]:
    """Reconcile one task's proposal PR. Returns (lifecycle, base_payload).

    `read_pr_state(repository=, head_branch=, token=)` is injected so the GitHub
    read is mocked in tests (no network). `base_payload` is the task's current
    `proposal_payload`, returned so the caller can persist `{**base_payload,
    "pr_lifecycle": lifecycle}` in one audited transaction. With `session` given,
    the merge re-sync's snapshot is written in THAT transaction (not committed
    here), so a failed lifecycle/audit append rolls the snapshot back too -- the
    caller runs this whole reconcile inside one `session.begin()`.
    """
    from hyperset.context.sync import sync_git_context
    from hyperset.db.base import utcnow
    from hyperset.repositories.errors import NotFoundError
    from hyperset.repositories.postgres import (
        PostgresContextRepository,
        PostgresReviewRepository,
        PostgresWritebackConfigRepository,
    )

    checked_at = (now or utcnow()).isoformat()
    task = PostgresReviewRepository(session_factory).get_task(
        task_id, workspace=workspace
    )  # NotFoundError -> caller
    base_payload = dict(task.proposal_payload or {})
    routing = base_payload.get("review_routing") or {}
    backlink = routing.get("backlink")
    head_branch = routing.get("head_branch")
    # The RECORDED, immutable identity of the target this proposal actually routed
    # to (the propose-time snapshot), NOT the live mutable row. Reconcile is done
    # against this, so an edited target row can never redirect a reconcile to a
    # different repo (hq-ci92 #436 round 2 TOCTOU).
    recorded_target = routing.get("target") or {}
    target_id = recorded_target.get("id")
    rec_repository = recorded_target.get("repository")
    rec_base_ref = recorded_target.get("base_ref")
    rec_path = routing.get("path")

    def life(state: str, *, sync=None, pr=None) -> dict:
        pr = pr or {}
        return {
            "state": state,
            "pr_url": pr.get("pr_url") or backlink,
            "pr_number": pr.get("pr_number"),
            "head_branch": head_branch,
            "merged": bool(pr.get("merged")),
            "target_id": target_id,
            "sync": sync,
            "checked_at": checked_at,
        }

    # A local-path target opens no PR (the pushed branch IS the proposal), so
    # there is nothing to reconcile against GitHub.
    if not backlink:
        return life("no_pr"), base_payload
    if not head_branch or not target_id or not (rec_repository and rec_base_ref and rec_path):
        return life("unknown"), base_payload

    config = PostgresWritebackConfigRepository(session_factory).get_by_id(
        target_id, workspace=workspace
    )
    if config is None:
        # The routed target is gone; without it the identity cannot be verified.
        # Explicit, never a silent done.
        return life("unknown"), base_payload

    # TOCTOU GUARD (hq-ci92 #436 round 2): the target row is MUTABLE. If it was
    # edited to a different repository/ref/path between propose and reconcile, do
    # NOT read that other repo's PR or sync it -- reconcile only against the
    # RECORDED snapshot. On a mismatch, return an explicit 'target_changed' and
    # sync NOTHING (so a proposal for A can never touch B's repo or source).
    if (config.repository, config.base_ref, config.manifest_path) != (
        rec_repository,
        rec_base_ref,
        rec_path,
    ):
        return life("target_changed"), base_payload

    # Resolve the SAME server-side token the proposal used, best-effort: a URL
    # target whose token is unavailable reads unauthenticated (a private repo then
    # yields 'unknown' rather than failing the reconcile). No secret is returned.
    token = None
    try:
        from hyperset.transport.operations import resolve_writeback_token

        token = resolve_writeback_token(config)
    except Exception:
        token = None

    # Everything from here reads the RECORDED identity, not the live row.
    pr = read_pr_state(repository=rec_repository, head_branch=head_branch, token=token)
    state = pr.get("state", "unknown")

    if not pr.get("merged"):
        # open / closed_unmerged / unknown: recorded as-is, and NEVER synced --
        # an unmerged PR is not applied (GitHub is the merge authority).
        return life(state, pr=pr), base_payload

    # MERGED by a human in GitHub. Re-sync ONLY the source of the RECORDED target,
    # matched by its exact identity so no other target is touched. hyperset merged
    # nothing; it snapshots what Git now owns.
    # Resolve the source WITHIN the routed target's OWN workspace (hq-t6nx #438
    # finding 4): the source identity is now (workspace, repository, ref, path), so a
    # workspace-agnostic lookup over a pointer two tenants share would raise
    # MultipleResultsFound and permanently 500 this tenant's reconcile. Scoping by
    # the target's workspace_id makes it exactly one row -- the recorded-snapshot
    # discipline the TOCTOU guard already uses.
    context = PostgresContextRepository(session_factory)
    try:
        source = context.get_source_by_identity(
            repository=rec_repository,
            ref=rec_base_ref,
            path=rec_path,
            workspace=config.workspace_id,
        )
    except NotFoundError:
        return life("merged", sync={"status": "no_source"}, pr=pr), base_payload

    result = sync_git_context(
        source_id=source.id,
        session_factory=session_factory,
        cache_dir=cache_dir,
        session=session,
    )
    sync = {"source_id": source.id, "status": result.status, "ok": result.ok}
    return life("synced" if result.ok else "merged", sync=sync, pr=pr), base_payload


def reconcile_and_record(
    *,
    task_id: str,
    session_factory,
    cache_dir,
    read_pr_state,
    actor: str,
    issuer: str | None,
    workspace: str = "default",
) -> dict:
    """Reconcile ONE task and record the lifecycle + audit in ONE transaction, then
    return the lifecycle (hq-ci92/hq-3ta2). Shared by the single-task admin route
    and the sweep so both record identically: the re-sync snapshot, the lifecycle
    write, and both audit rows commit together (a failed audit rolls back the
    snapshot too). Raises NotFoundError if the task is gone. Does not change
    reconcile_proposal's semantics."""
    from hyperset.repositories.postgres import (
        PostgresAdminAuditRepository,
        PostgresReviewRepository,
    )

    with session_factory() as session, session.begin():
        lifecycle, base_payload = reconcile_proposal(
            task_id=task_id,
            session_factory=session_factory,
            cache_dir=cache_dir,
            read_pr_state=read_pr_state,
            workspace=workspace,
            session=session,
        )
        PostgresReviewRepository(session_factory).set_proposal_payload(
            task_id,
            {**base_payload, "pr_lifecycle": lifecycle},
            workspace=workspace,
            session=session,
        )
        audit = PostgresAdminAuditRepository(session_factory)
        audit.record(
            actor=actor,
            actor_issuer=issuer,
            action="review_task.reconcile",
            target=task_id,
            result=lifecycle["state"],
            detail=f"pr={lifecycle.get('pr_number')}",
            workspace=workspace,
            session=session,
        )
        sync = lifecycle.get("sync") or {}
        if sync.get("source_id"):
            audit.record(
                actor=actor,
                actor_issuer=issuer,
                action="context_source.sync",
                target=sync["source_id"],
                result="ok" if sync.get("ok") else "error",
                detail=f"reconcile:{sync.get('status')}",
                workspace=workspace,
                session=session,
            )
    return lifecycle


def reconcile_open_proposals(
    *,
    session_factory,
    cache_dir,
    read_pr_state,
    actor: str,
    issuer: str | None,
    limit: int,
    workspace: str = "default",
) -> dict:
    """Bounded reconcile SWEEP (hq-3ta2): reconcile up to `limit` tasks whose
    proposal PR is still open, OLDEST-first, EACH in its own transaction.

    Not a daemon -- an idempotent, externally scheduled, bounded call. Per-task
    failures are ISOLATED: each task reconciles+records in its own transaction, and
    any error is caught and reported as that task's result, so one task's failure
    never aborts the sweep, corrupts another task's transaction, or leaks across
    tasks. Idempotent: terminal tasks are not selected (see `list_reconcilable`), so
    a re-run over merged/synced tasks does nothing. No cross-target: each task's
    re-sync is bounded by reconcile_proposal's recorded-snapshot guard."""
    from hyperset.repositories.postgres import PostgresReviewRepository

    limit = validate_sweep_limit(limit)
    tasks = PostgresReviewRepository(session_factory).list_reconcilable(
        limit=limit, workspace=workspace
    )
    results: list[dict] = []
    for task in tasks:
        try:
            lifecycle = reconcile_and_record(
                task_id=task.id,
                session_factory=session_factory,
                cache_dir=cache_dir,
                read_pr_state=read_pr_state,
                actor=actor,
                issuer=issuer,
                workspace=workspace,
            )
            results.append(
                {
                    "task_id": task.id,
                    "state": lifecycle["state"],
                    "pr_number": lifecycle.get("pr_number"),
                    "synced": bool((lifecycle.get("sync") or {}).get("ok")),
                }
            )
        except Exception as exc:  # noqa: BLE001 -- one task's failure never aborts the sweep
            results.append({"task_id": task.id, "state": "error", "error": type(exc).__name__})
    return {"limit": limit, "count": len(results), "tasks": results}
