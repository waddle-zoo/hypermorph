#!/usr/bin/env python3
"""Poll-driven refinery merge: self-trigger the landing of an approved, green PR.

The stall this closes (hy-8b6c): after the critic and adversary post MERGE
verdicts on a green, MERGEABLE PR, the merge still waited for a human nudge --
#298 sat 5h, #300 16h, each already clear to land. The refinery's default loop is
now to POLL for such PRs and land them, with no nudge.

This is the AUTONOMOUS sibling of ``scripts/merge_precheck.py``, not a
replacement. ``merge_precheck`` is the one-command control a human merger runs
and reads; this loop runs that same control (``mp.evaluate``) on every open PR
and, only when it is CLEAR, executes the merge. Because no human is in the loop
it is STRICTER on two axes the human otherwise supplies by judgement:

  * dual-model AT THE LANDING TREE -- it requires TWO DISTINCT review ROLES
    (``role=critic`` AND ``role=adversary``) whose MERGE verdict PINS a reviewed
    tree (``tree=``) STILL equal to the tree that would land now. Distinctness is
    by ROLE, not by forge login: this town posts every review comment as one
    GitHub account, so a distinct-login count is always 1 and nothing would ever
    land (hy-irni). Role distinctness is discipline (as forge authorship has always
    been here), and one comment cannot supply two roles. This is the fix for the
    #302 BLOCKER too:
    checking only that the would-land merge has *a* tree (no conflict) let a base
    advance B -> B' auto-land ``merge(B', H)`` -- a tree no verdict reviewed and,
    under ADR 0014's no-branch-protection, no CI retested (stale-green), while
    ``--match-head-commit`` pins the head and leaves the base move invisible. The
    reviewed tree must be the tree that lands (``gate.gate_describes_would_land``,
    #300), so a base move that CHANGES the tree matches no pin and cannot land,
    while a base move the head already contains (tree unchanged) still may. The
    approvals floor is 2 and the config can only raise it
    (``HYPERSET_MIN_MERGE_APPROVALS``);
  * a head-pinned button -- ``gh pr merge --match-head-commit <head>`` refuses if
    the head moved between the decision and the merge, so a push that races the
    poll cannot land an unreviewed head.

Fail-closed throughout: a PR merges only when it is OPEN, MERGEABLE, every
required CI check is COMPLETED/SUCCESS at the head, every structured constraint
(hy-sofx ``merge_after`` / ``do_not_close``) is honoured, and two distinct trusted
review ROLES (critic AND adversary) MERGE-reviewed the exact tree that would land
now. Anything
unread, unmet, unmergeable, or reviewed at a tree that is no longer what would
land SKIPS -- the poll leaves the PR for the next pass and never lands past a
condition it cannot confirm. The reused pieces (``merge_precheck.evaluate``,
``merge_precheck.merge_authorizers`` and ``gate.gate_describes_would_land``) are
imported, never re-implemented, so the autonomous gate and the human one cannot
drift.

The single-merger rule (ADR 0014, ``docs/directives/refinery.md``) is unchanged:
this loop runs in the ONE refinery seat, and being poll-driven does not make it a
second merger -- it makes the one merger stop waiting to be told.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Import the sibling scripts as modules: scripts/ is not a package, so its
# directory goes on the path and the reused gate/precheck logic is the SAME code
# the human merger runs, never a copy.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import merge_precheck as mp  # noqa: E402

MIN_APPROVALS_ENV = "HYPERSET_MIN_MERGE_APPROVALS"
DUAL_MODEL_FLOOR = 2  # critic AND adversary -- the unattended path has NO lower escape


@dataclass
class Decision:
    """Whether one PR is clear for the poll to land, and why not when it is not."""

    pr: str
    head: str
    merge: bool
    reasons: list[str] = field(default_factory=list)


def min_approvals(env: dict[str, str] | None = None) -> int:
    """How many distinct trusted MERGE verdicts the unattended merge requires.

    The floor is DUAL-MODEL (2): the config can only RAISE the bar, never lower
    it. A value below 2, an unparseable one, or an absent one is forced to 2 --
    a floor of 1 would collapse dual-model on the unattended path and let a single
    approval auto-merge (#302 MINOR), so the configuration fails toward MORE
    review, never less. Distinctness is by ROLE and the known roles are two
    (`MERGE_APPROVAL_ROLES`), so a value above 2 can never be met and simply keeps
    the poll inert -- a raise, still fail-closed, but there is no third role to
    satisfy it.
    """
    raw = (env if env is not None else os.environ).get(MIN_APPROVALS_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DUAL_MODEL_FLOOR
    return max(value, DUAL_MODEL_FLOOR)


def merged_tree_reason(
    *, head_tree: str, would_land: str | None, run_suite: Callable[[str], bool]
) -> str | None:
    """The residual merged-tree-red block reason, or None (hy-ftyp).

    #302's tree-identity re-gate already confirms the would-land tree IS the tree
    the dual-model reviewed. This is the class it does NOT catch: a genuinely-new
    NON-FF would-land tree can be clean-merge, green at the head, and green at
    main, yet RED at the MERGED tree -- a semantic conflict no single head shows
    (a rename on one side, a new caller on the other; a moved fixture, a new test
    that reads it). GitHub's `pull_request` CI tests the merge ref, but that green
    goes STALE the moment main advances past the CI run, and this town carries no
    branch protection to force a re-run (ADR 0014).

    WHICH trees owe the merged-tree suite -- and only those:

      * would_land == head_tree: a pure FAST-FORWARD or content-equal tree. The
        head already contains all of base, so the tree that lands IS the head tree
        the head's own CI ran green. Nothing new to test -- `run_suite` is not even
        called, so a trivial FF never pays for a suite run.
      * would_land is None: a CONFLICT, which has no single tree; it is already
        refused upstream (not MERGEABLE, and no `describes` pin matches), so this
        guard says nothing about it.
      * would_land != head_tree: main advanced into content the head does NOT have,
        producing a tree neither head nor base was tested at. THIS is the tree the
        suite must run at, and it fails CLOSED: `run_suite` must return True (the
        suite ran and passed at exactly that tree) or the merge is blocked.

    Pure and injectable: `run_suite` is the seam, so the FF/non-FF branching and
    the fail-closed rule are tested without running pytest, and the FF path is
    proven to make no suite call at all."""
    if would_land is None or would_land == head_tree:
        return None
    if run_suite(would_land):
        return None
    return (
        f"the would-land tree {would_land[:12]} is a genuinely-new non-FF merge (main advanced "
        "into content the head does not have), and the test suite is not confirmed GREEN at that "
        "merged tree -- a semantic conflict can be red at the merge while the head and main are "
        "both green, and the pull_request CI green is stale once main moved past it; re-gate"
    )


def _tree_of(commit: str, root: Path) -> str:
    """`<commit>^{tree}` as a 40-hex oid, or raise `gate.GitEnvError`. The head's
    tree, to tell a fast-forward (would-land == head tree) from a genuinely-new
    merge without a checkout."""
    result = subprocess.run(
        ["git", "rev-parse", f"{commit}^{{tree}}"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    oid = result.stdout.strip()
    if result.returncode != 0 or not gate._OID.match(oid):
        raise gate.GitEnvError(
            f"could not resolve the tree of {commit} in {root}: rc={result.returncode}, "
            f"stderr={result.stderr.strip()!r}"
        )
    return oid


def suite_green_at_tree(tree: str, *, parent: str | None = None, root: Path | None = None) -> bool:
    """Run the full gate at exactly `tree` in a throwaway detached worktree and
    return whether it passed AT that tree (hy-ftyp). Fail-CLOSED: any fault --
    git, the worktree, a non-PASS gate, or a gate that measured a DIFFERENT tree
    -- is False, so an unconfirmable merged tree never clears the auto-merge.

    A tree has no commit to check out, so a throwaway commit carries it
    (`commit-tree`), a detached worktree materialises it, and `gate.py` runs there.
    The gate's own `tree_id` must equal `tree`: it is the proof the suite measured
    the merged tree and not something the checkout drifted to.

    The throwaway commit is PARENTED on `parent` (the base the merge lands onto),
    so the worktree's history is the real one -- a PARENTLESS commit would BE a
    root commit, and a suite test that reads the repository's root commit
    (`git rev-list --max-parents=0`, e.g. the clause3 root assertion) would red on
    the synthetic root, a false red that would block every non-FF merge. The tree
    is identical either way (`commit-tree` sets the tree; the parent sets only the
    history), so `tree_id` still equals `tree`."""
    root = gate.ROOT if root is None else root
    worktree: str | None = None
    try:
        commit = subprocess.run(
            ["git", "commit-tree", tree, *(["-p", parent] if parent else []), "-m", "hy-ftyp"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        commit_sha = commit.stdout.strip()
        if commit.returncode != 0 or not gate._OID.match(commit_sha):
            _log(f"! merged-tree suite: commit-tree of {tree[:12]} failed: {commit.stderr.strip()}")
            return False
        worktree = tempfile.mkdtemp(prefix="hy-ftyp-merged-tree-")
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", worktree, commit_sha],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        if add.returncode != 0:
            _log(f"! merged-tree suite: worktree add for {tree[:12]} failed: {add.stderr.strip()}")
            return False
        try:
            # Carry the gitignored BUILT frontend (playground/ui/dist) from the base
            # checkout: a fresh worktree has only the dev index.html, and a suite
            # test asserts the BUILT asset path is served, so its absence would
            # false-red every non-FF merge. This guard's target is the PYTHON
            # semantic-conflict class (a rename + a new caller, a moved fixture);
            # the frontend build artifact is carried as-is, because a
            # frontend-SOURCE change is CI's own `npm build` concern, not a Python
            # merge conflict this suite would see. Absent in the base too (never
            # built) -> not copied, and the one asset test reds, which is honest.
            built_ui = Path(root) / "playground" / "ui" / "dist"
            if built_ui.is_dir():
                shutil.copytree(
                    built_ui,
                    Path(worktree) / "playground" / "ui" / "dist",
                    dirs_exist_ok=True,
                )
            # A detached worktree has the merged tree's FILES but its own empty
            # environment. Sync the extras into it FIRST, exactly as the completion
            # sequence does -- else `gate.py` finds an extra missing and prints
            # `NOT-A-GATE` (its refusal to run pytest with the suite incomplete),
            # which would fail-close EVERY non-FF merge, not just a red one. The uv
            # cache makes this mostly a link step. A sync that fails is fail-closed
            # (return False), never a merge on an unbuilt env.
            synced = subprocess.run(
                ["uv", "sync", "--all-extras", "--all-groups"],
                cwd=worktree,
                capture_output=True,
                text=True,
            )
            if synced.returncode != 0:
                _log(f"! merged-tree suite at {tree[:12]}: uv sync failed: {synced.stderr.strip()}")
                return False
            run = subprocess.run(
                ["uv", "run", "python", "scripts/gate.py"],
                cwd=worktree,
                capture_output=True,
                text=True,
            )
            line = next(
                (ln for ln in reversed(run.stdout.splitlines()) if ln.startswith(gate.LINE_PREFIX)),
                None,
            )
            if line is None:
                _log(f"! merged-tree suite at {tree[:12]}: no gate line in output")
                return False
            fields = gate.parse_evidence_line(line)
            passed = fields.get("result") == "PASS" and fields.get("tree_id") == tree
            verdict = "GREEN" if passed else "RED/mismatch"
            _log(
                f"merged-tree suite at {tree[:12]}: result={fields.get('result')} "
                f"tree_id={fields.get('tree_id', '')[:12]} -> {verdict}"
            )
            return passed
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree],
                cwd=str(root),
                capture_output=True,
                text=True,
            )
    except (OSError, ValueError) as exc:
        _log(f"! merged-tree suite at {tree[:12]} could not run: {exc}")
        return False
    finally:
        # `git worktree remove` unlinks a registered worktree; a temp dir created
        # but never registered (a failed `worktree add`) is not its concern, so it
        # is removed here so no orphan accumulates under $TMPDIR on repeated faults.
        if worktree is not None and os.path.isdir(worktree):
            shutil.rmtree(worktree, ignore_errors=True)


def merged_tree_guard(base: str, head: str, *, root: Path | None = None) -> str | None:
    """The wired guard the poll passes to `decide`: compute the head tree and the
    would-land tree from the graph, then apply `merged_tree_reason`, running the
    suite (`suite_green_at_tree`) only for a genuinely-new non-FF tree. A
    `gate.GitEnvError` from either graph read propagates, so the pass SKIPs the PR
    (fail-closed) rather than landing on an unconfirmable tree."""
    root = gate.ROOT if root is None else root
    would_land = gate.would_land_tree(base, head, root)
    head_tree = _tree_of(head, root)
    return merged_tree_reason(
        head_tree=head_tree,
        would_land=would_land,
        # Parent the throwaway commit on the base, so the merged-tree checkout
        # carries the real history (its root commit) and no history-reading test
        # false-reds on a synthetic root.
        run_suite=lambda tree: suite_green_at_tree(tree, parent=base, root=root),
    )


def decide(
    pr: str,
    live: dict,
    *,
    trusted_authors: set[str] | None,
    required_checks: tuple[str, ...],
    bead_id: str | None,
    approvals_needed: int,
    acknowledged: set[str],
    merged_prs: set[str] | None,
    describes,
    merged_tree_check: Callable[..., str | None],
) -> Decision:
    """Decide whether the poll may land this PR, fail-closed with named reasons.

    Pure and injectable: the loop fetches the live PR and passes it here, and the
    tests pass crafted state with a stub ``describes`` (the injected
    ``gate.gate_describes_would_land``), so every rule -- and both arms -- run
    without a network. Empty ``reasons`` (``merge=True``) is the ONLY path to the
    button.
    """
    reasons: list[str] = []
    head = live.get("head_sha") or ""

    state = live.get("state")
    if state != "OPEN":
        # Strict, like `mergeable` below: an absent/None state SKIPS rather than
        # passing. A real PR always carries a state, so this only fires on a
        # malformed fetch -- toward not-merging, never toward it.
        reasons.append(f"state is {state or 'UNKNOWN'}, not OPEN")

    mergeable = live.get("mergeable")
    if mergeable != "MERGEABLE":
        # CONFLICTING, UNKNOWN (the forge has not finished computing), or absent
        # all skip: the poll lands only a PR the forge itself calls mergeable.
        reasons.append(f"mergeable is {mergeable or 'UNKNOWN'}, not MERGEABLE")

    # The human-run control, run unchanged: CI green at the head, head != base, a
    # trusted MERGE verdict names the live head, the Completes-Bead trailer, and
    # every structured constraint honoured. Its reasons are this loop's reasons.
    reasons.extend(
        mp.evaluate(
            head_sha=head,
            base_sha=live.get("base_sha"),
            checks=live.get("checks") or [],
            verdicts=live.get("verdicts") or [],
            pr_number=pr,
            trusted_authors=trusted_authors,
            required_checks=required_checks,
            bead_id=bead_id,
            commit_messages=live.get("commit_messages") or [],
            merged_prs=merged_prs,
            acknowledged_constraints=acknowledged,
        )
    )

    # The load-bearing autonomous gate, folding dual-model AND the #300 tree
    # identity into one selection (the #302 BLOCKER: the old check computed the
    # would-land tree and only None-checked it, so a base advance B -> B' auto-
    # landed merge(B', H) -- a tree no verdict reviewed and, under ADR 0014, no CI
    # retested). `merge_authorizers` returns the DISTINCT review ROLES (critic /
    # adversary, hy-irni -- not logins, since one account posts all reviews) whose
    # trusted MERGE verdict PINS a reviewed tree STILL equal to the current
    # would-land tree (via `describes` = gate.gate_describes_would_land). So a base
    # move that changes the tree, a verdict with no `tree=` pin, or one with no
    # `role=` tag authorizes nothing; a base move the head already contains (tree
    # unchanged) still does. Two DISTINCT roles are required.
    base = live.get("base_sha")
    if not (head and base):
        reasons.append("missing head or base sha; the would-land tree cannot be confirmed")
    authorizers = mp.merge_authorizers(
        live.get("verdicts") or [],
        head_sha=head,
        pr_number=pr,
        trusted_authors=trusted_authors,
        base_sha=base,
        describes=describes,
    )
    if len(authorizers) < approvals_needed:
        named = ", ".join(sorted(authorizers)) or "none"
        reasons.append(
            f"unattended-merge unauthorized: {len(authorizers)} distinct trusted MERGE "
            f"role(s) [{named}] pin the tree that would land now, need {approvals_needed} "
            "(critic AND adversary, AT the current would-land tree); a verdict with no "
            "role= tag, no tree= pin, or a tree a base advance has since made stale does "
            "not authorize an auto-land -- re-gate"
        )

    # hy-ftyp: the residual merged-tree-red guard, LAST and only when everything
    # else is clear. #302's re-gate has just confirmed the would-land tree IS the
    # reviewed tree; this runs the full suite AT that tree when -- and only when --
    # it is a genuinely-new non-FF merge (main advanced into content the head
    # lacks), catching a semantic conflict that reds the merge while both heads are
    # green. Gated on `not reasons` so the expensive suite never runs for a PR that
    # will not land anyway, and on head/base being present so the trees can be
    # computed. A `gate.GitEnvError` from the graph reads propagates to `poll_once`,
    # which SKIPs the PR -- an env fault never lands, and never reads as clear.
    if not reasons and head and base:
        merged_reason = merged_tree_check(base=base, head=head)
        if merged_reason:
            reasons.append(merged_reason)

    return Decision(pr=pr, head=head, merge=not reasons, reasons=reasons)


def _log(message: str) -> None:
    """One timestamped line, flushed. The poll is a long-lived process whose only
    window is its log, so a false 'polling' must be falsifiable from it: every pass
    and every per-PR decision is a line here (hy-wpqa)."""
    print(f"[{datetime.now(UTC).isoformat(timespec='seconds')}] {message}", flush=True)


def preflight() -> str | None:
    """The environment the poll cannot run without, or None when it is sound.

    Two faults, each of which made EVERY PR skip while the log said "polling", so
    each is named ONCE up front and stops the pass rather than repeating per PR:

      * gate.ROOT is not a git repository (hy-wpqa, the #305 root cause): the poll
        ran from a non-git scratchpad copy, so every `would_land_tree` failed for
        the environment and every verdict's tree check came back empty. This is the
        load-bearing guard -- an env fault that is global must be caught globally,
        not read as "no PR is clear";
      * `HYPERSET_CRITIC_LOGINS` is unset: `_trusted_authors()` is None, every
        verdict is untrusted, every PR skips as unauthorized.
    """
    if not gate.is_git_repo():
        return (
            f"gate.ROOT ({gate.ROOT}) is not a git work tree -- every would-land tree "
            "computation would fail for the ENVIRONMENT, not the code, so every PR would "
            "skip with would_land None. Run the poll from a real checkout or a detached "
            "worktree at origin/main, never from a copy in a scratch directory."
        )
    if mp._trusted_authors() is None:
        return (
            f"{mp.CRITIC_LOGINS_ENV} is unset -- no verdict can be trusted, so every PR "
            "would skip as unauthorized. Export it with the reviewer login(s), e.g. "
            f"{mp.CRITIC_LOGINS_ENV}=bsovs."
        )
    return None


def _gh(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def open_pr_numbers(limit: int = 100) -> list[str]:
    """Every OPEN PR number, newest first. An empty or failed list yields nothing
    to merge -- the poll does nothing rather than guessing."""
    result = _gh(["pr", "list", "--state", "open", "--json", "number", "--limit", str(limit)])
    if result.returncode != 0:
        _log(f"! gh pr list failed: {result.stderr.strip()}")
        return []
    return [str(item["number"]) for item in json.loads(result.stdout or "[]")]


def _ensure_objects(branch: str | None) -> None:
    """Make the PR head and current main reachable locally so
    ``gate.would_land_tree`` can compute the merge tree. This clone's fetch
    refspec tracks only main, so a crew branch is fetched by name here.

    The fetches run in ``gate.ROOT`` -- the SAME repo ``would_land_tree`` reads --
    not the process CWD: fetching the head into a different repo would leave the
    oid absent where the merge is computed, degrading every PR to a git-env fault
    (hy-wpqa). Anchoring both to ROOT keeps the object and its reader together."""
    subprocess.run(["git", "fetch", "-q", "origin", "main"], check=False, cwd=str(gate.ROOT))
    if branch:
        subprocess.run(["git", "fetch", "-q", "origin", branch], check=False, cwd=str(gate.ROOT))


def land(pr: str, head: str) -> bool:
    """Press the button, pinned to the reviewed head. ``--match-head-commit``
    makes the forge refuse if the head moved after the decision, so a push that
    raced the poll cannot land an unreviewed head. Returns whether it merged."""
    result = _gh(["pr", "merge", pr, "--merge", "--match-head-commit", head])
    if result.returncode != 0:
        _log(f"! merge of PR #{pr} @ {head[:12]} did not execute: {result.stderr.strip()}")
        return False
    _log(f"MERGED PR #{pr} @ {head[:12]}")
    return True


RIG = "hyperset"


def reconcile_merged_pr(
    pr: str,
    *,
    commit_messages: list[str],
    branch: str | None,
    graded_tree: str | None,
    landed_tree: str | None,
    merge_sha: str,
    bead_status,
    close_bead,
    mq_id_for_branch,
    mq_clear,
) -> None:
    """S4 on a PR the poll JUST merged: close the source bead and clear the mq
    entry, hands-off (hy-n0ge). Pure and injectable -- the four IO callables are
    the tests' seam, so every fail-safe branch runs with no bd/gt/network.

    Only ever called after an ACTUAL merge. It is fail-safe and idempotent and
    NEVER closes the wrong bead:

      * the tree that LANDED must equal the tree that was graded/reviewed. If a
        race landed a different tree, it closes NOTHING and shouts -- the reason it
        would write ("graded == landed") must be true, not asserted;
      * the bead is the SINGLE `Completes-Bead:` trailer. Zero (no trailer) or more
        than one (ambiguous) closes nothing and leaves it for a human;
      * a bead id that does not resolve is not closed; a bead already `closed` is
        skipped (idempotent -- a second pass does not error);
      * the mq entry is cleared only if one matches the branch (a crew PR has
        none, which is a logged no-op, not a failure).
    """
    if graded_tree is None or landed_tree is None or graded_tree != landed_tree:
        _log(
            f"PR #{pr}: reconcile ABORTED -- graded tree {graded_tree} != landed tree "
            f"{landed_tree}; closing nothing, a human must check what actually landed"
        )
        return

    beads = mp.completion_beads(commit_messages)
    if not beads:
        _log(f"PR #{pr}: no Completes-Bead trailer -- no bead to close, left for a human")
    elif len(beads) > 1:
        _log(
            f"PR #{pr}: AMBIGUOUS -- {len(beads)} distinct Completes-Bead trailers {beads}; "
            "closing none, left for a human (never the wrong bead)"
        )
    else:
        bead = beads[0]
        status = bead_status(bead)
        if status is None:
            _log(
                f"PR #{pr}: Completes-Bead {bead} does not resolve -- not closing (a human should)"
            )
        elif status == "closed":
            _log(f"PR #{pr}: bead {bead} already closed -- nothing to do (idempotent)")
        else:
            reason = (
                f"landed via auto-merge PR #{pr} (merge {merge_sha[:12]}; "
                f"graded tree {graded_tree[:12]} == landed tree {landed_tree[:12]})"
            )
            if close_bead(bead, reason):
                _log(f"PR #{pr}: closed bead {bead} -- {reason}")

    mr = mq_id_for_branch(branch) if branch else None
    if mr:
        if mq_clear(mr):
            _log(f"PR #{pr}: cleared mq entry {mr}")
    else:
        _log(f"PR #{pr}: no mq entry for branch {branch} -- nothing to clear")


def _bead_status(bead: str) -> str | None:
    """The bead's status via `bd show --json`, or None when it does not resolve.
    Read-only. Runs in gate.ROOT so the `.beads/redirect` routes to the rig DB."""
    result = subprocess.run(
        ["bd", "show", bead, "--json"], capture_output=True, text=True, cwd=str(gate.ROOT)
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "null")
    except json.JSONDecodeError:
        return None
    rows = data if isinstance(data, list) else [data]
    if not rows or not rows[0]:
        return None
    return (rows[0].get("status") or "").lower() or None


def _close_bead(bead: str, reason: str) -> bool:
    result = subprocess.run(
        ["bd", "close", bead, "--reason", reason],
        capture_output=True,
        text=True,
        cwd=str(gate.ROOT),
    )
    if result.returncode != 0:
        _log(f"! bd close {bead} failed: {result.stderr.strip()}")
        return False
    return True


def _mq_id_for_branch(branch: str) -> str | None:
    result = subprocess.run(["gt", "mq", "list", RIG, "--json"], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        entries = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    for entry in entries:
        # Match the `branch: <b>` line EXACTLY, not as a substring: a prefix branch
        # (`crew/hy-27` vs `crew/hy-273-x`) must not clear another entry's queue row.
        lines = (entry.get("description") or "").splitlines()
        if any(line.strip() == f"branch: {branch}" for line in lines):
            return entry.get("id")
    return None


def _mq_clear(mr_id: str) -> bool:
    # --skip-branch-delete: crew branches outlive the merge (delete_branch_on_merge
    # is off) and their deletion is the guarded, separate delete_branch.py path.
    result = subprocess.run(
        ["gt", "mq", "post-merge", RIG, mr_id, "--skip-branch-delete"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _log(f"! gt mq post-merge {mr_id} failed: {result.stderr.strip()}")
        return False
    return True


def _pr_merge_commit(pr: str) -> str | None:
    result = _gh(["pr", "view", pr, "--json", "mergeCommit", "--jq", ".mergeCommit.oid"])
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _reconcile_live(pr: str, live: dict, head: str) -> None:
    """Compute the graded and landed trees for a just-merged PR, then reconcile.
    Fail-safe: any git/gh fault leaves a tree None, which the reconciler reads as
    'do not close', so a reconcile problem never closes a bead wrongly."""
    graded = None
    try:
        graded = gate.would_land_tree(live.get("base_sha"), head)
    except gate.GitEnvError as exc:
        _log(f"PR #{pr}: reconcile could not compute the graded tree: {exc}")
    subprocess.run(["git", "fetch", "-q", "origin", "main"], check=False, cwd=str(gate.ROOT))
    merge_sha = _pr_merge_commit(pr) or ""
    landed = None
    if merge_sha:
        result = subprocess.run(
            ["git", "rev-parse", f"{merge_sha}^{{tree}}"],
            capture_output=True,
            text=True,
            cwd=str(gate.ROOT),
        )
        landed = result.stdout.strip() if result.returncode == 0 else None
    reconcile_merged_pr(
        pr,
        commit_messages=live.get("commit_messages") or [],
        branch=live.get("branch"),
        graded_tree=graded,
        landed_tree=landed,
        merge_sha=merge_sha or head,
        bead_status=_bead_status,
        close_bead=_close_bead,
        mq_id_for_branch=_mq_id_for_branch,
        mq_clear=_mq_clear,
    )


def poll_once(*, dry_run: bool = False) -> dict:
    """One pass: enumerate open PRs, LOG each one's decision, and land those the
    gate clears (unless ``dry_run``). Returns a summary dict.

    Every PR considered produces exactly one WOULD MERGE / SKIP line naming the
    gate(s) it failed, and the pass is bracketed by a start line (echoing the
    trusted logins, required checks, and approvals floor so a config fault is
    visible) and a summary line. So the log answers 'is it really polling, and why
    did a clear PR not land' without guessing (hy-wpqa).
    """
    trusted = mp._trusted_authors()
    required = mp._required_checks()
    needed = min_approvals()
    mode = "DRY-RUN (no merge)" if dry_run else "LIVE"
    _log(
        f"poll pass start [{mode}]: trusted_logins="
        f"{sorted(trusted) if trusted else None} required_checks={list(required)} "
        f"approvals_floor={needed}"
    )
    fatal = preflight()
    if fatal:
        _log(f"FATAL CONFIG: {fatal} -- pass does nothing, no PR considered")
        return {"considered": 0, "would_merge": 0, "merged": 0, "skipped": 0, "fatal": True}

    acknowledged = mp._acknowledged_constraints()
    considered = would_merge = merged = skipped = 0
    for pr in open_pr_numbers():
        considered += 1
        live = mp._fetch(pr)
        _ensure_objects(live.get("branch"))
        bead_id = mp.bead_id_from_branch(live.get("branch"))
        constraints = mp.merge_constraints(
            live["verdicts"], head_sha=live["head_sha"], pr_number=pr, trusted_authors=trusted
        )
        try:
            decision = decide(
                pr,
                live,
                trusted_authors=trusted,
                required_checks=required,
                bead_id=bead_id,
                approvals_needed=needed,
                acknowledged=acknowledged,
                merged_prs=mp._merged_prs(constraints),
                describes=gate.gate_describes_would_land,
                merged_tree_check=merged_tree_guard,
            )
        except gate.GitEnvError as exc:
            # A per-PR git ENV fault (an unfetched object, an unresolvable ref) is
            # loud and skips THIS PR -- never a silent None that reads as "not
            # clear". The daemon keeps running; the pass continues (hy-wpqa).
            skipped += 1
            _log(f"PR #{pr} @ {(live.get('head_sha') or '')[:12]}: SKIP -- GIT ENV FAULT: {exc}")
            continue
        head12 = (decision.head or "")[:12]
        if decision.merge:
            would_merge += 1
            _log(f"PR #{pr} @ {head12}: WOULD MERGE -- every gate clear")
            if dry_run:
                _log(f"PR #{pr}: dry-run, not merging")
            elif land(pr, decision.head):
                merged += 1
                # Hands-off S4: close the source bead + clear the mq entry, on the
                # actually-merged PR only, fail-safe and idempotent (hy-n0ge).
                _reconcile_live(pr, live, decision.head)
        else:
            skipped += 1
            _log(f"PR #{pr} @ {head12}: SKIP -- {len(decision.reasons)} gate(s) unmet:")
            for reason in decision.reasons:
                _log(f"    - {reason}")
    _log(
        f"poll pass done: considered={considered} would_merge={would_merge} "
        f"merged={merged} skipped={skipped}"
    )
    return {
        "considered": considered,
        "would_merge": would_merge,
        "merged": merged,
        "skipped": skipped,
        "fatal": False,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Poll open PRs and land the approved, green ones.")
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        metavar="SECONDS",
        help="loop forever, sleeping SECONDS between passes (default: one pass, then exit)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="log each PR's WOULD MERGE / SKIP decision but never merge (safe to run)",
    )
    args = parser.parse_args(argv)
    if args.interval <= 0:
        poll_once(dry_run=args.dry_run)
        return 0
    while True:  # pragma: no cover - the unattended loop
        poll_once(dry_run=args.dry_run)
        time.sleep(args.interval)


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main(sys.argv[1:]))
