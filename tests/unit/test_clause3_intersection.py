"""hy-fy7d clause 3, driven through real repositories rather than described.

Git is real in every arm here for the same reason it is real in
`test_version_collision_gate.py`: the failures this script exists to stop are
the ones where git answers and the answer is not what it looks like. The arm
that matters most is a pair -- one CUT checkout and one that merely has nothing
in common -- because `git merge-base` returns rc=1 with empty stdout for both,
and every earlier version of clause 3's method was wrong precisely there.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLAUSE3 = str(ROOT / "scripts" / "clause3-intersection.sh")

# Well-formed and held by nobody. `git rev-parse --verify` resolves it to itself
# at rc=0; the same string PEELED with `^{commit}` is rc=1, which is why the
# script never asks about a rev without peeling it.
UNHELD = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

# The one repo-specific value in the script, read out of the script rather than
# restated: a second copy of a sha is a second thing to forget.
TRUE_ROOT_DECLARATION = re.compile(r"^TRUE_ROOT_DEFAULT=([0-9a-f]{40})$", re.M)

# The hy-1ryd data-read pattern, likewise read from the script so the drift guard
# below tests the real regex and not a paraphrase of it.
DATA_RE_DECLARATION = re.compile(r"^data_re='([^']*)'$", re.M)


def _declared_true_root():
    declared = TRUE_ROOT_DECLARATION.search(Path(CLAUSE3).read_text())
    assert declared, "clause3-intersection.sh no longer declares a 40-hex TRUE_ROOT_DEFAULT"
    return declared.group(1)


def _declared_data_re():
    declared = DATA_RE_DECLARATION.search(Path(CLAUSE3).read_text())
    assert declared, "clause3-intersection.sh no longer declares a data_re for the hy-1ryd arm"
    return re.compile(declared.group(1))


def _cut_boundaries(repo):
    """The shallow boundaries a checkout holds, from the file the SCRIPT reads.

    `--git-common-dir` and not `--git-dir`, which is the whole reason this is a
    helper. Most checkouts of this repository are WORKTREES of a shared store:
    measured 2026-08-01, of 16 hyperset checkouts under ~/gt, 13 resolve a
    `shallow` file of 41 bytes and only the three crew clones have none. Read
    per-worktree with `--git-dir` they all look boundary-free, which is the
    wrong answer and not the one line 131 of the script gets.

    THE FILE IS NOT EVIDENCE OF A CUT. Those 41 bytes are exactly the true root,
    and all 13 resolve `rev-list --max-parents=0` correctly, so a non-empty
    shallow file does not mean history is missing -- the script ANDs it with
    `awk '$0 != TRUE_ROOT'` for precisely that reason.

    THE ARM BELOW MUST NOT BORROW THAT SECOND CONJUNCT, and this is the durable
    part. It compares the boundary against the constant the arm exists to
    verify. A wrong TRUE_ROOT_DEFAULT would make the conjunct fire, the arm
    skip, and this file report nothing -- the guard disarming itself in exactly
    the state it was built to catch. So the predicate here never mentions
    TRUE_ROOT, and the price is deliberate: it skips on 13 checkouts that could
    have run it. Over-skipping is a smaller guard; a self-disarming skip is not
    a guard at all.
    """
    common = _git_common_dir(repo)
    assert common is not None, f"{repo} holds no git checkout, so it has no boundaries to read"
    shallow = common / "shallow"
    if not shallow.exists():
        return []
    return [line for line in shallow.read_text().splitlines() if line.strip()]


NOT_A_REPOSITORY = 128
"""git's rc for "this is not a repository".

