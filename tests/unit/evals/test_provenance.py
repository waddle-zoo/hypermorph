"""A recording session names one commit, and it is one anybody can resolve (hy-r1i0).

Every test here builds a real repository and puts it in the state that produced
the defect, because the defect is entirely about what git says: a commit that
exists as an object and is reachable from nothing reads exactly like a good one
until someone else tries to look it up.

The negative controls are the point (hq-xneo). Each refusal is paired with the
world in which it must NOT fire -- a clean tree, a commit a branch reaches, a
session whose HEAD held still -- because a checker that refuses everything
passes every test a checker that refuses nothing fails, and neither is the one
being asked for.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hyperset.evals import provenance
from hyperset.evals.cases import load_cases
from hyperset.evals.pins import RunPins, repository_pins
from hyperset.evals.provenance import (
    CommitMoved,
    CommitOffDefaultBranch,
    CommitUnresolvable,
    DefaultBranchUnverified,
    HistoryIncomplete,
    RecordingSession,
    TreeDirty,
    fetch_scope_evidence,
    observe_the_default_branch,
    recording_session,
    refuse_a_commit_off_the_default_branch,
    refuse_a_dirty_tree,
    refuse_an_unresolvable_commit,
    session_commit,
    truncation_evidence,
)
from hyperset.evals.recording import GOVERNED_ARM, RECORDING_SCHEMA_VERSION, Recording
from hyperset.evals.run import write_recording

HOST = {"digest": "sha256:1c2f3d4e5a6b", "quantization": "Q4_K_M", "ollama_version": "0.32.4"}

CASE = next(case for case in load_cases() if case.id == "revenue_by_region")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path) -> Path:
    """A repository with one commit and a clean tree.

    Identity is configured per repository rather than inherited, so the test
    holds on a machine with no global git identity.
    """
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "provenance@example.test")
    git(root, "config", "user.name", "Provenance")
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    commit_all(root, "first")
    return root


@pytest.fixture(autouse=True)
def no_session_carried_between_tests(monkeypatch):
    """`session_commit` caches in module state, so each test starts with none."""
    monkeypatch.setattr(provenance, "_SESSION", None)


def recording_pinned_to(commit: str) -> Recording:
    """A recording that is uninteresting except for the commit it names."""
    return Recording(
        run_id="e" * 32,
        schema_version=RECORDING_SCHEMA_VERSION,
        arm=GOVERNED_ARM,
        case_id="revenue_by_region",
        task_version="sha256:0000",
        git_commit=commit,
        recorded_at="2026-07-30T00:00:00+00:00",
        pins=RunPins(**{**repository_pins(GOVERNED_ARM), **HOST}),
        trace={},
        source_refs=[],
    )


def dangle(repo: Path) -> str:
    """Make a commit the way the checkpoint hook did, then orphan it.

    `commit` then `reset --hard` back is the whole mechanism hy-r1i0 measured,
    compressed: the object survives, every ref forgets it, and a fresh clone or
    a `gc` would not carry it at all.
    """
    first = git(repo, "rev-parse", "HEAD")
    (repo / "tracked.txt").write_text("checkpointed\n", encoding="utf-8")
    checkpoint = commit_all(repo, "WIP: checkpoint (auto)")
    git(repo, "reset", "--hard", first)
    return checkpoint


def test_the_orphaned_checkpoint_still_exists_as_an_object(repo):
    """The premise every refusal below rests on, asserted rather than assumed.

    If `reset --hard` deleted the object, refusing an unreachable commit would
    be indistinguishable from refusing a missing one, and the tests that follow
    would prove the weaker check.
    """
    checkpoint = dangle(repo)

    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{checkpoint}^{{commit}}"], cwd=repo, capture_output=True
        ).returncode
        == 0
    ), "the checkpoint is still a readable object here"
    assert not git(repo, "for-each-ref", "--contains", checkpoint), "and no ref reaches it"


def test_a_recording_pinned_to_a_dangling_commit_is_not_persisted(repo, tmp_path):
    """The artifact the refinery found on dag's branch, refused at the door.

    Three of four recordings there pinned `WIP: checkpoint (auto)` commits
    reachable from no branch. Nothing stopped them being written, and once
    written they were committed and reviewed like evidence.
    """
    checkpoint = dangle(repo)
    path = tmp_path / "out" / "revenue_by_region.json"

    with pytest.raises(CommitUnresolvable) as raised:
        write_recording(recording_pinned_to(checkpoint), path, root=repo)

    assert "reachable from no ref" in str(raised.value)
    assert checkpoint in str(raised.value), "the refusal has to name the commit it refused"
    assert not path.exists(), "refusing after writing leaves the artifact an unwary add -A ships"


def test_a_recording_pinned_to_a_commit_a_ref_reaches_is_persisted(repo, tmp_path):
    """The control. Without it every assertion above passes on a checker that
    refuses unconditionally, which would make recording impossible."""
    path = tmp_path / "out" / "revenue_by_region.json"

    write_recording(recording_pinned_to(git(repo, "rev-parse", "HEAD")), path, root=repo)

    assert path.exists()


def test_a_recording_that_pinned_nothing_is_refused(repo, tmp_path):
    """An empty field must not read as a commit that resolved -- the reason
    `PinsIncomplete` exists apart from `PinMismatch`, one field over."""
    path = tmp_path / "out" / "revenue_by_region.json"

    with pytest.raises(CommitUnresolvable) as raised:
        write_recording(recording_pinned_to("   "), path, root=repo)

    assert "pinned no commit at all" in str(raised.value)
    assert not path.exists()


def test_a_commit_no_repository_has_is_refused_before_reachability(repo):
    """A sha nobody ever made -- what the unit fixtures use -- is refused as an
    absent object, not as an unreachable one, so the message points at the
    right thing."""
    with pytest.raises(CommitUnresolvable) as raised:
        refuse_an_unresolvable_commit("a" * 40, root=repo)

    assert "is not a commit" in str(raised.value)


def test_a_checkpoint_landing_mid_session_is_refused_rather_than_relabelled(repo):
    """The measured failure, at the instant it happens.

    A session pins the commit under test; the hook commits while the run is in
    flight; the next case asks the session what it is recording. Before this,
    the answer was a fresh `git rev-parse HEAD` and the run carried on, which is
    how one 17-minute session wrote four recordings at two commits.
    """
    session = RecordingSession.establish(repo)
    (repo / "tracked.txt").write_text("mid-run\n", encoding="utf-8")
    checkpoint = commit_all(repo, "WIP: checkpoint (auto)")

    with pytest.raises(CommitMoved) as raised:
        session.reaffirm()

    assert session.commit in str(raised.value) and checkpoint in str(raised.value), (
        "the refusal names both commits, because which one the evidence belongs to is the question"
    )
    assert provenance.head_commit(repo) == checkpoint, (
        "and re-reading HEAD -- what run_case used to do per case -- would have returned the "
        "checkpoint silently, which is the behaviour this replaces"
    )


def test_a_session_whose_head_held_still_keeps_recording(repo):
    """The control for the one above: reaffirming an unmoved session is not an
    event, or no recording session could run more than one case."""
    session = RecordingSession.establish(repo)

    assert session.reaffirm() == session.commit == git(repo, "rev-parse", "HEAD")


def test_the_session_commit_is_observed_once_and_compared_afterwards(repo):
    """`session_commit` is the seam `run_case` calls, so the caching is its own
    behaviour to hold: two calls agree, and a commit between the second and the
    third is refused rather than adopted."""
    first = session_commit(root=repo)

    assert session_commit(root=repo) == first

    (repo / "tracked.txt").write_text("mid-run\n", encoding="utf-8")
    commit_all(repo, "WIP: checkpoint (auto)")

    with pytest.raises(CommitMoved):
        session_commit(root=repo)


def test_one_process_records_under_one_run_id_and_two_sessions_do_not_share_it(repo):
    """What tells two runs of one case apart, and it cannot be derived (hy-qc4u).

    Two sessions at ONE tree -- the pair #25's close condition requires
    compared -- produce the same commit by construction and the same pins by
    construction, because `StabilityReport` refuses a pin drift between
    repetitions. So every identifier the recorder already had is constant
    across exactly the two runs that must be told apart, which is why this is
    minted rather than computed.

    Both halves are asserted: one id for the whole process, so a session's cases
    do not look like several sessions, and a different id for a session
    established afresh, so two sessions do not look like one.
    """
    first = recording_session(root=repo)

    assert recording_session(root=repo).run_id == first.run_id
    assert first.run_id and len(first.run_id) == 32

    monkeypatched = RecordingSession.establish(repo)

    assert monkeypatched.run_id != first.run_id
    assert monkeypatched.commit == first.commit


def test_a_session_will_not_start_from_a_tree_its_commit_does_not_describe(repo):
    """Refusing a dirty tree is also what leaves the hook nothing to commit.

    The pin claims a commit produced the evidence, and an uncommitted edit is
    both a way for that claim to be false and the precondition for a checkpoint
    landing mid-run.
    """
    (repo / "tracked.txt").write_text("uncommitted\n", encoding="utf-8")

    with pytest.raises(TreeDirty) as raised:
        RecordingSession.establish(repo)

    assert "tracked.txt" in str(raised.value), "the refusal names what to commit"


def test_an_untracked_file_is_a_dirty_tree_too(repo):
    """A module nothing has committed yet is imported exactly like a modified
    one, so a session that ignored untracked files would pin a commit that does
    not describe the code that ran."""
    (repo / "extra.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(TreeDirty):
        RecordingSession.establish(repo)


def test_a_clean_tree_is_not_refused(repo):
    """The control: the fixture's own state has to be recordable, or the check
    above is measuring nothing but the checker's willingness to raise."""
    refuse_a_dirty_tree(repo)
    assert RecordingSession.establish(repo).commit == git(repo, "rev-parse", "HEAD")


