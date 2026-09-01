"""The poll-driven refinery merge decides fail-closed, and merges only on CLEAR.

hy-8b6c: after the critic and adversary post MERGE verdicts on a green, MERGEABLE
PR, the merge waited for a human nudge (#298 5h, #300 16h). The poll now lands
such PRs itself. The load-bearing logic is the pure ``decide``: it reuses
``merge_precheck.evaluate`` (the human control) and adds the axis a human supplied
by judgement -- a dual-model MERGE that pinned the tree that would land NOW.

#302 BLOCKER, the arm this file exists to hold: the old gate computed the
would-land tree and only None-checked it for a conflict; it never compared it to
the REVIEWED tree, so a base advance B -> B' would auto-land ``merge(B', H)`` -- a
tree no verdict reviewed and, under ADR 0014, no CI retested. ``decide`` now
authorizes only when two distinct trusted logins pinned a reviewed tree that
STILL equals the current would-land tree (``gate.gate_describes_would_land``,
injected here as ``describes``), so a main-advance that CHANGES the tree SKIPS.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mp = _load("merge_precheck")
rp = _load("refinery_poll")

HEAD = "689bf470583958280d113057ae941088103c7107"
BASE = "a" * 40
MOVED = "b" * 40
TREE = "1" * 40  # the reviewed tree the verdicts pin AND the current would-land tree
OTHER_TREE = "2" * 40  # what a main-advance would make the would-land tree instead
# One forge account posts every review (the hy-irni reality): distinctness is by
# ROLE, not login, so both roles share the SAME trusted login here.
BSOVS = "bsovs"
TRUSTED = {BSOVS}
GREEN = [
    {"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"name": "migrations", "status": "COMPLETED", "conclusion": "SUCCESS"},
    {"name": "benchmark", "status": "COMPLETED", "conclusion": "SUCCESS"},
]


def describes_at(landing_tree: str):
    """A stub for ``gate.gate_describes_would_land``: a pinned tree describes the
    would-land tree iff it equals ``landing_tree`` (what would land now)."""

    def describes(*, tree_id: str, base: str, head: str) -> bool:
        return tree_id == landing_tree

    return describes


def _verdict(
    author: str = BSOVS,
    *,
    sha: str = HEAD,
    word: str = "MERGE",
    tree: str = TREE,
    role: str = "",
    comment: str | None = None,
    constraints: str = "",
) -> object:
    body = f"HYPERSET-VERDICT v1 seat=s pr=273 sha={sha} verdict={word}"
    if tree:
        body += f" tree={tree}"
    if role:
        body += f" role={role}"
    if constraints:
        body += f" constraints={constraints}"
    (v,) = mp.parse_verdicts(body, author=author, comment=comment)
    return v


def _dual_model(**kw) -> list[object]:
    # critic and adversary, SAME login, DISTINCT roles on DISTINCT comments.
    return [
        _verdict(role="critic", comment="c-critic", **kw),
        _verdict(role="adversary", comment="c-adversary", **kw),
    ]


def _live(**over) -> dict:
    live = dict(
        head_sha=HEAD,
        base_sha=BASE,
        state="OPEN",
        mergeable="MERGEABLE",
        checks=GREEN,
        verdicts=_dual_model(),
        commit_messages=["feat: x\n\nCompletes-Bead: hy-273"],
        branch="crew/hy-273-x",
    )
    live.update(over)
    return live


def dec(live=None, **over) -> object:
    """`decide` with green/dual-model defaults and a `describes` that says the
    reviewed tree IS what would land now; override one field per test."""
    live = live if live is not None else _live()
    params = dict(
        pr="273",
        trusted_authors=TRUSTED,
        required_checks=("test", "migrations", "benchmark"),
        bead_id="hy-273",
        approvals_needed=2,
        acknowledged=set(),
        merged_prs=set(),
        describes=describes_at(TREE),
        # Default: a would-land tree that owes no merged-tree suite (a FF /
        # content-equal tree). Tests that exercise the guard override this.
        merged_tree_check=lambda **_: None,
    )
    params.update(over)
    pr = params.pop("pr")
    return rp.decide(pr, live, **params)


# --- the clean path lands ---


def test_a_dual_model_pr_reviewed_at_the_landing_tree_is_cleared():
    decision = dec()
    assert decision.merge is True
    assert decision.reasons == []
    assert decision.head == HEAD


# --- #302 BLOCKER: a base advance that CHANGES the tree must not auto-land ---


def test_a_main_advance_that_changes_the_would_land_tree_skips_not_merges():
    # The verdicts pinned TREE, but main advanced so the tree that would land now
    # is OTHER_TREE. No pinned tree equals it -> no authorizer -> SKIP. This is the
    # unreviewed, stale-green tree the old None-only check would have auto-landed.
    decision = dec(describes=describes_at(OTHER_TREE))
    assert decision.merge is False
    assert any("unattended-merge unauthorized" in r for r in decision.reasons)


def test_a_base_move_the_head_already_contains_keeps_the_tree_and_still_lands():
    # A base advance the head already contains leaves the would-land tree == the
    # reviewed TREE (a fast-forward / content-equal, #300), so the same two
    # verdicts still authorize: the poll lands the exact tree CI ran.
    decision = dec(describes=describes_at(TREE))
    assert decision.merge is True
    assert decision.reasons == []


# --- a verdict with no reviewed-tree pin authorizes nothing ---


def test_a_merge_verdict_without_a_tree_pin_does_not_authorize():
    live = _live(verdicts=_dual_model(tree=""))  # both MERGE, neither pins a tree
    decision = dec(live)
    assert decision.merge is False
    assert any("unattended-merge unauthorized" in r for r in decision.reasons)


# --- dual-model by ROLE: one role, an untagged verdict, an unknown role, or a
#     single comment claiming both roles is not two (hy-irni) ---


def test_a_single_role_does_not_clear_the_unattended_merge():
    decision = dec(_live(verdicts=[_verdict(role="critic", comment="c1")]))
    assert decision.merge is False
    assert any("unattended-merge unauthorized" in r and "need 2" in r for r in decision.reasons)


def test_two_verdicts_of_the_SAME_role_count_once_not_twice():
    live = _live(
        verdicts=[
            _verdict(role="critic", comment="c1"),
            _verdict(role="critic", comment="c2"),  # distinct comments, same role
        ]
    )
    decision = dec(live)
    assert decision.merge is False
    assert any("[critic]" in r for r in decision.reasons)  # exactly one distinct role


def test_an_untagged_verdict_authorizes_nothing_two_untagged_are_not_two():
    # The hy-irni guard: two MERGE verdicts with NO role= tag must not pass as two.
    live = _live(
        verdicts=[
            _verdict(role="", comment="c1"),
            _verdict(role="", comment="c2"),
        ]
    )
    decision = dec(live)
    assert decision.merge is False
    assert any("[none]" in r for r in decision.reasons)


def test_an_unknown_role_does_not_count_toward_the_two():
    # role=critic plus role=something-else must not reach two: only known roles.
    live = _live(
        verdicts=[
            _verdict(role="critic", comment="c1"),
            _verdict(role="reviewer", comment="c2"),  # not in {critic, adversary}
        ]
    )
    decision = dec(live)
    assert decision.merge is False
    assert any("[critic]" in r for r in decision.reasons)


def test_one_comment_claiming_both_roles_counts_for_none():
    # A single comment naming BOTH roles cannot stand in for two reviewers: it is
    # discarded. Two MERGE verdicts on the SAME comment id, one per role.
    live = _live(
        verdicts=[
            _verdict(role="critic", comment="same"),
            _verdict(role="adversary", comment="same"),
        ]
    )
    decision = dec(live)
    assert decision.merge is False
    assert any("[none]" in r for r in decision.reasons)


def test_an_untrusted_second_role_does_not_make_a_dual_model():
    live = _live(
        verdicts=[
            _verdict(role="critic", comment="c1"),
            _verdict("rando", role="adversary", comment="c2"),  # untrusted login
        ]
    )
    decision = dec(live)
    assert decision.merge is False
    assert any("[critic]" in r for r in decision.reasons)


def test_a_higher_approval_bar_is_honored():
    decision = dec(approvals_needed=3)  # two present, three demanded
    assert decision.merge is False
    assert any("need 3" in r for r in decision.reasons)


# --- forge state: only a mergeable, open PR lands ---


@pytest.mark.parametrize("mergeable", ["CONFLICTING", "UNKNOWN", None])
def test_a_non_mergeable_pr_does_not_land(mergeable):
    decision = dec(_live(mergeable=mergeable))
    assert decision.merge is False
    assert any("not MERGEABLE" in r for r in decision.reasons)


def test_a_closed_pr_does_not_land():
    decision = dec(_live(state="MERGED"))
    assert decision.merge is False
    assert any("not OPEN" in r for r in decision.reasons)


def test_a_missing_state_skips_strictly_like_mergeable():
    decision = dec(_live(state=None))
    assert decision.merge is False
    assert any("not OPEN" in r for r in decision.reasons)


# --- missing head/base cannot confirm the would-land tree ---


def test_a_missing_base_sha_cannot_confirm_the_tree_and_blocks():
    decision = dec(_live(base_sha=None))
    assert decision.merge is False
    assert any("cannot be confirmed" in r for r in decision.reasons)
    # ...and with no base, merge_authorizers is empty too -- doubly fail-closed.
    assert any("unattended-merge unauthorized" in r for r in decision.reasons)


# --- the reused human control still gates (a sample of its arms) ---


def test_a_head_named_by_no_verdict_blocks_via_evaluate():
    live = _live(verdicts=_dual_model(sha=MOVED))
    decision = dec(live)
    assert decision.merge is False
    assert any("does not carry" in r or "name the head" in r for r in decision.reasons)


def test_a_pending_check_blocks_via_evaluate():
    checks = GREEN[1:] + [{"name": "test", "status": "IN_PROGRESS", "conclusion": None}]
    decision = dec(_live(checks=checks))
    assert decision.merge is False
    assert any("not completed" in r for r in decision.reasons)


def test_a_missing_completion_trailer_blocks_via_evaluate():
    decision = dec(_live(commit_messages=["feat: no trailer"]))
    assert decision.merge is False
    assert any("Completes-Bead: hy-273" in r and "land OPEN" in r for r in decision.reasons)


def test_an_unmet_merge_after_constraint_blocks_via_evaluate():
    live = _live(verdicts=_dual_model(constraints="merge_after:#999"))
    decision = dec(live, merged_prs=set())  # #999 not merged
    assert decision.merge is False
    assert any("until #999 lands" in r for r in decision.reasons)


def test_a_bounce_at_the_head_blocks_and_does_not_land():
    live = _live(verdicts=_dual_model(word="BOUNCE"))
    decision = dec(live)
    assert decision.merge is False
    assert any("fail safe" in r for r in decision.reasons)


# --- config: min_approvals floors at dual-model, never below ---


@pytest.mark.parametrize(
    "raw,expected",
    [("", 2), ("x", 2), ("0", 2), ("-1", 2), ("1", 2), ("2", 2), ("3", 3)],
)
def test_min_approvals_never_drops_below_two(raw, expected):
    assert rp.min_approvals({rp.MIN_APPROVALS_ENV: raw} if raw else {}) == expected


# --- the skip-reason log, preflight, and dry-run (hy-wpqa) ---
#
# The #305 stall: the poll RAN but skipped a fully-clear PR, and the failure was
# invisible -- it "reported polling" while landing nothing. These bind the log
# that makes a skip cause visible, the preflight that names a missing config once
# instead of a wall of identical skips, and the dry-run that decides without
# merging. `_dual_model` posts as ONE login (bsovs) with two roles, so the
# clear-path test IS the #305 regression: one account, two roles, would land.


def _wire_clear(monkeypatch, *, live=None, prs=("273",)):
    monkeypatch.setenv("HYPERSET_CRITIC_LOGINS", "bsovs")
    monkeypatch.setattr(rp, "open_pr_numbers", lambda: list(prs))
    monkeypatch.setattr(rp, "_ensure_objects", lambda branch: None)
    monkeypatch.setattr(rp.mp, "_fetch", lambda pr: live if live is not None else _live())
    monkeypatch.setattr(
        rp.gate, "gate_describes_would_land", lambda *, tree_id, base, head: tree_id == TREE
    )
    # The merged-tree guard reads the graph (would-land + head tree); these
    # poll_once tests carry fabricated shas, so stub it clean like `describes`.
    # Its own arms are covered by the merged_tree_reason / suite_green_at_tree unit
    # tests above.
    monkeypatch.setattr(rp, "merged_tree_guard", lambda **kw: None)
    landed = _Calls()  # a list that also carries `.reconciled`
    monkeypatch.setattr(rp, "land", lambda pr, head: landed.append((pr, head)) or True)
    # Stub the live S4 reconciliation so the poll_once tests are hermetic (it else
    # runs real git/gh/bd/gt). `.reconciled` records the calls for the spy tests.
    monkeypatch.setattr(
        rp, "_reconcile_live", lambda pr, live, head: landed.reconciled.append((pr, head))
    )
    return landed


class _Calls(list):
    """A list of land() calls that also records reconcile() calls, so a test can
    assert both from one fixture return."""

    def __init__(self) -> None:
        super().__init__()
        self.reconciled: list = []


def test_preflight_names_the_missing_login_env(monkeypatch):
    monkeypatch.delenv("HYPERSET_CRITIC_LOGINS", raising=False)
    assert "HYPERSET_CRITIC_LOGINS" in rp.preflight()
    monkeypatch.setenv("HYPERSET_CRITIC_LOGINS", "bsovs")
    assert rp.preflight() is None


def test_a_missing_critic_logins_is_a_named_fatal_not_a_wall_of_skips(monkeypatch, capsys):
    # The #305 root config fault: unset -> every PR skips as unauthorized. It must
    # be one loud FATAL line, and NO PR enumerated, not N identical skip lines.
    monkeypatch.delenv("HYPERSET_CRITIC_LOGINS", raising=False)
    considered = []
    monkeypatch.setattr(rp, "open_pr_numbers", lambda: considered.append(1) or ["305"])
    summary = rp.poll_once()
    out = capsys.readouterr().out
    assert "FATAL CONFIG" in out and "HYPERSET_CRITIC_LOGINS" in out
    assert summary["fatal"] is True
    assert considered == []  # short-circuits before touching any PR


def test_a_clear_305_class_pr_logs_would_merge_and_lands_live(monkeypatch, capsys):
    landed = _wire_clear(monkeypatch)
    summary = rp.poll_once()
    out = capsys.readouterr().out
    assert "poll pass start [LIVE]" in out
    assert "WOULD MERGE" in out
    assert "poll pass done: considered=1 would_merge=1 merged=1 skipped=0" in out
    assert landed == [("273", HEAD)]
    assert landed.reconciled == [("273", HEAD)]  # S4 runs on the merged PR
    assert summary["merged"] == 1


def test_dry_run_logs_would_merge_but_never_lands_or_reconciles(monkeypatch, capsys):
    landed = _wire_clear(monkeypatch)
    summary = rp.poll_once(dry_run=True)
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "WOULD MERGE" in out and "dry-run, not merging" in out
    assert landed == []  # the safe diagnostic never presses the button
    assert landed.reconciled == []  # ...and never closes a bead
    assert summary["would_merge"] == 1 and summary["merged"] == 0


def test_reconcile_runs_only_after_an_actual_merge_not_on_a_skip(monkeypatch, capsys):
    # The stated danger is closing on a NON-merge. A skipped PR must not reconcile.
    landed = _wire_clear(monkeypatch, live=_live(mergeable="CONFLICTING"))
    rp.poll_once()
    assert landed == [] and landed.reconciled == []


def test_reconcile_does_not_run_when_the_merge_button_fails(monkeypatch, capsys):
    # land() returns False (the forge refused) -> no merge counted, no reconcile.
    landed = _wire_clear(monkeypatch)
    monkeypatch.setattr(rp, "land", lambda pr, head: False)
    summary = rp.poll_once()
    assert landed.reconciled == [] and summary["merged"] == 0


def test_each_skipped_pr_logs_exactly_which_gate_failed(monkeypatch, capsys):
    _wire_clear(monkeypatch, live=_live(mergeable="CONFLICTING"))
    monkeypatch.setattr(
        rp, "land", lambda pr, head: (_ for _ in ()).throw(AssertionError("must not land"))
    )
    summary = rp.poll_once()
    out = capsys.readouterr().out
    assert "SKIP" in out and "not MERGEABLE" in out
    assert summary["skipped"] == 1 and summary["merged"] == 0


def test_a_non_git_root_is_a_named_fatal_not_silent_skips(monkeypatch, capsys):
    # The #305 ROOT CAUSE (hy-wpqa): the poll ran from a non-git dir, so every
    # would_land was None and every PR skipped invisibly. Now preflight names it
    # once and considers NO PR.
    monkeypatch.setenv("HYPERSET_CRITIC_LOGINS", "bsovs")
    monkeypatch.setattr(rp.gate, "is_git_repo", lambda: False)
    considered = []
    monkeypatch.setattr(rp, "open_pr_numbers", lambda: considered.append(1) or ["1"])
    summary = rp.poll_once()
    out = capsys.readouterr().out
    assert "FATAL CONFIG" in out and "not a git work tree" in out
    assert summary["fatal"] is True and considered == []


def test_preflight_checks_the_git_root_before_the_login_env(monkeypatch):
    # A non-git root is reported even when the login env is fine -- it is the
    # load-bearing, global fault.
    monkeypatch.setenv("HYPERSET_CRITIC_LOGINS", "bsovs")
    monkeypatch.setattr(rp.gate, "is_git_repo", lambda: False)
    assert "not a git work tree" in rp.preflight()


def test_a_per_pr_git_env_fault_is_loud_and_skips_never_a_silent_none(monkeypatch, capsys):
    # A git ENV fault for ONE PR (an unfetched object) must be a LOUD skip, not a
    # silent None that reads as "not clear". The daemon keeps running.
    _wire_clear(monkeypatch)
    monkeypatch.setattr(rp.gate, "is_git_repo", lambda: True)

    def boom(*, tree_id, base, head):
        raise rp.gate.GitEnvError("git error: bad object")

    monkeypatch.setattr(rp.gate, "gate_describes_would_land", boom)
    summary = rp.poll_once()
    out = capsys.readouterr().out
    assert "GIT ENV FAULT" in out
    assert summary["skipped"] == 1 and summary["merged"] == 0


# --- S4 reconciliation on auto-merge: close the source bead, clear the mq entry,
#     fail-safe and idempotent, never the wrong bead (hy-n0ge) ---

_MERGE_SHA = "abc123def456abc123def456abc123def456abcd"


def _reconcile(
    *, messages, branch="crew/hy-273-x", graded=TREE, landed=TREE, status="open", mq=None
):
    closed: list[tuple[str, str]] = []
    cleared: list[str] = []
    rp.reconcile_merged_pr(
        "273",
        commit_messages=messages,
        branch=branch,
        graded_tree=graded,
        landed_tree=landed,
        merge_sha=_MERGE_SHA,
        bead_status=lambda b: status,
        close_bead=lambda b, r: closed.append((b, r)) or True,
        mq_id_for_branch=lambda b: mq,
        mq_clear=lambda m: cleared.append(m) or True,
    )
    return closed, cleared


def test_reconcile_closes_the_single_trailer_bead_with_a_landed_reason(capsys):
    closed, _ = _reconcile(messages=["x\n\nCompletes-Bead: hy-n0ge"])
    assert [b for b, _ in closed] == ["hy-n0ge"]
    reason = closed[0][1]
    assert "auto-merge PR #273" in reason and "== landed tree" in reason


def test_reconcile_closes_nothing_when_the_trailer_is_missing(capsys):
    closed, _ = _reconcile(messages=["feat: no trailer"])
    assert closed == []
    assert "no Completes-Bead trailer" in capsys.readouterr().out


def test_reconcile_closes_nothing_when_the_trailer_is_ambiguous(capsys):
    closed, _ = _reconcile(messages=["a\n\nCompletes-Bead: hy-aaa", "b\n\nCompletes-Bead: hy-bbb"])
    assert closed == []
    assert "AMBIGUOUS" in capsys.readouterr().out


def test_reconcile_does_not_close_a_bead_that_does_not_resolve(capsys):
    closed, _ = _reconcile(messages=["x\n\nCompletes-Bead: hy-ghost"], status=None)
    assert closed == []
    assert "does not resolve" in capsys.readouterr().out


def test_reconcile_is_idempotent_on_an_already_closed_bead(capsys):
    closed, _ = _reconcile(messages=["x\n\nCompletes-Bead: hy-n0ge"], status="closed")
    assert closed == []  # no re-close, no error
    assert "already closed" in capsys.readouterr().out


def test_reconcile_aborts_and_closes_nothing_when_landed_tree_differs(capsys):
    # The reason would claim graded == landed; if a race landed a different tree,
    # close NOTHING and shout. The claim must be true, not asserted.
    closed, cleared = _reconcile(
        messages=["x\n\nCompletes-Bead: hy-n0ge"], graded=TREE, landed=OTHER_TREE
    )
    assert closed == [] and cleared == []
    assert "reconcile ABORTED" in capsys.readouterr().out


def test_reconcile_aborts_when_a_tree_is_unknown(capsys):
    closed, _ = _reconcile(messages=["x\n\nCompletes-Bead: hy-n0ge"], graded=None)
    assert closed == []
    assert "reconcile ABORTED" in capsys.readouterr().out


def test_reconcile_clears_an_mq_entry_when_one_matches_the_branch(capsys):
    _, cleared = _reconcile(messages=["x\n\nCompletes-Bead: hy-n0ge"], mq="hy-wisp-42")
    assert cleared == ["hy-wisp-42"]
    assert "cleared mq entry hy-wisp-42" in capsys.readouterr().out


def test_reconcile_a_crew_pr_with_no_mq_entry_is_a_logged_no_op(capsys):
    _, cleared = _reconcile(messages=["x\n\nCompletes-Bead: hy-n0ge"], mq=None)
    assert cleared == []
    assert "no mq entry" in capsys.readouterr().out


# --- hy-bclr: the stale-BOUNCE residue surfaces on the autonomous SKIP path too ---


def test_a_stale_bounce_residue_appears_in_the_poll_skip_reasons():
    # A head that was BOUNCED at an earlier sha and carries no live verdict must
    # SKIP (fail-closed on no live MERGE) AND its skip reasons must mark it a
    # re-grade, so the autonomous log distinguishes it from a never-graded PR --
    # the poll reuses mp.evaluate, so the residue rides through with no poll change.
    bounced = mp.parse_verdicts(f"## VERDICT BOUNCE - PR #273 @ {MOVED}", author=BSOVS)
    decision = dec(_live(verdicts=bounced))
    assert decision.merge is False
    assert any("RE-GRADE" in r and "fail OPEN" in r for r in decision.reasons)


# --- hy-ftyp: the residual merged-tree-red guard for genuinely-new non-FF trees ---

MT_HEAD_TREE = "e" * 40  # the head's own tree (a FF would-land equals this)
MT_MERGED = "f" * 40  # a genuinely-new non-FF merged tree (main advanced into it)


def test_merged_tree_reason_ff_owes_no_suite_run():
    # would_land == head tree: a fast-forward / content-equal tree the head's own
    # CI already ran green -- run_suite must NOT even be called (no trivial-FF cost).
    calls: list[str] = []
    reason = rp.merged_tree_reason(
        head_tree=MT_HEAD_TREE,
        would_land=MT_HEAD_TREE,
        run_suite=lambda tree: calls.append(tree) or True,
    )
    assert reason is None
    assert calls == []


def test_merged_tree_reason_conflict_is_not_this_guards_concern():
    # A conflict (would_land None) is refused upstream (not MERGEABLE); this guard
    # says nothing and runs nothing.
    calls: list[str] = []
    reason = rp.merged_tree_reason(
        head_tree=MT_HEAD_TREE,
        would_land=None,
        run_suite=lambda tree: calls.append(tree) or True,
    )
    assert reason is None
    assert calls == []


def test_merged_tree_reason_non_ff_green_passes():
    reason = rp.merged_tree_reason(
        head_tree=MT_HEAD_TREE, would_land=MT_MERGED, run_suite=lambda tree: True
    )
    assert reason is None


def test_merged_tree_reason_non_ff_red_blocks_and_ran_at_the_merged_tree():
    calls: list[str] = []
    reason = rp.merged_tree_reason(
        head_tree=MT_HEAD_TREE,
        would_land=MT_MERGED,
        run_suite=lambda tree: calls.append(tree) or False,
    )
    assert reason is not None and "non-FF" in reason
    assert calls == [MT_MERGED]  # the suite ran at exactly the merged tree, and it red


# --- decide: the semantic-conflict fixture is blocked; a clean FF is not ---


def test_decide_blocks_when_the_merged_tree_check_returns_a_reason():
    # Wiring: whatever reason the merged-tree guard returns (a red merged tree)
    # blocks a dual-model-authorized, otherwise-clear PR from auto-landing. The
    # semantic-conflict block itself is proven at the reason level by
    # test_merged_tree_reason_non_ff_red_blocks_and_ran_at_the_merged_tree.
    red = "the would-land tree fedcba987654 is a genuinely-new non-FF merge ... suite red"
    decision = dec(merged_tree_check=lambda **_: red)
    assert decision.merge is False
    assert any("non-FF merge" in r for r in decision.reasons)


def test_a_clean_ff_needs_no_merged_tree_suite_and_lands():
    # The default guard returns None (FF / merged-tree green); the PR still lands.
    assert dec().merge is True


def test_the_merged_tree_guard_is_not_run_on_an_already_blocked_pr():
    # The suite is expensive; a PR blocked for another reason (a pending check)
    # must not trigger it. The guard is gated on `not reasons`.
    calls: list[dict] = []

    def spy(**kw):
        calls.append(kw)
        return "RED"

    checks = GREEN[1:] + [{"name": "test", "status": "IN_PROGRESS", "conclusion": None}]
    decision = dec(_live(checks=checks), merged_tree_check=spy)
    assert decision.merge is False
    assert calls == []  # never consulted -> the suite never ran on a doomed PR


def test_the_merged_tree_guard_is_not_run_when_the_tree_is_not_dual_model_authorized():
    # A stale-tree (unauthorized) PR is blocked before the guard, so the merged
    # tree is confirmed only for a tree the dual-model already pinned to what lands.
    calls: list[dict] = []

    def spy(**kw):
        calls.append(kw)
        return None

    decision = dec(merged_tree_check=spy, describes=describes_at(OTHER_TREE))
    assert decision.merge is False
    assert calls == []


# --- the runner fails closed ---


def test_suite_green_at_tree_fails_closed_on_an_unresolvable_tree():
    # commit-tree of a tree oid that does not exist fails; the runner returns False
    # (never True, never a crash), so an unconfirmable merged tree never clears.
    assert rp.suite_green_at_tree("d" * 40) is False