The one nonzero code this file reads as a FACT about the seat. Every other one
is git failing for a reason this file has not classified, and the two are not
the same thing: keyed on `!= 0`, a full non-shallow checkout whose git exits 129
was told it held no checkout at all (atlas, on 39182216, with a shim).
"""


def _git_common_dir_answer(repo):
    """git's own `(returncode, stdout, stderr)` for `--git-common-dir`.

    A MISSING GIT BINARY arrives as `returncode is None` rather than as an
    exception, and that is the point of this function existing. No git raises
    `FileNotFoundError`, which is not a return code, so a reader keyed on the
    return code alone never runs and the import that reads it aborts collection
    -- this file's own bug, one state over. It is not hypothetical here: the
    rc 128 sentence below names "a container that ships the tests and not the
    history", and that container has no git in it either (atlas, on 39182216,
    with PATH emptied).
    """
    try:
        found = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
    except OSError as unavailable:
        return None, "", str(unavailable)
    return found.returncode, found.stdout, found.stderr


def _git_common_dir(repo):
    """The common git directory of `repo`, or `None` where git named no path.

    `--git-common-dir` and not `--git-dir`, which is the whole reason this is a
    helper: see `_cut_boundaries`.

    WHAT `None` DOES NOT MEAN. It does not mean "no checkout". It means git did
    not answer with a path, and rc 128 is the only code that says why. Which
    state a seat is in is `_how_the_root_reads_here`'s question rather than this
    reader's, because the reader has two callers and only one of them skips.
    """
    code, out, _ = _git_common_dir_answer(repo)
    return Path(out.strip()) if code == 0 else None


def _how_the_root_reads_here(repo):
    """`(skip reason, failure reason)` for the arm below; at most one is set.

    FOUR STATES, THREE DISPOSITIONS, and the fourth line is the ruling (hy-sram,
    after atlas bounced the first fix):

        rc 0                  the arm RUNS, unless the checkout declares a cut
        rc 128                SKIP: no repository here, so nothing to pin
        no git binary         SKIP, with its OWN sentence: also true, and true
                              for a different reason than rc 128
        any other rc          FAIL: git broke in a way this file cannot
                              classify, on a seat that may well be pinnable

    The first fix returned a skip for every nonzero rc, which turned a loud
    collection abort into a quiet disarm wearing a false sentence. Skip is the
    right disposition only where the arm is INAPPLICABLE. Where we cannot tell,
    going quiet is the unsafe direction, and a guard that writes its own excuse
    for not running is hy-73ed one level up.

    A reason and not a boolean, and a reason PER STATE rather than one shared
    string: they are different facts about the seat, and a skip that gives one
    sentence for two conditions describes the wrong seat half the time. That
    also absorbs the worktree whose common dir was deleted -- it answers 128, so
    the sentence states what git said rather than what we inferred from it.

    Both values are strings computed at import and neither branch raises, so
    collection survives all four states. That was the original bug and it stays
    fixed; it is a separate requirement from the arm not going quiet, and the
    first fix served only one of them.

    Neither branch mentions TRUE_ROOT, for the reason `_cut_boundaries` gives.
    """
    code, _, stderr = _git_common_dir_answer(repo)
    said = stderr.strip() or "<nothing on stderr>"
    if code is None:
        return (
            "there is no `git` on PATH here, so `rev-list --max-parents=0` cannot be asked at "
            "all: a container that ships the tests and not the history has no git in it either. "
            "SKIPPED IS NOT PASSED: the value is unpinned here. This is a DIFFERENT state from "
            f"rc 128 and carries its own sentence for that reason; python reported {said}",
            None,
        )
    if code == NOT_A_REPOSITORY:
        return (
            "git answers rc 128, `not a repository`, so `rev-list --max-parents=0` has nothing "
            "to read: an rsync copy taken without `.git`, an sdist, a container that ships the "
            "tests and not the history, or a worktree whose common dir is gone. SKIPPED IS NOT "
            "PASSED: the value is unpinned here, and answering rather than raising is hy-sram",
            None,
        )
    if code != 0:
        return (
            None,
            f"`git rev-parse --git-common-dir` exited {code} here, which is neither a path nor "
            "rc 128, so this file cannot tell whether this seat can pin its root. git said: "
            f"{said}. NOT SKIPPED, deliberately: a skip on a failure nothing has classified is "
            "the guard writing its own excuse, and the seat may well be pinnable (hy-sram)",
        )
    if _cut_boundaries(repo):
        return (
            "this checkout declares a shallow boundary, so `rev-list --max-parents=0` may "
            "return the graft point rather than the root and would agree with itself. "
            "SKIPPED IS NOT PASSED: the value is unpinned here. It is pinned on the three "
            "crew clones -- crew/critic, crew/hyperion, crew/atlas -- and in the CI `test` "
            "job, which already checks out with fetch-depth: 0 and runs tests/unit. It is "
            "skipped on the 13 worktree seats, deliberately: see `_cut_boundaries`",
            None,
        )
    return None, None


UNPINNABLE_HERE, UNREADABLE_HERE = _how_the_root_reads_here(ROOT)


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo, path, body):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(body)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"touch {path}")
    return _git(repo, "rev-parse", "HEAD")


def _init(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "clause3@example.invalid")
    _git(path, "config", "user.name", "Clause Three Test")
    return path


def _run(repo, *args):
    return subprocess.run(["bash", CLAUSE3, *args], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def shared_history(tmp_path):
    """A base, a main that moved on top of it, and a head branched off it.

    Which files each side touched is the whole question, so the two sides are
    given separate files here and the same file in the arm below.
    """
    repo = _init(tmp_path / "repo")
    base = _commit(repo, "base.txt", "base\n")
    _commit(repo, "main_only.txt", "main moved\n")
    main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "head", base)
    head = _commit(repo, "head_only.txt", "head moved\n")
    return {"repo": repo, "base": base, "main": main, "head": head}


def test_a_head_and_main_touching_different_files_reads_clean(shared_history):
    """Clause 3's whole point is that this case must NOT bounce: main moved,
    the head is behind it, and nothing they touched overlaps."""
    result = _run(shared_history["repo"], shared_history["head"], shared_history["main"])

    assert result.returncode == 0, result.stderr
    assert "NO INTERSECTION" in result.stdout
    assert shared_history["base"][:12] in result.stdout or "merge base" in result.stdout


def test_a_file_both_sides_touched_is_named_rather_than_counted(tmp_path):
    """The ruling requires the intersection be NAMED when it goes back to
    critic. A gate that reports the count sends the reader back to git."""
    repo = _init(tmp_path / "repo")
    base = _commit(repo, "shared.txt", "base\n")
    _commit(repo, "shared.txt", "main rewrote it\n")
    main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "head", base)
    head = _commit(repo, "shared.txt", "head rewrote it\n")

    result = _run(repo, head, main)

    assert result.returncode == 4, result.stdout + result.stderr
    assert "shared.txt" in result.stdout
    assert "NO INTERSECTION" not in result.stdout


def test_data_coupling_through_a_recording_directory_demands_the_merged_tree_suite(tmp_path):
    """hy-1ryd. A path intersection is blind to coupling through data READ at
    runtime. Main rewrites a recording; the head edits the test that globs the
    recordings directory. They share no path, so the intersection is empty AND a
    conflict-only trial merge is clean -- the exact #197xX194 case. Exit 5 names
    the merged-tree suite as the remaining check, the one instrument that sees it.
    """
    repo = _init(tmp_path / "repo")
    base = _commit(repo, "recordings/one.json", '{"git_commit": "aaa"}\n')
    _commit(repo, "recordings/one.json", '{"git_commit": "bbb"}\n')  # main rewrote the data
    main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "head", base)
    head = _commit(repo, "test_reads_recordings.py", "reads the recordings dir\n")

    result = _run(repo, head, main)

    assert result.returncode == 5, result.stdout + result.stderr
    assert "NO INTERSECTION: clause 3 satisfied" not in result.stdout
    assert "merged-tree" in result.stdout.lower()
    assert "recordings/one.json" in result.stdout


def test_a_head_touching_a_fixture_directory_also_demands_the_suite(tmp_path):
    """The either-side half: the head adds a fixture and main diverged elsewhere,
    so the merged-tree suite reads the head's fixture against main's code and can
    differ from either side's own CI. Same blindness, other axis -- exit 5."""
    repo = _init(tmp_path / "repo")
    base = _commit(repo, "app.py", "base\n")
    _commit(repo, "app.py", "main changed code\n")  # main diverged on a non-data path
    main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "head", base)
    head = _commit(repo, "tests/fixtures/sample.json", "{}\n")

    result = _run(repo, head, main)

    assert result.returncode == 5, result.stdout + result.stderr
    assert "tests/fixtures/sample.json" in result.stdout