def test_an_ignored_file_is_not_a_dirty_tree(repo):
    """`.gitignore`d output is not part of what a commit claims to describe, and
    a session that refused it could not run in a repository with a build
    directory."""
    (repo / ".gitignore").write_text("out/\n", encoding="utf-8")
    commit_all(repo, "ignore build output")
    (repo / "out").mkdir()
    (repo / "out" / "artifact.bin").write_text("x\n", encoding="utf-8")

    refuse_a_dirty_tree(repo)


def test_a_moved_head_stops_a_run_before_the_first_token(monkeypatch):
    """`run_case` asks about provenance where it asks about pins: up front.

    The same argument #25 makes about a pin mismatch -- failing after 314
    seconds of inference is a warning with extra steps -- and here it is
    sharper, because the run that would be spent is one whose recording nobody
    could attribute afterwards.

    Two fakes and a tripwire, and the ordering they measure is not a property of
    any of them: a `run_case` that consulted the session after driving the model
    fails this while every fake stays exactly as it is.
    """
    from hyperset.evals import run as live_run

    def refuse(**kwargs) -> RecordingSession:
        raise CommitMoved("HEAD moved")

    def never(*args, **kwargs):
        raise AssertionError("the run reached the model after provenance refused it")

    pins = RunPins(**{**repository_pins(GOVERNED_ARM), **HOST})
    monkeypatch.setattr(live_run, "observe_pins", lambda **kwargs: pins)
    monkeypatch.setattr(live_run, "recording_session", refuse)
    monkeypatch.setattr(live_run, "declared_context_window", never)
    monkeypatch.setattr(live_run, "plan_analytics_context", never)

    with pytest.raises(CommitMoved):
        live_run.run_case(CASE, arm=GOVERNED_ARM, session_factory=None)


