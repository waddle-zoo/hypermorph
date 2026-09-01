"""The two ways to read a `Completes-Bead:` trailer, and why they disagree.

`completion_commit_shas` in `scripts/gh-to-gastown-sync.sh` decides whether a
closed bead may close its GitHub issue. It reads the trailer with an anchored
`git log --grep`. Replacing that with git's own trailer parser --
`--format='%(trailers:key=Completes-Bead)'` -- is the obvious cleanup: it is
shorter, it looks more correct, and a reviewer would suggest it.

It would silently stop closing beads. Git treats only the LAST paragraph of a
message as the trailer block, and the house convention ends every message with
`Co-Authored-By: Claude`. A `Completes-Bead:` line written as its own paragraph
above that is therefore orphaned by construction -- invisible to `%(trailers)`,
plainly visible to an anchored grep. Measured on merged main at fe764bc, in
commits rather than lines because the two do not agree: the anchored reader
finds 10 commits, git's parser finds 2, so 8 merged commits declare a bead
complete in a way git cannot see. Two honest readers, and neither output says
which one you are holding.

Count commits here, never lines. Those 2 parser-visible commits carry 3
trailer lines between them, because 899de3d closes two beads at once. Reading
a line count as a commit count is how the original hy-8bvm figure came out
wrong, and the same trap is one `wc -l` away in either reader.

The failure mode of that cleanup is beads staying open on merged, complete
work, which produces no signal at all -- it is exactly the `#118` case in the
hy-8bvm completion audit, where finished work sat open because nothing closed
it.

These tests are forward-only. History cannot be rewritten, so the commits
already carrying the orphaned shape must stay unreadable by git's parser
without turning this suite red. Nothing here asserts on the repository's own
history: the fixtures below build both shapes in a scratch repository so the
divergence is stated executably rather than inherited from whatever main
happens to contain today (hy-c7cj, follow-up to hy-8bvm).

That last point is not hypothetical caution. This file was written against
main at 73583c9, where the readers stood at 9 and 1; main reached fe764bc the
same day and they stood at 10 and 3. A guard keyed to those counts would
already have been wrong. The ratio is not the invariant -- the divergence on
the orphaned shape is, and that is what the fixtures pin.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SYNC_SCRIPT = ROOT / "scripts" / "gh-to-gastown-sync.sh"

BEAD_ID = "hy-fixture1"

# The majority shape. `Completes-Bead:` is its own paragraph, and the
# `Co-Authored-By:` block below it is what git parses as the trailer block.
ORPHANED_SHAPE = f"""fix(fixture): a change whose trailer git cannot see

Completes-Bead: {BEAD_ID}

Co-Authored-By: Claude <noreply@anthropic.com>
"""

# The shape both readers agree on: one trailer block, no blank line inside it.
SAME_BLOCK_SHAPE = f"""fix(fixture): a change whose trailer git can see

Completes-Bead: {BEAD_ID}
Co-Authored-By: Claude <noreply@anthropic.com>
"""


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A scratch repository holding one commit of each trailer shape."""
    repo = tmp_path / "trailers"
    repo.mkdir()
    _git(repo, "init", "--quiet", "--initial-branch", "main")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    # Committing the two shapes in separate commits keeps each one addressable
    # by its own sha, so a reader that finds "one of two" names which one.
    for name, message in (("orphaned", ORPHANED_SHAPE), ("same_block", SAME_BLOCK_SHAPE)):
        (repo / name).write_text(f"{name}\n")
        _git(repo, "add", name)
        _git(repo, "commit", "--quiet", "--message", message)
    return repo


def _sha_of(repo: Path, subject_fragment: str) -> str:
    matches = [
        line.split(maxsplit=1)[0]
        for line in _git(repo, "log", "--format=%H %s").splitlines()
        if subject_fragment in line
    ]
    assert len(matches) == 1, f"{subject_fragment!r} matched {len(matches)} commits, expected 1"
    return matches[0]