def test_a_data_touch_where_the_head_contains_main_still_reads_clean(tmp_path):
    """The arm is gated on main having diverged. When the head contains main the
    merged tree equals the head tree and its own CI already ran the suite against
    it -- a recording the head touched is not a second tree to check. So this
    stays exit 0 and does not manufacture a suite run that head CI already did.
    """
    repo = _init(tmp_path / "repo")
    base = _commit(repo, "recordings/one.json", "base\n")
    main = base  # main never moved past the base: the head strictly contains it
    _git(repo, "checkout", "-q", "-b", "head", base)
    head = _commit(repo, "recordings/one.json", "head rewrote it\n")

    result = _run(repo, head, main)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NO INTERSECTION: clause 3 satisfied" in result.stdout


def test_the_data_arm_covers_the_known_read_sets():
    """A positive lock on the read sets the suite actually couples through (hy-1ryd).

    NOT a completeness proof. Deriving every file a test reads from test source is
    hy-1ryd's deferred hard half -- a globbed directory names no member, a
    `.read_text()` target is built from split literals, and keying on the leaf
    alone cannot tell a file a test READS from one it merely MENTIONS in a message
    (that scan flagged CLAUDE.md and town.json). So this pins the KNOWN couplings
    instead, each a real disjoint-path pair a shipped test embodies: main rewrites
    the data, a head edits the reader, paths disjoint. An eval-only first version
    omitted the docs and workflow reads yet claimed "every read set"; these name
    them, and a `data_re` edit that dropped one reddens here.
    """
    pattern = _declared_data_re()

    def _first(glob: str) -> str:
        found = sorted(p for p in ROOT.glob(glob) if p.is_file())
        assert found, f"no file matches {glob}; a read-set example is gone, not a pass"
        return found[0].relative_to(ROOT).as_posix()

    known = [
        "hyperset/evals/cases/revenue.yaml",  # load_cases feeds ARMS x cases
        "hyperset/evals/prompts/raw_baseline.md",  # arms.RAW_PROMPT_PATH
        "hyperset/evals/expected_failures.yaml",  # the ratchet's data
        "docs/development/benchmark.md",  # test_benchmark_md_matches_the_scored_recordings
        "docs/v0-foundation.md",  # section 7/8 + version tests, vs the served contract
        ".github/workflows/ci.yml",  # test_committed_recordings_resolve reads the steps
        "pyproject.toml",  # test_gate_evidence_line vs gate.extra_distributions
        "AGENTS.md",  # test_gate_evidence_line: the pasted gate line vs evidence_line()
        "CLAUDE.md",  # test_gate_evidence_line, same
        _first("hyperset/evals/recordings/**/*.json"),  # the committed recordings, globbed
        _first("docs/adr/*.md"),  # expected_failures.py resolves and reads the cited ADR
        _first("tests/fixtures/**/*.json"),  # every test fixture tree
    ]

    for path in known:
        assert (ROOT / path).exists(), f"read-set example {path} no longer exists"
    missed = [path for path in known if not pattern.search(path)]
    assert not missed, (
        "data_re no longer covers these known read sets, so main rewriting one while a "
        f"head edits its reader would exit 0 as clean: {missed}"
    )