# --- Ancestry of the default branch (hy-narn) -------------------------------
#
# The stronger rule, and the reading of `--is-ancestor` that hy-jkqi now
# requires: its exit code is not the answer. The shallow arm below is built from
# a REAL `git clone --depth=1`, because that state cannot be simulated -- an
# object you construct in a fixture exists in your own store, which is why an
# earlier attempt at a probe like this measured a different arm than it named.


DEFAULT_BRANCH = "refs/remotes/origin/HEAD"
"""What a clone calls the branch it came from, whatever that branch is named.
`git init` picks `main` or `master` by version and by the machine's config, and
this file is not the place to have an opinion about which."""


def clone(source: Path, into: Path, *args: str) -> Path:
    subprocess.run(
        ["git", "clone", *args, source.as_uri(), str(into)],
        capture_output=True,
        text=True,
        check=True,
    )
    git(into, "config", "user.email", "provenance@example.test")
    git(into, "config", "user.name", "Provenance")
    return into


def exit_code(root: Path, *args: str) -> int:
    return subprocess.run(["git", *args], cwd=root, capture_output=True).returncode


def default_branch_name(working: Path) -> str:
    """Whatever the upstream calls its default branch, read rather than assumed."""
    return git(working, "symbolic-ref", "refs/remotes/origin/HEAD").rsplit("/", 1)[-1]


def measured(working: Path) -> str:
    """The remote's own answer for where the default branch is, over `ls-remote`.

    This is the input the guard refuses to invent for itself: a network read at
    a moment the CALLER chooses, passed in as a value, so the verdict does not
    depend on when the guard happened to run.
    """
    return observe_the_default_branch(working, branch=default_branch_name(working))


@pytest.fixture
def upstream(repo) -> Path:
    """A repository whose history is long enough to truncate, with a branch left
    on an OLD commit that main has since passed.

    Both parts matter to the shallow arm. Depth-1 of a one-commit repository is
    not truncated at all, and a shallow clone only produces two disjoint roots
    when it has two refs to shorten -- which is exactly the real shape: a
    long-lived branch pointing into history main already contains.
    """
    landed = ""
    for index in range(4):
        (repo / "tracked.txt").write_text(f"upstream {index}\n", encoding="utf-8")
        commit = commit_all(repo, f"upstream {index}")
        if index == 0:
            landed = commit
    git(repo, "branch", "landed", landed)
    # A real remote, so the stale-ref fixture below can actually push to it and
    # `ls-remote` can actually answer. `updateInstead` keeps this repository's
    # own worktree consistent with the branch it just accepted, which matters
    # because later clones read it.
    git(repo, "config", "receive.denyCurrentBranch", "updateInstead")
    return repo


def test_a_commit_the_default_branch_contains_is_not_refused(upstream, tmp_path):
    """The control, and it comes first because every refusal below also passes
    on a checker that refuses unconditionally -- which would make recording
    impossible rather than safe."""
    working = clone(upstream, tmp_path / "full")

    refuse_a_commit_off_the_default_branch(
        git(working, "rev-parse", "HEAD"), root=working, default_branch_ref=DEFAULT_BRANCH
    )


def test_an_old_commit_the_default_branch_still_contains_is_not_refused(upstream, tmp_path):
    """The second control, and the one the shallow arm needs: this exact commit
    IS an ancestor here, and the same call refuses in the truncated clone
    below. Without this, that refusal could be a fixture that broke."""
    working = clone(upstream, tmp_path / "full")
    landed = git(working, "rev-parse", "refs/remotes/origin/landed")

    refuse_a_commit_off_the_default_branch(landed, root=working, default_branch_ref=DEFAULT_BRANCH)


def test_a_commit_only_a_side_branch_reaches_is_refused(upstream, tmp_path):
    """The defect hy-narn is about, and the reason the weaker rule is not
    enough: `refuse_an_unresolvable_commit` passes this commit -- a ref does
    reach it -- and it becomes dangling the day that branch is deleted, which is
    the state hy-r1i0 found on disk."""
    working = clone(upstream, tmp_path / "full")
    git(working, "checkout", "-q", "-b", "side")
    (working / "tracked.txt").write_text("only on the side branch\n", encoding="utf-8")
    unlanded = commit_all(working, "not on the default branch")

    refuse_an_unresolvable_commit(unlanded, root=working)

    with pytest.raises(CommitOffDefaultBranch) as raised:
        refuse_a_commit_off_the_default_branch(
            unlanded,
            root=working,
            default_branch_ref=DEFAULT_BRANCH,
            default_branch_sha=measured(working),
        )

    assert unlanded in str(raised.value), "the refusal names the commit it refused"
    assert "re-record" in str(raised.value), "and what to do about it"