def _anchored_reader(repo: Path) -> set[str]:
    """The production reader, sourced from the script rather than restated.

    Restating the regex here would let the script and this test drift apart
    silently, which is the failure this file exists to prevent.
    """
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"source {SYNC_SCRIPT}; completion_commit_shas {BEAD_ID} HEAD",
        ],
        cwd=repo,
        capture_output=True,
        check=False,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin", "HOME": str(repo)},
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _commits_the_cleanup_would_orphan() -> str:
    """The live commits that only the anchored reader can see, for the message.

    Computed at failure time and used ONLY inside an assertion message, never
    to decide pass or fail: this repository's history is a moving baseline, and
    a guard keyed to it would go quiet the moment main moved or a seat ran with
    a truncated clone.

    Both readers run over the same revision list, so a shallow or absent
    history yields no commits from either and this refuses to report a count
    rather than claiming zero. A blank answer here means "could not look", and
    it must not read as "nothing would break".
    """
    try:
        anchored = subprocess.run(
            [
                "git",
                "log",
                "origin/main",
                "--extended-regexp",
                "--grep=^Completes-Bead: ",
                "--format=%H",
            ],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.split()
        parsed = subprocess.run(
            ["git", "log", "origin/main", "--format=%H %(trailers:key=Completes-Bead,valueonly)"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.splitlines()
    except (subprocess.CalledProcessError, OSError):
        return "(could not read this repository's history to list them)"

    visible_to_git = {
        line.split(maxsplit=1)[0] for line in parsed if len(line.split(maxsplit=1)) == 2
    }
    if not anchored:
        return "(no Completes-Bead trailers found in this checkout, so none could be listed)"
    orphaned = [sha for sha in anchored if sha not in visible_to_git]
    return (
        f"{len(orphaned)} such commits on origin/main today: {', '.join(s[:12] for s in orphaned)}"
    )


def _git_trailer_reader(repo: Path) -> set[str]:
    """What the obvious cleanup would use instead."""
    out = _git(repo, "log", "HEAD", "--format=%H %(trailers:key=Completes-Bead,valueonly)")
    found = set()
    for line in out.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            found.add(parts[0])
    return found


def test_the_shipped_reader_finds_both_trailer_shapes(fixture_repo: Path) -> None:
    """`completion_commit_shas` is position-independent. This is the guard.

    If this goes red, `completion_commit_shas` has been changed to a reader
    that cannot see a `Completes-Bead:` line written above `Co-Authored-By:`.
    That is the majority shape in merged history, so the change would stop
    closing GitHub issues for those beads and report nothing while doing it.
    """
    orphaned = _sha_of(fixture_repo, "git cannot see")
    same_block = _sha_of(fixture_repo, "git can see")

    found = _anchored_reader(fixture_repo)

    assert orphaned in found, (
        "completion_commit_shas no longer sees a Completes-Bead trailer written "
        "in its own paragraph above Co-Authored-By. That is the shape most "
        "merged commits use, and with this reader their beads' GitHub issues "
        "stay open forever while nothing reports it. "
        f"{_commits_the_cleanup_would_orphan()}. See hy-c7cj."
    )
    assert same_block in found
    assert found == {orphaned, same_block}


def test_gits_own_parser_sees_only_the_same_block_shape(fixture_repo: Path) -> None:
    """The divergence itself, stated executably.

    This is not a defect in git. Git documents the trailer block as the last
    paragraph. It is a defect in the assumption that the two readers are
    interchangeable, which is what makes the cleanup look safe.
    """
    orphaned = _sha_of(fixture_repo, "git cannot see")
    same_block = _sha_of(fixture_repo, "git can see")

    found = _git_trailer_reader(fixture_repo)

    assert same_block in found
    assert orphaned not in found, (
        "git's trailer parser now sees a trailer above Co-Authored-By. If this "
        "is a genuine git behaviour change rather than a broken fixture, the "
        "two readers may have converged and hy-c7cj can be revisited."
    )


def test_the_two_readers_disagree_on_the_orphaned_shape(fixture_repo: Path) -> None:
    """Both readers on both shapes, in one assertion, so the gap is the subject.

    A future reader of this file should be able to see the whole finding
    without running anything: the readers agree on one shape and differ on the
    other, and nothing in either command's output announces which one you ran.
    """
    orphaned = _sha_of(fixture_repo, "git cannot see")

    anchored = _anchored_reader(fixture_repo)
    parsed = _git_trailer_reader(fixture_repo)

    assert anchored - parsed == {orphaned}
    assert parsed - anchored == set()