@pytest.fixture
def main_renamed_it(tmp_path):
    """Main moved a.py to b.py; the head, branched off the base, edits a.py.

    `git mv` and not a delete plus a write, because the subject is what git's
    rename DETECTION does with the pair. A rename git declines to detect would
    leave the arm below green against the defect it exists to catch.
    """
    repo = _init(tmp_path / "repo")
    base = _commit(repo, "a.py", "def carried():\n    return 1\n")
    _git(repo, "mv", "a.py", "b.py")
    _git(repo, "commit", "-q", "-m", "main renames a.py to b.py")
    main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "head", base)
    return {"repo": repo, "base": base, "main": main}


def test_a_file_main_renamed_out_from_under_the_head_is_an_intersection(main_renamed_it):
    """The arm that fails without `--no-renames` (hy-8v1k).

    Detection reports a rename as ONE moved file, so main's changed set holds
    b.py and never a.py. The head edited a.py. The two sets intersect to
    nothing and this script exits 0 saying clause 3 is satisfied -- while the
    head is carrying edits to a file main has moved away underneath it, which
    is the exact shape clause 3 was written to bounce.

    Measured against the pre-fix script on this fixture: `head changed 1
    file(s); main changed 1 file(s) since` / `NO INTERSECTION: clause 3
    satisfied` / EXIT=0. With the flag, main changes 2 and it exits 4 naming
    a.py.
    """
    head = _commit(main_renamed_it["repo"], "a.py", "def carried():\n    return 2\n")

    result = _run(main_renamed_it["repo"], head, main_renamed_it["main"])

    assert result.returncode == 4, result.stdout + result.stderr
    assert "a.py" in result.stdout
    assert "NO INTERSECTION" not in result.stdout


def test_a_rename_the_head_never_touched_still_reads_clean(main_renamed_it):
    """`--no-renames` widens main's set, so the control is that it does not
    widen it into a bounce. Same rename on main, head somewhere else entirely:
    b.py and the added-then-deleted a.py are both in main's set and neither is
    in the head's. Without this arm the one above passes against a script that
    intersects everything."""
    head = _commit(main_renamed_it["repo"], "unrelated.py", "def elsewhere():\n    return 3\n")

    result = _run(main_renamed_it["repo"], head, main_renamed_it["main"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NO INTERSECTION" in result.stdout


@pytest.fixture
def cut_checkout(tmp_path):
    """refinery's depth-1 probe, rebuilt: both tips present, ancestor absent.

    `git clone --depth 1` of a repository whose main has moved leaves a
    checkout where `cat-file -e` passes on both tips and the commit they share
    is not there at all. That is the state no exit code distinguishes from an
    orphan, and 128 does not cover it -- 128 is for an absent TIP.
    """
    origin = _init(tmp_path / "origin")
    base = _commit(origin, "base.txt", "base\n")
    _commit(origin, "main_only.txt", "main moved\n")
    main = _git(origin, "rev-parse", "HEAD")
    _git(origin, "checkout", "-q", "-b", "head", base)
    head = _commit(origin, "head_only.txt", "head moved\n")
    _git(origin, "checkout", "-q", "main")

    clone = tmp_path / "clone"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            "--no-single-branch",
            f"file://{origin}",
            str(clone),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return {"clone": clone, "main": main, "head": head, "base": base}


def test_a_cut_checkout_refuses_where_the_same_output_would_mean_orphan(cut_checkout):
    """The amendment. Both tips resolve, the merge base comes back rc=1 and
    empty, and the honest answer is that this seat cannot tell."""
    for tip in (cut_checkout["head"], cut_checkout["main"]):
        assert (
            subprocess.run(
                ["git", "cat-file", "-e", f"{tip}^{{commit}}"], cwd=cut_checkout["clone"]
            ).returncode
            == 0
        ), "the probe is only the probe while both tips are present"
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", f"{cut_checkout['base']}^{{commit}}"],
            cwd=cut_checkout["clone"],
        ).returncode
        != 0
    ), "the probe is only the probe while the shared ancestor is absent"

    result = _run(cut_checkout["clone"], cut_checkout["head"], cut_checkout["main"])

    assert result.returncode == 2, result.stdout + result.stderr
    assert "REFUSE" in result.stderr
    assert "NO COMMON ANCESTOR" not in result.stdout