def test_a_truncated_history_refuses_to_answer_rather_than_answering_no(upstream, tmp_path):
    """The measured false, reproduced from a real depth-1 clone.

    Every precondition the weaker checks ask about is SATISFIED here -- asserted
    below rather than assumed, because if the object did not resolve this would
    be measuring the unresolvable arm wearing a hat. The commit resolves, a ref
    reaches it, and it is genuinely an ancestor of main (the control above says
    so from a full clone of the same upstream). `--is-ancestor` still exits
    non-zero, because each shallow ref's boundary commit presents as a root.
    """
    working = clone(upstream, tmp_path / "shallow", "--depth=1", "--no-single-branch")
    landed = git(working, "rev-parse", "refs/remotes/origin/landed")

    assert git(working, "rev-parse", "--is-shallow-repository") == "true", (
        "the fixture is the state"
    )
    assert exit_code(working, "cat-file", "-e", f"{landed}^{{commit}}") == 0, "the object resolves"
    refuse_an_unresolvable_commit(landed, root=working)

    with pytest.raises(HistoryIncomplete) as raised:
        refuse_a_commit_off_the_default_branch(
            landed, root=working, default_branch_ref=DEFAULT_BRANCH
        )

    assert "no common ancestor" in str(raised.value)
    assert "fetch --unshallow" in str(raised.value), "a refusal that does not say how to fix it"


def test_the_truncated_fixture_would_fool_a_checker_that_read_the_exit_code(upstream, tmp_path):
    """Why the arm above is not tautological, asserted rather than described.

    A guard that trusted `--is-ancestor` would report a confident, wrong
    NOT-ON-MAIN on this fixture -- the whole defect, in one assertion. If this
    ever starts passing, the fixture stopped reproducing and the arm above is
    measuring nothing.
    """
    working = clone(upstream, tmp_path / "shallow", "--depth=1", "--no-single-branch")
    landed = git(working, "rev-parse", "refs/remotes/origin/landed")

    assert exit_code(working, "merge-base", "--is-ancestor", landed, DEFAULT_BRANCH) != 0


def test_a_truncated_history_still_accepts_the_ancestries_it_can_see(upstream, tmp_path):
    """The other half of the one-primitive rule, and the reason truncation is
    not a gate: truncation can hide a path, never fabricate one. HEAD in a
    shallow clone is on the default branch and reads as such, so the guard
    answers YES here instead of refusing every question a shortened clone asks.
    """
    working = clone(upstream, tmp_path / "shallow", "--depth=1", "--no-single-branch")

    assert git(working, "rev-parse", "--is-shallow-repository") == "true"
    refuse_a_commit_off_the_default_branch(
        git(working, "rev-parse", "HEAD"), root=working, default_branch_ref=DEFAULT_BRANCH
    )


def test_an_unresolvable_default_branch_is_refused_rather_than_answered(upstream, tmp_path):
    """hy-jkqi one level up. An unfetched default branch, a fork whose branch is
    not called `main`, a typo -- each would otherwise read as "your commit is
    not on the default branch", which is the same false in a different costume.
    """
    working = clone(upstream, tmp_path / "full")

    with pytest.raises(HistoryIncomplete) as raised:
        refuse_a_commit_off_the_default_branch(
            git(working, "rev-parse", "HEAD"),
            root=working,
            default_branch_ref="refs/remotes/origin/no-such-branch",
        )

    assert "no-such-branch does not resolve" in str(raised.value), "it names what it could not find"


def test_an_absent_object_is_refused_as_absent_rather_than_as_off_branch(upstream, tmp_path):
    """Ordering, and not a duplicate of the arm in the section above: the object
    check runs first, so a sha nobody made reports as absent. The two refusals
    have different repairs, which is the only reason they are different
    classes."""
    working = clone(upstream, tmp_path / "full")

    with pytest.raises(CommitUnresolvable):
        refuse_a_commit_off_the_default_branch(
            "a" * 40, root=working, default_branch_ref=DEFAULT_BRANCH
        )


def test_the_check_that_gates_recording_is_still_the_weaker_one(upstream, tmp_path):
    """The boundary this whole change had to respect, kept as a test because
    nothing else states it: `RecordingSession.establish` asks only for
    reachability. A commit being recorded is normally NOT yet on the default
    branch -- recording before merging is the ordinary case -- so tightening the
    session check in place would have made recording impossible until merge. The
    stronger rule belongs to standing checks over committed recordings, and this
    is the world that tells the two apart.
    """
    working = clone(upstream, tmp_path / "full")
    git(working, "checkout", "-q", "-b", "recording")
    (working / "tracked.txt").write_text("mid-review\n", encoding="utf-8")
    commit_all(working, "the commit a recording is taken at")

    session = RecordingSession.establish(root=working)

    assert session.commit == git(working, "rev-parse", "HEAD")
    with pytest.raises(CommitOffDefaultBranch):
        refuse_a_commit_off_the_default_branch(
            session.commit,
            root=working,
            default_branch_ref=DEFAULT_BRANCH,
            default_branch_sha=measured(working),
        )


