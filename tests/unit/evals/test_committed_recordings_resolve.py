"""Every recording this repository ships pins a commit a later reader can resolve (hy-ow2v).

`refuse_an_unresolvable_commit` has exactly two callers -- `RecordingSession.establish`
and `write_recording` -- and both sit on the PRODUCTION path, checking a commit at the
moment a run records under it. Nothing re-checks a recording once it is committed, so the
ordinary cleanup that FOLLOWS a recording session turns a valid pin into a dangling one
silently: squash the checkpoints away and the object the file names stops being reachable
from anything.

That is not hypothetical. Both governed-arm recordings sat on main pinning
b9e06ebeda47c3e13439f018e032d9a56a4e803c, a `WIP: checkpoint (auto)` commit that existed
as an object in eight clones of this repository and was reachable from a ref in none of
them, while the raw-baseline arm pinned a commit that resolved -- so the arm the
benchmark's headline numbers come from was the unresolvable half, and an audit that
enumerated one of the two arms reported all-clear (hy-ow2v, found by running the hy-r1i0
guard over the committed FILES rather than over a run).

This is the check the production path cannot make: over the files, in the tree they are
committed in, on every gate. And it makes it with the STRONGER rule (hy-d5bx): the standing
check now requires each pin to be an ancestor of the DEFAULT BRANCH, not merely reachable
from some ref. "Some ref" is what the b9e06eb checkpoint satisfied while being reachable
from a ref in none of eight clones; a commit main contains is one every clone resolves for
as long as the repository exists. The production-path guards (`RecordingSession.establish`,
`write_recording`) deliberately stay on the weaker reachability check -- they run against a
live checkout at record time, before the commit could be on main -- so only this
committed-tree walk carries the stronger rule.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

import pytest
import yaml

from hyperset.evals.cases import load_cases
from hyperset.evals.provenance import (
    REPO_ROOT,
    CommitUnresolvable,
    HistoryIncomplete,
    ProvenanceRefused,
    fetch_scope_evidence,
    observe_the_default_branch,
    refuse_a_commit_off_the_default_branch,
    refuse_an_unresolvable_commit,
)
from hyperset.evals.recording import ARMS, Recording
from hyperset.evals.run import (
    RECORDINGS_DIR,
    case_recordings_dir,
    recording_path,
    recordings_of,
)

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def committed_recordings(directory: Path) -> tuple[Path, ...]:
    """Every recording the gate scores, enumerated rather than globbed.

    `score_recordings` enumerates `ARMS` against the case file for a reason this bug
    repeats: a walk over whatever happens to be on disk reports all-clear over an arm
    somebody deleted, and reporting all-clear over an arm nobody read is exactly how the
    unresolvable governed pins survived an audit.
    """
    paths: list[Path] = []
    for arm in ARMS:
        for case in load_cases():
            found = recordings_of(arm, case.id, directory=directory)
            assert found, (
                f"no recording under {case_recordings_dir(arm, case.id, directory=directory)}; "
                "this walk enumerates the arms and cases the gate scores, so a missing file is "
                "a hole in the evidence, not a file to skip"
            )
            paths.extend(found)
    return tuple(paths)


def unresolvable_pins(directory: Path, *, root: Path) -> tuple[tuple[Path, str], ...]:
    """The recordings under `directory` whose `git_commit` `root` cannot resolve.

    Collected rather than raised, so the caller names WHICH file lost its provenance:
    the guard's own message names a commit, and a commit alone does not say which arm it
    belonged to.
    """
    refused: list[tuple[Path, str]] = []
    for path in committed_recordings(directory):
        try:
            refuse_an_unresolvable_commit(Recording.read(path).git_commit, root=root)
        except CommitUnresolvable as refusal:
            refused.append((path, str(refusal)))
    return tuple(refused)


def pins_off_the_default_branch(
    directory: Path, *, root: Path, default_branch_sha: str
) -> tuple[tuple[tuple[Path, str], ...], tuple[tuple[Path, str], ...]]:
    """The stronger reading: recordings whose pin the default branch does not contain.

    `refuse_a_commit_off_the_default_branch` is strictly stronger than the weaker
    reachability guard `unresolvable_pins` uses -- "some ref reaches it" is satisfied by a
    crew branch nobody merged and a branch about to be cleaned up, so a recording can pass
    that today and cite a dangling object next week. A commit the default branch contains
    is one every clone resolves for as long as the repository lives, which is the claim
    `docs/development/benchmark.md` makes about `git_commit`.

    Returns two tuples, because the guard makes two DIFFERENT claims and only one reddens
    the gate. A genuine `ProvenanceRefused` (off-branch, moved, or a dangling object this
    seat HOLDS) is a fact about the pin. `HistoryIncomplete` is a fact about this seat --
    it cannot see enough history to tell -- and a truncated checkout may not turn its own
    ignorance into a finding (hy-jkqi). The caller skips on incomplete-only and fails on
    refused, never the reverse.
    """
    refused: list[tuple[Path, str]] = []
    incomplete: list[tuple[Path, str]] = []
    for path in committed_recordings(directory):
        try:
            # The observed remote head is the ancestry target AND the currency proof.
            # Passing it as `default_branch_ref` too means the check needs only the OBJECT
            # present locally (guaranteed on a `fetch-depth: 0` seat, whose merge commit
            # descends from it), never the `refs/remotes/origin/main` tracking ref by name --
            # a narrow or PR-shaped checkout that never wrote that ref would otherwise route
            # every pin to `incomplete` and skip the whole gate where it must bite.
            refuse_a_commit_off_the_default_branch(
                Recording.read(path).git_commit,
                root=root,
                default_branch_ref=default_branch_sha,
                default_branch_sha=default_branch_sha,
            )
        except HistoryIncomplete as why:
            incomplete.append((path, str(why)))
        except ProvenanceRefused as refusal:
            refused.append((path, str(refusal)))
    return tuple(refused), tuple(incomplete)


def incomplete_checkout(root: Path) -> str:
    """Why THIS checkout might not be able to answer, or an empty string.

    A shallow clone answers a reachability question confidently and wrongly, and this
    check ships onto every seat's completion gate. Most checkouts of this repository are
    shallow -- measure it rather than trusting this sentence, `find ~/gt/hyperset -maxdepth
    4 -name .git` and `git rev-parse --is-shallow-repository` in each -- and they do not
    all hold the same objects, so an undiscriminated failure is intermittent across seats
    and reads like evidence rot. That exact sentence, a local absence reported as a fact
    about the world, cost this town a day: a false P1, a branch-deletion freeze, and a
    governed-arm re-record stopped just short of being spent (hy-jkqi, hy-narn).

    Written as a relation because the count decayed on contact: this docstring said
    "3 of 5 seats" from a five-seat table, and the real population was 8 shallow of 11
    (hy-6hl3). A literal about a moving population is wrong by the time anyone reads it;
    the relation and the command to check it are not.

    The partial-clone case is here because it truncates history identically and reports
    `--is-shallow-repository` false. A narrow fetch refspec is deliberately NOT: these
    pins are ancestors of the default branch, and every seat fetches that.
    """
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"], cwd=root, capture_output=True, text=True
    )
    if shallow.stdout.strip() == "true":
        return "this checkout is SHALLOW"
    promisor = subprocess.run(
        ["git", "config", "--get-regexp", r"remote\..*\.promisor"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if promisor.returncode == 0 and promisor.stdout.strip():
        return "this checkout is a PARTIAL clone"
    return ""


def held_here(paths: Iterable[Path], *, root: Path) -> tuple[Path, ...]:
    """Of the recordings that were refused, the ones whose object `root` actually holds.

    `cat-file -e` is a fact about the object; `incomplete_checkout` is a fact about the
    seat. Which one a message may blame depends on the first, never on the second alone.
    """
    return tuple(
        path
        for path in paths
        if subprocess.run(
            ["git", "cat-file", "-e", f"{Recording.read(path).git_commit}^{{commit}}"],
            cwd=root,
            capture_output=True,
        ).returncode
        == 0
    )


def failure_lead(refused: tuple[tuple[Path, str], ...], *, root: Path) -> str:
    """What a refusal is entitled to blame, given what was measured (hy-7sdq).

    Three cases, and the middle one is the defect this replaced. A truncated checkout is
    not a licence to discount every refusal it produces: three seats in this town are
    shallow AND ship a genuinely dangling governed pin, and they were being told to run
    `--unshallow` and disbelieve a true alarm -- which repairs nothing, since the object
    is absent from a whole clone too.

    Truncation can hide a ref that reaches an object, so a dangling verdict on a shallow
    seat is still worth re-testing. It is not worth discarding. The remedy is phrased to
    TEST the finding rather than to excuse it.
    """
    if not refused:
        return ""
    truncated = incomplete_checkout(root)
    if not truncated:
        return "committed recordings pin commits nobody can resolve:"
    if len(held_here([path for path, _ in refused], root=root)) < len(refused):
        return (
            f"{truncated}, and at least one pin below names an object it does not hold, so "
            "it cannot answer whether these pins resolve -- run `git fetch --unshallow "
            "origin` and re-run before believing any of this:"
        )
    return (
        f"{truncated}, but it HOLDS every object below and no ref here reaches them. "
        "Truncation can hide a containing ref, so `git fetch --unshallow origin` is worth "
        "running -- if these still refuse afterwards the pins are genuinely dangling. This "
        "is not a reason to discount the finding:"
    )


def test_every_committed_recording_names_a_commit_the_default_branch_contains():
    """The standing check, over the recordings actually committed here (hy-d5bx).

    Now the STRONGER rule: not "some ref reaches this" but "the default branch contains
    this". The weaker check let a governed pin sit green on a `WIP: checkpoint` object that
    was reachable from a ref in eight clones and from no ref in any of them; a commit the
    default branch holds cannot rot that way.

    The default branch's position is a value the caller measures, over the network, at a
    moment it chooses -- the guard never reaches the network itself. When nothing answers
    (offline, no remote) the ancestry is simply unanswerable here, so this SKIPS rather
    than passing silently or reddening a true pin. A truncated local checkout that holds
    the objects but not the history is the same case: `HistoryIncomplete` is this seat's
    ignorance, not a finding, so it skips too -- unless some pin is provably off the branch,
    which stands on any seat that can resolve it.
    """
    assert len(committed_recordings(RECORDINGS_DIR)) == len(ARMS) * len(load_cases()) > 0

    try:
        default_sha = observe_the_default_branch(REPO_ROOT)
    except HistoryIncomplete as why:
        pytest.skip(
            f"cannot observe the default branch from here, so ancestry is unanswerable: {why}"
        )

    refused, incomplete = pins_off_the_default_branch(
        RECORDINGS_DIR, root=REPO_ROOT, default_branch_sha=default_sha
    )
    if not refused and incomplete:
        pytest.skip(
            "this checkout cannot see enough history to place these pins on the default "
            "branch, and none is provably off it -- `git fetch --unshallow origin` and "
            "re-run:\n" + "\n".join(f"  {p.relative_to(REPO_ROOT)}: {why}" for p, why in incomplete)
        )
    lead = "committed recordings pin commits the default branch does not contain:"
    assert not refused, f"{lead}\n" + "\n".join(
        f"  {path.relative_to(REPO_ROOT)}: {reason}" for path, reason in refused
    )


def test_the_walk_reports_the_recording_whose_pin_went_dangling(tmp_path):
    """The negative control: the walk must fire, and must name the right file.

    A copy of the real recordings with ONE commit replaced by an object no repository
    has. The mutated file is the LAST one enumerated on purpose -- a walk that stops at
    the first arm, or that drops entries the way a filtering comprehension does, passes a
    control planted at the front.
    """
    for arm in ARMS:
        for case in load_cases():
            for source in recordings_of(arm, case.id):
                copy = recording_path(arm, case.id, source.stem, directory=tmp_path)
                copy.parent.mkdir(parents=True, exist_ok=True)
                copy.write_text(source.read_text())

    planted = recordings_of(ARMS[-1], load_cases()[-1].id, directory=tmp_path)[-1]
    payload = json.loads(planted.read_text())
    payload["git_commit"] = "a" * 40
    planted.write_text(json.dumps(payload))

    refused = unresolvable_pins(tmp_path, root=REPO_ROOT)

    assert [path for path, _ in refused] == [planted]
    assert "a" * 40 in refused[0][1]


def test_the_gate_checks_out_enough_history_for_that_check_to_answer():
    """A depth-1 checkout cannot resolve any commit older than itself.

    `actions/checkout` clones one commit by default, so the check above would refuse
    every recording on the gate -- for the shallow clone, not for the pin -- and the
    obvious repair is to make it skip, which is how a guard ends up green in the one
    place it is supposed to bite. The repair belongs on the checkout instead.

    Read from the workflow so it holds off CI too, and confirmed against the real
    checkout when there is one, because what the workflow file can support is an
    assertion about a key and the fact that matters is about history.
    """
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["test"]["steps"]
    checkout = next(
        step for step in steps if str(step.get("uses", "")).startswith("actions/checkout")
    )
    assert checkout.get("with", {}).get("fetch-depth") == 0, (
        "the job that runs tests/unit checks out shallow, so "
        "test_every_committed_recording_names_a_commit_this_checkout_can_resolve cannot "
        "resolve any recording's commit there"
    )

    if not os.environ.get("CI"):
        pytest.skip("off the gate the local checkout's depth is the developer's business")
    shallow = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert shallow.stdout.strip() == "false", (
        "the gate's checkout is shallow despite fetch-depth: 0, so the recording check is "
        "measuring this clone rather than the pins"
    )

    # `fetch-depth: 0` also settles the refspec: checkout fetches the broad
    # `+refs/heads/*:refs/remotes/origin/*`, so `fetch_scope_evidence` is empty here. A
    # NARROW refspec would let the stronger standing check excuse an ABSENT governed
    # object as this seat's ignorance (HistoryIncomplete) and SKIP -- green in exactly the
    # place the b9e06eb regression must redden. Asserted loud here so a future checkout
    # change reddens this guard rather than silently disarming the standing check.
    assert fetch_scope_evidence(REPO_ROOT) == (), (
        "the gate fetches a narrow refspec, so the recording check would excuse an absent "
        "pin as truncation and skip instead of failing -- broaden the checkout to "
        "`+refs/heads/*:refs/remotes/origin/*` (fetch-depth: 0 does this)"
    )


def _repo_with_two_commits(root: Path) -> Path:
    root.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "p@example.test"],
        ["config", "user.name", "P"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    for index in range(2):
        (root / "tracked.txt").write_text(f"{index}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", f"c{index}"], cwd=root, check=True, capture_output=True
        )
    return root


def test_the_probe_fires_on_a_shallow_checkout_and_not_on_a_whole_one(tmp_path):
    """The discrimination, measured on real clones rather than asserted about a message.

    The control is the point: a probe that answered "shallow" everywhere would satisfy
    the bounce and destroy the check, since every genuine dangling pin would then be
    reported as this seat's own ignorance and repaired with a fetch that changes nothing.
    """
    source = _repo_with_two_commits(tmp_path / "source")
    whole = tmp_path / "whole"
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", source.as_uri(), str(whole)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", source.as_uri(), str(shallow)],
        check=True,
        capture_output=True,
    )

    assert incomplete_checkout(shallow) == "this checkout is SHALLOW"
    assert incomplete_checkout(whole) == "", "a whole checkout gets no excuse it has not earned"


def test_truncation_can_only_refuse_a_pin_and_never_accept_a_dangling_one(tmp_path):
    """Why the standing check needs no precondition about THIS seat, measured (hy-u50c).

    The version of this file that shipped to review asserted the seat was whole before
    trusting a green, and that was wrong twice over. It reddened the mayor's and the
    refinery's completion gate -- both shallow, both resolving every pin -- with a bare
    `AssertionError` carrying no message, on trees where nothing was wrong. And the
    premise was false: the probe never suppresses a failure, it only relabels one, so a
    green was never the probe's silence.

    The property that actually holds is the one below, and it is about git rather than
    about a seat. A shallow graft TRUNCATES the walk, so `for-each-ref --contains` can
    miss a ref that reaches a commit and can never invent one, and `cat-file -e` can miss
    an object and never conjure one. Truncation moves an answer in exactly one direction
    -- toward refusal -- so an acceptance on a truncated seat has established as much as
    an acceptance on a whole one. Which is why the accept side needs no guard at all, and
    why the refusal side needs `incomplete_checkout` to say whose ignorance it is.

    Asserted on real clones of a real repository, so it is the behaviour saying this and
    not a paraphrase of it.
    """
    source = _repo_with_two_commits(tmp_path / "source")
    whole = tmp_path / "whole"
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", source.as_uri(), str(whole)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", source.as_uri(), str(shallow)],
        check=True,
        capture_output=True,
    )

    def rev(ref: str) -> str:
        return subprocess.run(
            ["git", "rev-parse", ref], cwd=whole, capture_output=True, text=True, check=True
        ).stdout.strip()

    tip, older = rev("HEAD"), rev("HEAD~1")

    # The whole clone answers both, which is what makes the shallow half discriminating
    # rather than a repository with one commit in it.
    refuse_an_unresolvable_commit(tip, root=whole)
    refuse_an_unresolvable_commit(older, root=whole)

    # The shallow clone accepts the pin it holds. Same answer, less history.
    assert incomplete_checkout(shallow) == "this checkout is SHALLOW"
    refuse_an_unresolvable_commit(tip, root=shallow)

    # And refuses the one its graft cut off -- a refusal about the clone, which is the
    # only direction truncation can move an answer.
    with pytest.raises(CommitUnresolvable):
        refuse_an_unresolvable_commit(older, root=shallow)


def _recording_pinning(commit: str, path: Path) -> Path:
    """A real committed recording, copied, with only its `git_commit` replaced."""
    source = recordings_of(ARMS[0], load_cases()[0].id)[0]
    payload = json.loads(source.read_text())
    payload["git_commit"] = commit
    path.write_text(json.dumps(payload))
    return path


def test_a_shallow_seat_is_not_told_to_disbelieve_a_pin_it_holds(tmp_path):
    """The three leads, on real repositories rather than on a message template (hy-7sdq).

    The middle case is the defect: a truncated checkout was blaming itself for every
    refusal it produced, so three seats in this town shipping a genuinely dangling
    governed pin were told to run `--unshallow` and disbelieve a true finding -- a remedy
    that repairs nothing, since a whole clone does not hold that object either.

    Discriminated on the OBJECT, `cat-file -e`, not on the seat. A seat that holds the
    object has measured a dangling pin; a seat that does not may simply be missing it.
    """
    source = _repo_with_two_commits(tmp_path / "source")
    whole = tmp_path / "whole"
    shallow = tmp_path / "shallow"
    for target, args in ((whole, []), (shallow, ["--depth=1"])):
        subprocess.run(
            ["git", "clone", "-q", *args, source.as_uri(), str(target)],
            check=True,
            capture_output=True,
        )

    # A dangling object inside the shallow clone: committed, then the branch moved back
    # off it. Present, reachable from no ref -- which is exactly the estate's shape.
    tip = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=shallow, capture_output=True, text=True, check=True
    ).stdout.strip()
    (shallow / "tracked.txt").write_text("dangling\n", encoding="utf-8")
    for args in (
        ["config", "user.email", "p@example.test"],
        ["config", "user.name", "P"],
        ["add", "-A"],
        ["commit", "-qm", "orphan"],
    ):
        subprocess.run(["git", *args], cwd=shallow, check=True, capture_output=True)
    orphan = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=shallow, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(["git", "reset", "-q", "--hard", tip], cwd=shallow, check=True)

    dangling = ((_recording_pinning(orphan, tmp_path / "d.json"), "reachable from no ref"),)
    absent = ((_recording_pinning("a" * 40, tmp_path / "a.json"), "not a commit"),)

    # Held here: the finding stands, and the remedy TESTS it rather than excusing it.
    held = failure_lead(dangling, root=shallow)
    assert "HOLDS every object below" in held
    assert "genuinely dangling" in held
    assert "before believing any of this" not in held

    # Not held here: the seat may simply be missing it, and says so.
    assert "does not hold" in failure_lead(absent, root=shallow)
    assert "before believing any of this" in failure_lead(absent, root=shallow)
    # One absent pin among many is enough to lose the right to blame the pins.
    assert "does not hold" in failure_lead(dangling + absent, root=shallow)

    # A whole checkout gets no excuse at all, in either case.
    whole_lead = failure_lead(dangling, root=whole)
    assert whole_lead == "committed recordings pin commits nobody can resolve:"
    assert failure_lead((), root=shallow) == ""