def test_the_same_empty_merge_base_answers_no_common_ancestor_where_nothing_is_cut(
    tmp_path,
):
    """The other side of the pair, and the reason the guard is not keyed to
    `--is-shallow-repository` (hy-f1vw).

    This checkout IS shallow, and its one boundary is the root the repository
    actually has, so nothing is missing below it -- the rig's own shape, where
    a flag-keyed refusal would fire on every run forever. Same repository, same
    rc=1, same empty stdout as the arm above; the declared true root is the
    only thing that differs, and it moves the verdict from REFUSE to an answer.
    """
    origin = _init(tmp_path / "origin")
    root = _commit(origin, "base.txt", "base\n")
    _git(origin, "checkout", "-q", "--orphan", "lonely")
    _git(origin, "rm", "-rqf", ".")
    orphan = _commit(origin, "elsewhere.txt", "unrelated\n")
    _git(origin, "checkout", "-q", "main")
    main = _commit(origin, "main_only.txt", "main moved\n")

    clone = tmp_path / "clone"
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            "main",
            f"file://{origin}",
            str(clone),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(clone, "fetch", "-q", "origin", "lonely")
    boundaries = (clone / ".git" / "shallow").read_text().split()
    assert boundaries == [main], "the arm needs exactly one boundary to declare"

    refused = _run(clone, orphan, main)
    answered = _run(clone, orphan, main, "--true-root", main)

    assert refused.returncode == 2, refused.stdout + refused.stderr
    assert answered.returncode == 3, answered.stdout + answered.stderr
    assert "NO COMMON ANCESTOR" in answered.stdout
    assert root not in answered.stdout


def test_a_store_missing_an_ancestor_object_is_not_an_orphan(tmp_path):
    """The third population of rc=1, found by measurement here and not covered
    by hy-fy7d's published method.

    Nothing is shallow and both tips peel; one loose commit object is simply
    gone, which is what a raced `git gc` leaves behind on an object reachable
    from no ref -- the state refinery and mayor read differently twelve minutes
    apart in crew/hyperion. `git merge-base` exits non-zero with empty stdout --
    1 here, which is the orphan shape exactly, and 255 on CI's Linux git for the
    same deleted object -- and writes `error: Could not read <sha>` to the stream
    the published snippet sends to /dev/null. A genuine orphan writes zero bytes
    there. That is the whole discrimination, and the shallow file cannot make it.
    """
    repo = _init(tmp_path / "repo")
    base = _commit(repo, "base.txt", "base\n")
    _commit(repo, "main_only.txt", "main moved\n")
    main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "head", base)
    head = _commit(repo, "head_only.txt", "head moved\n")
    (repo / ".git" / "objects" / base[:2] / base[2:]).unlink()

    assert not (repo / ".git" / "shallow").exists(), "nothing here is cut"
    probe = subprocess.run(
        ["git", "merge-base", head, main], cwd=repo, capture_output=True, text=True
    )
    # The rc for this state is not the same on every platform: git 2.39.5 (Apple
    # Git-154) returns 1, and CI's Linux git returns 255 for the identical
    # deleted object (hy-9m12). Both are non-zero with nothing on stdout, so the
    # precondition asserts that much and no more -- pinning either platform's
    # number here kills the arm on the other one before it reaches the assertion
    # it exists for, which is what happened at 04f693d5. What the arm needs is
    # only that merge-base failed while saying nothing, and that it named the
    # object it could not read; the rc itself is under measurement, not assumed.
    # The two rcs land in different arms of the script's `case` and the file's
    # headline reasoning is written from the rc=1 one; that is hy-h08a's matrix
    # to measure, not this arm's to assert.
    assert probe.returncode != 0, probe.stdout + probe.stderr
    assert probe.stdout == "", probe.stdout
    assert base in probe.stderr, probe.stderr

    result = _run(repo, head, main)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "NO COMMON ANCESTOR" not in result.stdout
    assert base in result.stderr
    # git's own first line reaches the caller, on whichever arm the rc landed in.
    assert probe.stderr.splitlines()[0] in result.stderr


# --- hy-t0ot: the object-read discrimination, pinned on ONE git version -----------
#
# The state above (an ancestor object deleted, both tips peeling) makes `git
# merge-base` fail while writing to stderr, and the rc it fails with is the git
# version, NOT the platform: 1 on 2.39.5, 255 on 2.47.3, byte-identical stderr.
# The two land in different arms of the script's `case` -- rc=1 in the
# stderr-discrimination branch, rc>1 in the `*)` catchall -- so a single-version
# run reaches only one. atlas's sweep on #200: on 2.47.3 you can delete the entire
# rc=1 discrimination and the suite stays green, because that version's population
# never reaches it. Every gate runs one git version, so all of them report "the
# arm passes" while reading like "the arm is tested". The shim below forces either
# shape on whatever version runs, so both arms are exercised every run and deleting
# the discrimination reddens here regardless of ambient git.


def _git_shim(tmp_path: Path, forced_rc: int) -> Path:
    """A `git` wrapper that forces a merge-base OBJECT-READ error to `forced_rc`.

    Everything but `merge-base` is passed straight through, and a merge-base that
    is NOT the object-read error (a clean answer, or a genuine orphan writing zero
    bytes to stderr) keeps its real rc -- so the shim manufactures only the one
    population whose rc is version-dependent, and leaves the orphan shape the arms
    also depend on untouched. Keyed on the stderr git itself writes (`could not
    read`), never on uname: the axis is the git version (hy-h08a).
    """
    real = shutil.which("git")
    assert real, "no git on PATH to wrap"
    bin_dir = tmp_path / f"shim-{forced_rc}"
    bin_dir.mkdir()
    shim = bin_dir / "git"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'REAL="{real}"\n'
        'if [ "$1" = "merge-base" ]; then\n'
        '  err="$(mktemp)"\n'
        '  out="$("$REAL" "$@" 2>"$err")"; rc=$?\n'
        '  cat "$err" >&2\n'
        '  printf "%s" "$out"\n'
        '  if [ $rc -ne 0 ] && [ -z "$out" ] && grep -qi "could not read" "$err"; then\n'
        f'    rm -f "$err"; exit {forced_rc}\n'
        "  fi\n"
        '  rm -f "$err"; exit $rc\n'
        "fi\n"
        'exec "$REAL" "$@"\n'
    )
    shim.chmod(0o755)
    return bin_dir