# --- The two arms critic bounced at 1bff39d ---------------------------------
#
# Both are the SAME defect this section exists to close, one level out: a seat
# publishing a local, time-dependent view as a fact about the repository. One
# reads a stored measurement of a moving reference; the other reads its own
# object store as the set of objects that exist.


def stale_tracking_ref(upstream: Path, tmp_path: Path) -> tuple[Path, str]:
    """A clone whose `origin/<default>` is real but out of date, and the commit
    that landed while it was not looking.

    Constructed by pushing and then winding the tracking ref back, because that
    is what actually happens: the branch moves on the forge and this seat has
    not fetched since. Nothing here is faked -- the commit IS on the upstream
    default branch, which is exactly what makes the stale reading wrong.
    """
    working = clone(upstream, tmp_path / "stale")
    branch = default_branch_name(working)
    was = git(working, "rev-parse", f"refs/remotes/origin/{branch}")

    git(working, "checkout", "-q", "-b", "landing")
    (working / "tracked.txt").write_text(
        "landed while the seat was not looking\n", encoding="utf-8"
    )
    landed = commit_all(working, "a commit that reaches the default branch")
    git(working, "push", "-q", "origin", f"HEAD:{branch}")
    git(working, "update-ref", f"refs/remotes/origin/{branch}", was)

    assert git(working, "rev-parse", f"refs/remotes/origin/{branch}") == was, "the seat is behind"
    assert measured(working) == landed, "and the branch really did move to this commit"
    return working, landed


def test_a_stale_tracking_ref_refuses_instead_of_reporting_the_finding(upstream, tmp_path):
    """The arm that made an unqualified false statement.

    Same clone, same pin, nothing different but how long since the seat fetched.
    The old behaviour called this "a genuine finding" and told the caller to
    land the commit or re-record -- for a commit already on the default branch,
    whose actual repair is `git fetch`. That is hy-narn's own loop: re-record at
    a pushed commit, land it, and every seat that has not fetched refuses the
    new pin.
    """
    working, landed = stale_tracking_ref(upstream, tmp_path)

    with pytest.raises(DefaultBranchUnverified) as raised:
        refuse_a_commit_off_the_default_branch(
            landed, root=working, default_branch_ref=DEFAULT_BRANCH
        )

    assert "git fetch" in str(raised.value), "the repair is a fetch"
    assert "re-record" not in str(raised.value).split("would otherwise")[0], (
        "and it must not send anyone to re-record a commit that is already on the branch"
    )


def test_a_measured_sha_that_disagrees_with_the_tracking_ref_is_refused(upstream, tmp_path):
    """Passing the measurement is not a way to silence the check.

    The caller here does exactly what the refusal above asks -- measures the
    branch with `ls-remote` -- and the answer disagrees with what this clone
    holds. That is the staleness made visible rather than resolved, so it still
    refuses, now naming both shas.
    """
    working, landed = stale_tracking_ref(upstream, tmp_path)

    with pytest.raises(DefaultBranchUnverified) as raised:
        refuse_a_commit_off_the_default_branch(
            landed,
            root=working,
            default_branch_ref=DEFAULT_BRANCH,
            default_branch_sha=measured(working),
        )

    assert landed in str(raised.value), "the refusal names the commit"
    assert "two different commits" in str(raised.value), "and states what it measured"
    assert "has not fetched" not in str(raised.value), (
        "and NOT which of the two causes produced them -- this arm cannot tell a stale ref "
        "from a stale measurement, and hy-htov is the bug it filed by guessing"
    )


def test_fetching_settles_it_and_the_same_call_then_accepts(upstream, tmp_path):
    """The control that makes the two arms above mean something: the ONLY thing
    that changes here is that the seat fetched. Same repository, same commit,
    same call, and the verdict flips from a refusal to an acceptance -- which is
    the proof that the refusal was about the seat's view and never about the
    commit."""
    working, landed = stale_tracking_ref(upstream, tmp_path)

    git(working, "fetch", "-q", "origin")

    refuse_a_commit_off_the_default_branch(
        landed,
        root=working,
        default_branch_ref=DEFAULT_BRANCH,
        default_branch_sha=measured(working),
    )


def test_a_stale_ref_never_blocks_the_yes_direction(upstream, tmp_path):
    """The asymmetry, restated for staleness rather than truncation: a commit an
    OLDER default branch already contained, a newer one still contains. So YES
    needs no measurement and does not ask for one -- otherwise every seat in the
    town would have to reach the network before it could accept anything."""
    working, _ = stale_tracking_ref(upstream, tmp_path)
    old_and_landed = git(working, "rev-parse", "refs/remotes/origin/HEAD")

    refuse_a_commit_off_the_default_branch(
        old_and_landed, root=working, default_branch_ref=DEFAULT_BRANCH
    )