def _run_shimmed(repo, shim_bin, *args, script=CLAUSE3):
    env = dict(os.environ, PATH=f"{shim_bin}{os.pathsep}{os.environ['PATH']}")
    return subprocess.run(
        ["bash", str(script), *args], cwd=repo, capture_output=True, text=True, env=env
    )


def _script_without_stderr_discrimination(tmp_path: Path) -> Path:
    """A copy of the script with ONLY the rc=1 object-read discrimination removed.

    Matched on the exact `if [ -s "$STDERR" ]; then ... fi` block so a refactor
    that renames or reflows it reddens the mutation test rather than silently
    defeating it -- the branch's absence has to be this test's doing, not a
    rewrite's.
    """
    lines = Path(CLAUSE3).read_text().splitlines(keepends=True)
    starts = [i for i, ln in enumerate(lines) if ln.rstrip("\n") == '    if [ -s "$STDERR" ]; then']
    assert len(starts) == 1, f"expected one stderr-discrimination guard, found {len(starts)}"
    start = starts[0]
    end = next(i for i in range(start + 1, len(lines)) if lines[i].rstrip("\n") == "    fi")
    del lines[start : end + 1]
    mutant = tmp_path / "clause3-no-discrimination.sh"
    mutant.write_text("".join(lines))
    return mutant


@pytest.fixture
def missing_ancestor(tmp_path):
    """Both tips peel, one shared ancestor object is gone -- the object-read-error
    population, factored so the shim arms can force its rc to either git version's
    shape. Same construction as the ambient-behaviour test above."""
    repo = _init(tmp_path / "repo")
    base = _commit(repo, "base.txt", "base\n")
    _commit(repo, "main_only.txt", "main moved\n")
    main = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "head", base)
    head = _commit(repo, "head_only.txt", "head moved\n")
    (repo / ".git" / "objects" / base[:2] / base[2:]).unlink()
    assert not (repo / ".git" / "shallow").exists(), "nothing here is cut"
    return {"repo": repo, "base": base, "head": head, "main": main}


# The arm-specific line each rc produces, so the test proves WHICH branch ran and
# not merely that some branch refused. Both arms exit 2 and name the object, so an
# exit-code-and-object assertion alone would pass even if the shim failed to force
# the rc and the ambient version answered on the other arm.
_RC_ARM = {
    1: "exited 1 and wrote to stderr",  # the stderr-discrimination branch
    255: "exited 255",  # the `*)` catchall: "neither an answer nor a no"
}


@pytest.mark.parametrize("forced_rc", [1, 255])
def test_both_object_read_rc_shapes_refuse_on_any_git_version(
    missing_ancestor, tmp_path, forced_rc
):
    """Both arms of the discrimination, exercised on whatever git version runs.

    rc=1 forces 2.39.5's shape (the stderr-discrimination branch), rc=255 forces
    2.47.3's (the catchall). On a real run only one is reached; the shim reaches
    both. Either way an object-read error must REFUSE and name the object it could
    not read -- never read as NO COMMON ANCESTOR, the orphan verdict -- and the
    arm-specific line proves the forced rc actually landed in the intended branch.
    """
    shim = _git_shim(tmp_path, forced_rc)
    result = _run_shimmed(
        missing_ancestor["repo"], shim, missing_ancestor["head"], missing_ancestor["main"]
    )

    assert result.returncode == 2, result.stdout + result.stderr
    assert "NO COMMON ANCESTOR" not in result.stdout
    assert missing_ancestor["base"] in result.stderr
    assert _RC_ARM[forced_rc] in result.stderr, result.stderr


def test_deleting_the_stderr_discrimination_reddens_on_any_git_version(missing_ancestor, tmp_path):
    """The coverage claim, proven by mutation rather than assumed.

    Force the rc=1+stderr shape so the discrimination branch is reached regardless
    of ambient git, then delete that branch from a copy of the script: the same
    input that REFUSED and named the object now collapses to NO COMMON ANCESTOR --
    an object-read error misread as an orphan, the exact bug the branch prevents.
    Without the shim this mutation stays green on a 2.47.3 seat, which is why
    nothing pinned it (atlas, #200).
    """
    shim = _git_shim(tmp_path, 1)
    intact = _run_shimmed(
        missing_ancestor["repo"], shim, missing_ancestor["head"], missing_ancestor["main"]
    )
    assert intact.returncode == 2 and missing_ancestor["base"] in intact.stderr, (
        intact.stdout + intact.stderr
    )

    mutant = _script_without_stderr_discrimination(tmp_path)
    mutated = _run_shimmed(
        missing_ancestor["repo"],
        shim,
        missing_ancestor["head"],
        missing_ancestor["main"],
        script=mutant,
    )

    assert mutated.returncode == 3, mutated.stdout + mutated.stderr
    assert "NO COMMON ANCESTOR" in mutated.stdout


def test_a_sha_this_checkout_does_not_hold_refuses_with_the_scope_that_explains_it(
    shared_history,
):
    """An absence is a fact about this checkout, never about the repository
    (hy-4dci). The refusal carries the refspec so the reader can tell a commit
    that does not exist from one this seat was never configured to fetch --
    the live case on this rig, where mayor/rig and all three crew seats hold
    `+refs/heads/main:refs/remotes/origin/main` and no open PR head.
    """
    result = _run(shared_history["repo"], UNHELD, shared_history["main"])

    assert result.returncode == 2
    assert "does not hold" in result.stderr
    assert "remote.origin.fetch=" in result.stderr
    assert "refs/remotes=" in result.stderr
    assert "not `git fetch --unshallow`" in result.stderr


def test_refusal_finding_and_clean_do_not_share_an_exit_code(shared_history, cut_checkout):
    """ "I could not look" and "I looked and found something" are the two the
    ruling forbids collapsing, and the collapse is invisible in any output a
    caller reads with `if ! script; then`."""
    clean = _run(shared_history["repo"], shared_history["head"], shared_history["main"])
    refused = _run(cut_checkout["clone"], cut_checkout["head"], cut_checkout["main"])
    usage = _run(shared_history["repo"])

    assert len({clean.returncode, refused.returncode, usage.returncode}) == 3
    assert clean.returncode == 0
    assert refused.returncode == 2
    assert usage.returncode == 64


def test_the_cut_reader_tells_a_cut_checkout_from_a_whole_one(cut_checkout, shared_history):
    """The skip predicate, pinned, so a skipped pin is not read as a passing one.

    The arm below cannot run where history is cut, which means the predicate
    deciding that is load-bearing: one that answered "cut" everywhere would turn
    the pin into a green that never ran -- exactly the shape hy-73ed is about.
    This arm runs on every seat, cut or not, because both repositories are built
    here.
    """
    assert _cut_boundaries(cut_checkout["clone"]) != [], (
        "a depth-1 clone must read as cut, or the pin below skips for a reason "
        "that is not true of the checkout"
    )
    assert _cut_boundaries(shared_history["repo"]) == []


def test_the_reader_answers_where_there_is_no_checkout_rather_than_raising(
    tmp_path, cut_checkout, shared_history
):
    """Three seats, three answers, and none of them an exception (hy-sram).

    "Cut", "whole" and "no checkout at all" are three different facts about a
    tree, and the third one used to arrive as a `CalledProcessError` out of a
    decorator argument. The two reasons are asserted to DIFFER because a skip
    that gave one sentence for two conditions would describe the wrong seat
    half the time, which is the same silence in a smaller font.
    """
    assert _git_common_dir(tmp_path) is None
    assert _git_common_dir(shared_history["repo"]) is not None

    absent, absent_failure = _how_the_root_reads_here(tmp_path)
    cut, cut_failure = _how_the_root_reads_here(cut_checkout["clone"])

    assert absent is not None and "rc 128" in absent
    assert cut is not None and "shallow boundary" in cut
    assert absent != cut
    assert (absent_failure, cut_failure) == (None, None), "neither seat is unclassifiable"
    assert _how_the_root_reads_here(shared_history["repo"]) == (None, None)

    assert "TRUE_ROOT" not in absent, (
        "the reason names the constant the guarded arm verifies, so a wrong constant would "
        "write its own excuse for skipping; see `_cut_boundaries`"
    )


def _git_that_exits(tmp_path, code):
    """A directory holding a `git` that answers `code` and says so on stderr."""
    shim = tmp_path / f"shim{code}"
    shim.mkdir()
    stand_in = shim / "git"
    stand_in.write_text(f'#!/bin/sh\necho "shimmed git, rc {code}" >&2\nexit {code}\n')
    stand_in.chmod(0o755)
    return shim


def test_a_git_failure_this_file_cannot_classify_fails_the_arm_rather_than_skipping_it(
    tmp_path, shared_history, monkeypatch
):
    """The ruling on hy-sram, run on a seat that IS pinnable (atlas, mayor).

    rc 129 inside a real non-shallow checkout is not "no repository here"; it is
    git failing for a reason nothing in this file has classified. The first fix
    keyed on `!= 0` and handed that seat the rc 128 sentence, so a pinnable seat
    skipped with a false description and hy-73ed's arm went quiet. A FOURTH SKIP
    REASON WOULD BE THE SAME DISARM in better prose, which is why this state
    produces a failure and not a reason.
    """
    monkeypatch.setenv("PATH", str(_git_that_exits(tmp_path, 129)))

    skip, failure = _how_the_root_reads_here(shared_history["repo"])

    assert skip is None, f"an unclassified git failure must not disarm the arm; got {skip}"
    assert failure is not None
    assert "129" in failure and "shimmed git, rc 129" in failure, failure
    assert "not a repository" not in failure, (
        "rc 129 is not rc 128, and borrowing its sentence is the defect atlas measured"
    )