def test_an_object_this_seat_lacks_is_reported_as_ignorance_not_as_absence(upstream, tmp_path):
    """ "Nobody has this object" is a claim about the world, and a shallow seat
    is not entitled to make it.

    Measured, not theoretical: three of five seats in this town are shallow, and
    the object arm is the one a truncated clone hits FIRST, so this is the
    sentence such a seat actually sees. The commit below is on the upstream
    default branch the whole time -- asserted from the upstream itself -- and
    this clone simply does not have it.
    """
    working = clone(upstream, tmp_path / "shallow", "--depth=1", "--no-single-branch")
    older = git(upstream, "rev-parse", "HEAD~2")

    assert exit_code(upstream, "merge-base", "--is-ancestor", older, "HEAD") == 0, (
        "the commit is on the upstream default branch"
    )
    assert exit_code(working, "cat-file", "-e", f"{older}^{{commit}}") != 0, "and absent here"

    with pytest.raises(HistoryIncomplete) as raised:
        refuse_a_commit_off_the_default_branch(
            older, root=working, default_branch_ref=DEFAULT_BRANCH
        )

    assert "fetch --unshallow" in str(raised.value), "the repair, not a category"
    assert "nobody has" not in str(raised.value), "and no claim about repositories it cannot see"


def test_the_weaker_check_still_says_absent_because_that_is_its_contract(upstream, tmp_path):
    """Why the arm above is a re-raise in the new function and not an edit to
    `refuse_an_unresolvable_commit`: the recording path asks a narrower question
    -- can THIS repository resolve what it is about to write down -- and the
    answer there really is no. Changing it would move the recording boundary
    that `test_the_check_that_gates_recording_is_still_the_weaker_one` holds."""
    working = clone(upstream, tmp_path / "shallow", "--depth=1", "--no-single-branch")
    older = git(upstream, "rev-parse", "HEAD~2")

    with pytest.raises(CommitUnresolvable):
        refuse_an_unresolvable_commit(older, root=working)


def test_an_absent_object_in_a_complete_clone_is_still_reported_as_absent(upstream, tmp_path):
    """The negative control for the re-raise, and the reason it is conditional
    on evidence rather than applied everywhere: with nothing truncated, a sha
    nobody made is genuinely absent and saying so is not overreach."""
    working = clone(upstream, tmp_path / "full")

    assert truncation_evidence(working) == (), "nothing here is truncated"
    with pytest.raises(CommitUnresolvable):
        refuse_a_commit_off_the_default_branch(
            "a" * 40, root=working, default_branch_ref=DEFAULT_BRANCH
        )


def test_an_empty_pin_is_never_excused_by_a_truncated_repository(upstream, tmp_path):
    """The one absence a shallow clone cannot explain away. A recording that
    pinned nothing pinned nothing in every repository, so the re-raise must not
    dress it up as this seat's ignorance."""
    working = clone(upstream, tmp_path / "shallow", "--depth=1", "--no-single-branch")

    assert truncation_evidence(working), "the repository IS truncated"
    with pytest.raises(CommitUnresolvable):
        refuse_a_commit_off_the_default_branch("", root=working, default_branch_ref=DEFAULT_BRANCH)


def test_truncation_evidence_names_the_repair_rather_than_a_category(upstream, tmp_path):
    """A list, not a boolean, because the three ways to hold a partial
    repository have three different repairs and only one of them announces
    itself through `--is-shallow-repository`."""
    shallow = clone(upstream, tmp_path / "shallow", "--depth=1", "--no-single-branch")

    found = truncation_evidence(shallow)

    assert len(found) == 1 and "fetch --unshallow origin" in found[0]


def test_a_dangling_object_keeps_its_own_words_in_a_truncated_clone(upstream, tmp_path):
    """The third bounce, and the sharpest of the three: `CommitUnresolvable` has
    two arms and only one of them is this seat's opinion.

    An object the store does not hold may be one every other clone holds. An
    object the store DOES hold, with no ref reaching it, is the one commit in
    the repository whose opposite is true -- and relabelling it as truncation
    asserts three falsehoods at once: that this root cannot resolve it, that
    this seat lacks it, and that somewhere else has it. `git fetch --unshallow`
    changes none of it.

    Measured, not argued: #164's own head `1784de7f` was found in `crew/hyperion`
    present, with zero refs containing it, one `gc` from vanishing -- after a
    force-push moved the branch out from under it. That is the ordinary end of
    every rebased branch, so this arm fires far more often than the shallow one.
    """
    working = clone(upstream, tmp_path / "shallow", "--depth=1", "--no-single-branch")
    orphan = dangle(working)

    assert truncation_evidence(working), "the seat is flagged truncated"
    assert exit_code(working, "cat-file", "-e", f"{orphan}^{{commit}}") == 0, "and holds the object"

    with pytest.raises(CommitUnresolvable) as raised:
        refuse_a_commit_off_the_default_branch(
            orphan, root=working, default_branch_ref=DEFAULT_BRANCH
        )

    assert "reachable from no ref" in str(raised.value)
    assert "unshallow" not in str(raised.value), "a repair that would not repair anything"


def test_the_relabel_is_keyed_to_the_object_and_not_to_the_seat(upstream, tmp_path):
    """Why the discriminator is `cat-file -e` rather than `truncation_evidence`
    alone. Both commits below are refused from the SAME repository with the same
    truncation evidence, and they get different refusals, because what separates
    them is a fact about the object rather than a fact about the seat.

    This is also the arm that limits hy-f1vw's blast radius: seats flagged
    shallow while cutting nothing would otherwise have every dangling pin in
    town relabelled as their own ignorance.
    """
    working = clone(upstream, tmp_path / "shallow", "--depth=1", "--no-single-branch")
    orphan = dangle(working)
    never_made = "a" * 40

    with pytest.raises(CommitUnresolvable):
        refuse_a_commit_off_the_default_branch(
            orphan, root=working, default_branch_ref=DEFAULT_BRANCH
        )
    with pytest.raises(HistoryIncomplete):
        refuse_a_commit_off_the_default_branch(
            never_made, root=working, default_branch_ref=DEFAULT_BRANCH
        )


def test_a_narrowly_fetched_seat_is_told_to_name_the_object_and_not_to_unshallow(
    upstream, tmp_path
):
    """The live rig state, reproduced (hy-4nyg, hy-4dci).

    Every crew seat fetches `+refs/heads/main:refs/remotes/origin/main` and
    nothing else, so a commit on a pull-request branch was never asked for. The
    seat is NOT truncated -- asserted inside the arm, because that is the whole
    point: `crew/hyperion` unshallowed from 315 commits to 383, its truncation
    evidence went empty, and the head it could not resolve stayed gone. Before
    this arm existed the guard fell through and reported that absence as an
    unqualified finding.
    """
    working = clone(upstream, tmp_path / "narrow", "--single-branch")
    never_fetched = "b" * 40

    assert not truncation_evidence(working), "the seat is whole -- nothing was cut"
    assert fetch_scope_evidence(working), "and it still cannot see past one branch"

    with pytest.raises(HistoryIncomplete) as raised:
        refuse_a_commit_off_the_default_branch(
            never_fetched, root=working, default_branch_ref=DEFAULT_BRANCH
        )

    assert "covers part of the namespace" in str(raised.value), "the refusal names what it measured"
    assert "refs/pull/<n>/head" in str(raised.value), "and a repair that would work"
    assert "--unshallow" in str(raised.value), "only to say it is the wrong one"
    assert "`git fetch --unshallow origin`" not in str(raised.value), (
        "and never prescribes it -- a seat that runs it stays exactly as blind, now holding "
        "a receipt saying it was repaired"
    )


def test_a_seat_that_fetches_everything_gets_no_hint_it_has_not_earned(upstream, tmp_path):
    """The control that keeps the arm above from being vacuous. Same absent
    object, same call, a clone with a WILDCARD refspec and no truncation -- and
    the refusal goes back to being about the object, because here nothing about
    the seat explains the absence."""
    working = clone(upstream, tmp_path / "full")
    never_made = "c" * 40

    assert not truncation_evidence(working) and not fetch_scope_evidence(working)

    with pytest.raises(CommitUnresolvable) as raised:
        refuse_a_commit_off_the_default_branch(
            never_made, root=working, default_branch_ref=DEFAULT_BRANCH
        )

    assert not isinstance(raised.value, HistoryIncomplete)


def test_a_seat_that_is_both_cut_and_narrow_is_told_both_repairs(upstream, tmp_path):
    """`--depth=1` implies `--single-branch`, so the commonest truncated clone in
    town is narrow as well -- and one repair does not stand in for the other. The
    two lists stay separate precisely so this seat gets both sentences."""
    working = clone(upstream, tmp_path / "shallow-and-narrow", "--depth=1")
    absent = "d" * 40

    assert truncation_evidence(working) and fetch_scope_evidence(working)

    with pytest.raises(HistoryIncomplete) as raised:
        refuse_a_commit_off_the_default_branch(
            absent, root=working, default_branch_ref=DEFAULT_BRANCH
        )

    assert "shallow clone" in str(raised.value) and "covers part of the namespace" in str(
        raised.value
    )


def test_the_scope_evidence_reads_the_refspec_and_not_the_ref_count(upstream, tmp_path):
    """The correction that promoted this from my file to the rig (hy-4dci): the
    number of remote refs on disk is a FOSSIL of when the clone was made, not a
    capability. Two seats under identical configuration held 166 refs and 52.

    So the same narrow clone must answer identically before and after it fetches
    a pile of refs by hand, and a wildcard seat must answer empty even holding
    barely any.
    """
    narrow = clone(upstream, tmp_path / "narrow", "--single-branch")
    before = fetch_scope_evidence(narrow)
    git(narrow, "fetch", "-q", "origin", "landed:refs/remotes/origin/landed")

    assert fetch_scope_evidence(narrow) == before, "more refs, same refspec, same answer"

    wide = clone(upstream, tmp_path / "wide", "--depth=1", "--no-single-branch")
    assert not fetch_scope_evidence(wide), "a wildcard seat is not narrow, however little it holds"


def test_an_abbreviated_measurement_of_the_current_tip_is_not_a_stale_seat(upstream, tmp_path):
    """hy-htov. The comparison is a caller's string against 40 hex characters of
    `rev-parse` output, so an abbreviated sha of the very commit the ref is on
    used to earn a refusal diagnosing a staleness that was not there -- and
    prescribing a `git fetch` that would change nothing.

    The seat here has fetched, the ref is current, and the two values name one
    commit. The verdict must therefore be the real finding.
    """
    working = clone(upstream, tmp_path / "full")
    git(working, "checkout", "-q", "-b", "side")
    (working / "tracked.txt").write_text("only on the side branch\n", encoding="utf-8")
    unlanded = commit_all(working, "not on the default branch")

    abbreviated = measured(working)[:12]
    assert len(abbreviated) < 40, "the caller passed a short sha, as callers do"

    with pytest.raises(CommitOffDefaultBranch):
        refuse_a_commit_off_the_default_branch(
            unlanded,
            root=working,
            default_branch_ref=DEFAULT_BRANCH,
            default_branch_sha=abbreviated,
        )