def test_no_git_binary_skips_with_its_own_sentence_and_not_the_no_checkout_one(
    tmp_path, monkeypatch
):
    """The fifth state, and it is a `FileNotFoundError` rather than a code.

    No git on PATH raises out of `subprocess.run`, so a reader keyed on the
    return code alone never runs and the import aborts collection -- the very
    failure this bead exists to fix, in the environment its own rc 128 sentence
    names. True that the value is unpinned here, but true for a different reason
    than "this is not a repository", so it does not borrow that sentence.
    """
    # Read off PATH rather than through the reader under test, and a skip
    # rather than a failure: on a seat with no git at all the comparison below
    # has nothing to compare against, and an arm that cannot answer should say
    # so rather than go red for a reason it does not name.
    if shutil.which("git") is None:  # pragma: no cover
        pytest.skip("this seat has no git at all, so the rc 128 state cannot be produced here")

    # The rc 128 sentence read FIRST, while git is still reachable: the same
    # seat answers a different state once PATH is empty, which is the whole
    # distinction this arm exists to hold.
    with_git, _ = _how_the_root_reads_here(tmp_path)
    assert with_git is not None and "rc 128" in with_git

    monkeypatch.setenv("PATH", "")
    skip, failure = _how_the_root_reads_here(tmp_path)

    assert failure is None, "a missing git binary is genuinely inapplicable, not unclassifiable"
    assert skip is not None and "no `git` on PATH" in skip
    assert skip != with_git, "one seat, two states, two sentences"
    assert "not a repository" not in skip, (
        "a missing binary says nothing about whether this tree is a checkout"
    )
    assert _git_common_dir(tmp_path) is None


def test_collection_survives_where_there_is_no_git_checkout(tmp_path):
    """The regression, run rather than described (hy-sram).

    A copy of this file, three directories deep so its own `ROOT` lands on a
    tree that is not a checkout, collected by a real pytest. Before the fix this
    subprocess exited non-zero with `Interrupted: 1 error during collection`,
    and in the tree that mattered -- an rsync copy of main without `.git` --
    that interrupt cost all 1012 collected rows, not just this file's.

    Collection and not a run: what regressed was import time, and a run here
    would spend a minute building repositories to re-measure the arms above.

    BOTH ENVIRONMENTS, because the first version of this arm could not fail for
    the reason it names (atlas): it collected with git on PATH, where a missing
    binary cannot arise, while the tree it describes -- "a container that ships
    the tests and not the history" -- has no git in it. The second pass empties
    PATH, which is the state that aborted collection on 39182216.
    """
    nested = tmp_path / "tree" / "tests" / "unit"
    nested.mkdir(parents=True)
    copied = nested / "test_clause3_intersection_copy.py"
    shutil.copy(Path(__file__), copied)

    # The precondition, read off the filesystem rather than through the reader
    # under test: `_git_common_dir` is the thing this arm exists to grade, and a
    # precondition that calls it fails first and hides the assertion below.
    assert not [parent for parent in [tmp_path, *tmp_path.parents] if (parent / ".git").exists()], (
        "tmp_path sits inside a checkout, so this is not the environment this arm names"
    )

    for label, path in (("git on PATH", None), ("no git binary", "")):
        environment = dict(os.environ) if path is None else {**os.environ, "PATH": path}
        collected = subprocess.run(
            [sys.executable, "-m", "pytest", str(copied), "--collect-only", "-q"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=environment,
        )

        assert collected.returncode == 0, label + ": " + collected.stdout + collected.stderr
        assert "test_the_declared_true_root_is_the_root_this_repository_actually_has" in (
            collected.stdout
        ), label + ": " + collected.stdout


@pytest.mark.skipif(UNPINNABLE_HERE is not None, reason=UNPINNABLE_HERE or "")
def test_the_declared_true_root_is_the_root_this_repository_actually_has():
    """hy-73ed: TRUE_ROOT_DEFAULT was the one repo-specific value in the script
    and no arm could feel it -- set to forty zeroes, this file stayed green.

    It decides a REFUSE on every checkout whose history is cut, and a graft or a
    history rewrite invalidates it silently, because the script only reads it
    down an arm no seat in this rig reaches: the shallow file is non-empty on 13
    of 16 checkouts and every boundary in it IS the true root, so the second
    conjunct is false everywhere and the value is never compared to anything.

    THE FIRST ASSERTION IS THE hy-sram RULING, and it is here rather than in the
    `skipif` on purpose: a seat whose git failed in a way nothing classified is
    not a seat this arm is inapplicable to, so it turns this red with the code
    and git's own words rather than adding a fourth thing the skip can say.
    """
    assert UNREADABLE_HERE is None, UNREADABLE_HERE

    roots = _git(ROOT, "rev-list", "--max-parents=0", "HEAD").split()

    assert roots == [_declared_true_root()], (
        f"clause3-intersection.sh declares TRUE_ROOT_DEFAULT={_declared_true_root()}; "
        f"this repository's root commit is {roots}"
    )