def test_a_name_the_seat_can_resolve_is_still_not_a_measurement_of_the_branch(upstream, tmp_path):
    """hy-eowf. The repair for hy-htov resolved the caller's pin, which admits an
    abbreviated sha -- and also admits every other revision expression this seat
    can resolve, including the name of the default branch and the tracking ref
    the guard is checking. That last one made the guard compare a stored
    measurement to itself and then print `confirmed current`: the parameter
    exists precisely because a tracking ref cannot certify its own currency.

    Each name is asserted to RESOLVE and to AGREE inside the arm, because an
    input that refuses for being unresolvable would leave this vacuous -- and
    because whether it resolves is a fact about the seat, not about the branch.
    Measured: `main` resolved to a stale local branch on this author's checkout
    and refused, while resolving and agreeing on critic's. One input, opposite
    verdicts, same repository.
    """
    working = clone(upstream, tmp_path / "full")
    git(working, "checkout", "-q", "-b", "side")
    (working / "tracked.txt").write_text("only on the side branch\n", encoding="utf-8")
    unlanded = commit_all(working, "not on the default branch")
    tip = git(working, "rev-parse", DEFAULT_BRANCH)

    # The canary: a real measurement of the same branch, at the same moment, in
    # the same scenario, reaches the finding. Without it every arm below passes
    # on a guard that refuses unconditionally.
    with pytest.raises(CommitOffDefaultBranch):
        refuse_a_commit_off_the_default_branch(
            unlanded,
            root=working,
            default_branch_ref=DEFAULT_BRANCH,
            default_branch_sha=measured(working),
        )

    branch = default_branch_name(working)
    for name in (branch, f"origin/{branch}", DEFAULT_BRANCH):
        assert git(working, "rev-parse", name) == tip, (
            f"{name} resolves here and agrees, which is what made it look like a measurement"
        )
        with pytest.raises(DefaultBranchUnverified) as raised:
            refuse_a_commit_off_the_default_branch(
                unlanded,
                root=working,
                default_branch_ref=DEFAULT_BRANCH,
                default_branch_sha=name,
            )
        assert "does not name an object" in str(raised.value), "and the refusal says which input"
        assert "confirmed current" not in str(raised.value)


def test_the_shortest_measurement_the_guard_will_take_is_gits_own_floor(upstream, tmp_path):
    """Where the shape test draws its line, pinned rather than left to the
    constant. Seven hex characters is what `git rev-parse --short` prints, and
    below it a hex string short enough to collide is short enough to be a branch
    name -- so a six-character prefix of the true tip is refused even though it
    is a true prefix of it."""
    working = clone(upstream, tmp_path / "full")
    git(working, "checkout", "-q", "-b", "side")
    (working / "tracked.txt").write_text("only on the side branch\n", encoding="utf-8")
    unlanded = commit_all(working, "not on the default branch")
    tip = git(working, "rev-parse", DEFAULT_BRANCH)

    with pytest.raises(CommitOffDefaultBranch):
        refuse_a_commit_off_the_default_branch(
            unlanded, root=working, default_branch_ref=DEFAULT_BRANCH, default_branch_sha=tip[:7]
        )

    with pytest.raises(DefaultBranchUnverified):
        refuse_a_commit_off_the_default_branch(
            unlanded, root=working, default_branch_ref=DEFAULT_BRANCH, default_branch_sha=tip[:6]
        )


def test_a_wildcard_over_one_namespace_is_still_a_narrow_seat(upstream, tmp_path):
    """hy-m78p. `endswith("*")` read as "fetches everything" and does not mean
    it: a refspec wildcard under a prefix asks for that prefix and nothing else,
    and a seat configured that way was told nothing at all about why an object
    was missing. What makes a refspec total is an empty prefix, not the star."""
    narrow = clone(upstream, tmp_path / "prefixed")
    git(
        narrow,
        "config",
        "remote.origin.fetch",
        "+refs/heads/feature/*:refs/remotes/origin/feature/*",
    )

    assert not truncation_evidence(narrow), "whole history -- the seat was never cut"
    assert fetch_scope_evidence(narrow), "and a prefixed wildcard is still a partial namespace"
    assert "refs/heads/feature/*" in fetch_scope_evidence(narrow)[0], "named, not categorised"

    wide = clone(upstream, tmp_path / "wildcard")
    assert not fetch_scope_evidence(wide), "the control: an empty prefix does fetch everything"


def test_an_unresolvable_measurement_still_refuses(upstream, tmp_path):
    """The other side of comparing by prefix: a seat that does not hold what the
    caller passed compares it against the ref anyway and still refuses. The
    tolerance is for an ABBREVIATION of the tip, never a way for an unknown sha
    to slip through as agreement."""
    working = clone(upstream, tmp_path / "full")
    git(working, "checkout", "-q", "-b", "side")
    (working / "tracked.txt").write_text("only on the side branch\n", encoding="utf-8")
    unlanded = commit_all(working, "not on the default branch")

    with pytest.raises(DefaultBranchUnverified):
        refuse_a_commit_off_the_default_branch(
            unlanded,
            root=working,
            default_branch_ref=DEFAULT_BRANCH,
            default_branch_sha="e" * 40,
        )
