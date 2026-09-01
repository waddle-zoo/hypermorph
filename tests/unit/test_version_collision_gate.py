"""The version-collision gate, driven through a stubbed forge and a real repo.

Every assertion this gate makes has to be able to fail loudly, so each one is
fired here rather than described: an alarm nobody has fired is a comment. Two
of the arms are negative in a way that matters -- a gate that can never say
COLLISION passes all of them -- so the two rules each get an arm that proves
the gate can speak, and rule B gets one that proves it still speaks after the
head it would have been compared against has merged.

The forge is stubbed because the gate reads values and merge bases over the
API. Git is real, because the ancestry check is where the quiet failures live:
a missing object and "not an ancestor" are the same non-zero exit, and only a
repo with real commits tells the two apart.
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GATE = str(ROOT / "scripts" / "version-collision-gate.sh")
CONSTANTS = ROOT / "scripts" / "version-constants.tsv"

# Answers what the gate asks of the forge and nothing else. A read with no
# canned answer is a 404, which is how a sha the forge cannot serve, and a
# head with no merge base, both reach the gate.
GH_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG"
for arg in "$@"; do
  case "$arg" in
    */pulls\\?*) cat "$STUB_DIR/pulls.tsv"; exit 0 ;;
    */compare/*...*)
      sha="${arg##*...}"
      if [[ -f "$STUB_DIR/mergebase-$sha" ]]; then cat "$STUB_DIR/mergebase-$sha"; exit 0; fi
      if [[ -f "$STUB_DIR/compare-failure" ]]; then cat "$STUB_DIR/compare-failure" >&2; exit 1; fi
      printf 'gh: Not Found (HTTP 404)\\n' >&2
      exit 1
      ;;
    */contents/*\\?ref=*)
      ref="${arg##*ref=}"
      path="${arg#*/contents/}"
      path="${path%%\\?ref=*}"
      blob="$STUB_DIR/blob-$ref-${path//\\//_}"
      if [[ -f "$blob" ]]; then cat "$blob"; exit 0; fi
      if [[ -f "$STUB_DIR/forge-failure" ]]; then cat "$STUB_DIR/forge-failure" >&2; exit 1; fi
      printf 'gh: Not Found (HTTP 404)\\n' >&2
      exit 1
      ;;
  esac
done
printf 'gh stub: unhandled call: %s\\n' "$*" >&2
exit 1
"""

MISSING = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
ALSO_MISSING = "cafed00dcafed00dcafed00dcafed00dcafed00d"
PATH_UNDER_TEST = "hyperset/bundle/schema.py"


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo, message):
    (repo / "marker").write_text(message)
    _git(repo, "add", "marker")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def sandbox(tmp_path):
    """A repo with a base commit, two commits stacked on it, and a sibling.

    Three shapes, because the rules need all three: the base is what a head
    can inherit, stacked-on-base is a claim legitimately repeated by every
    head above it, and two siblings claiming one value is what rule A exists
    to catch.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "gate@example.invalid")
    _git(repo, "config", "user.name", "Gate Test")
    base = _commit(repo, "base")
    stacked = _commit(repo, "stacked on base")
    higher = _commit(repo, "stacked higher")
    _git(repo, "checkout", "-q", base)
    _git(repo, "checkout", "-q", "-b", "sibling")
    sibling = _commit(repo, "sibling of stacked")
    _git(repo, "checkout", "-q", "main")

    stubs = tmp_path / "stubs"
    binaries = tmp_path / "bin"
    stubs.mkdir()
    binaries.mkdir()
    gh = binaries / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)
    (stubs / "calls.log").write_text("")

    return {
        "repo": repo,
        "stubs": stubs,
        "bin": binaries,
        "base": base,
        "stacked": stacked,
        "higher": higher,
        "sibling": sibling,
    }


def _serve(sandbox, ref, value, *, name="SCHEMA_VERSION", path=PATH_UNDER_TEST, body=None):
    """What the forge returns for one constant's file at one ref."""
    if body is None:
        body = "" if value is None else f"{name} = {value}\n"
    (sandbox["stubs"] / f"blob-{ref}-{path.replace('/', '_')}").write_text(body)


def _merge_base(sandbox, sha, base_sha):
    (sandbox["stubs"] / f"mergebase-{sha}").write_text(f"{base_sha}\n")


def _open_pulls(sandbox, heads):
    (sandbox["stubs"] / "pulls.tsv").write_text(
        "".join(f"{number}\t{sha}\t{ref}\n" for number, sha, ref in heads)
    )


def _constants(sandbox, rows):
    path = sandbox["stubs"] / "constants.tsv"
    path.write_text("".join("\t".join(row) + "\n" for row in rows))
    return str(path)


def _one_constant(sandbox, caveat="-", register="-"):
    return _constants(sandbox, [("SCHEMA_VERSION", PATH_UNDER_TEST, "bare_int", register, caveat)])


def _run(sandbox, *args, constants=None):
    env = {
        "PATH": f"{sandbox['bin']}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        "HOME": str(sandbox["repo"].parent),
        "STUB_DIR": str(sandbox["stubs"]),
        "STUB_LOG": str(sandbox["stubs"] / "calls.log"),
    }
    if constants is None:
        constants = _one_constant(sandbox)
    return subprocess.run(
        [
            "bash",
            GATE,
            "--repo",
            "waddle-zoo/hyperset",
            "--constants",
            constants,
            *args,
        ],
        cwd=sandbox["repo"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _collisions(result):
    return [row for row in result.stdout.splitlines() if "COLLISION" in row]


def test_heads_that_inherit_their_merge_base_value_are_excluded_and_nothing_fires(sandbox):
    """The claim clause. Without it every pair of inheriting heads collides
    and the queue is refused wholesale for claiming nothing."""
    _serve(sandbox, "main", 4)
    for sha in (sandbox["stacked"], sandbox["sibling"]):
        _serve(sandbox, sha, 4)
        _merge_base(sandbox, sha, sandbox["base"])
    _serve(sandbox, sandbox["base"], 4)
    _open_pulls(
        sandbox,
        [(901, sandbox["stacked"], "one"), (902, sandbox["sibling"], "two")],
    )

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    assert "#901 #902" in result.stdout
    assert _collisions(result) == []


def test_two_heads_claiming_one_value_from_different_branches_is_reported(sandbox):
    """ARM 4, rule A, and the only arm that tests what the gate is FOR.

    The refusal arms are all negative -- two prove it refuses, one proves it
    stays quiet -- and a gate that can never say COLLISION passes every one of
    them. A re-implementation printed 'none' with both claims on the screen,
    because zsh does not word-split an unquoted expansion, so the loop ran once
    over the whole accumulated string and the pair guard skipped every
    iteration. Both facts are asserted: the exit code, and a line naming both
    pull requests and the value they both claim.
    """
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    for sha in (sandbox["stacked"], sandbox["sibling"]):
        _serve(sandbox, sha, 5)
        _merge_base(sandbox, sha, sandbox["base"])
    _open_pulls(
        sandbox,
        [(901, sandbox["stacked"], "one"), (902, sandbox["sibling"], "two")],
    )

    result = _run(sandbox)

    assert result.returncode == 3, result.stderr
    assert len(_collisions(result)) == 1, result.stdout
    line = _collisions(result)[0]
    assert "#901" in line
    assert "#902" in line
    assert "SCHEMA_VERSION=5" in line


def test_a_claim_main_already_shipped_still_fires_with_the_other_head_gone(sandbox):
    """ARM 5, rule B, and the reason rule A is not enough.

    This is #164 the moment #165 lands: main is 5, the surviving head is 5,
    and the head it collided with is no longer open, so there is no pair left
    to compare. Against current main the survivor reads as inheriting and the
    gate goes silent in the highest-hazard state in the sequence. Against its
    own merge base it still claims, and rule B names what it collides with.
    """
    _serve(sandbox, "main", 5)
    _serve(sandbox, sandbox["base"], 4)
    _serve(sandbox, sandbox["sibling"], 5)
    _merge_base(sandbox, sandbox["sibling"], sandbox["base"])
    _open_pulls(sandbox, [(164, sandbox["sibling"], "the-survivor")])

    result = _run(sandbox)

    assert result.returncode == 3, result.stderr
    assert len(_collisions(result)) == 1, result.stdout
    line = _collisions(result)[0]
    assert "#164" in line
    assert "SCHEMA_VERSION=5" in line
    assert "main already uses" in line
    # And rule A found nothing, which is the point: one head cannot pair.
    rule_a = result.stdout.split("rule A:", 1)[1].split("rule B:", 1)[0]
    assert "COLLISION" not in rule_a


def test_a_head_that_never_touched_the_constant_stays_quiet_after_main_moves(sandbox):
    """The other half of the merge-base claim test, and the one that decides
    whether it is usable: a head branched before a bump, carrying the old
    value it never wrote, must not be dragged in as a claim. Otherwise every
    open branch fires the day any version lands."""
    _serve(sandbox, "main", 5)
    _serve(sandbox, sandbox["base"], 4)
    _serve(sandbox, sandbox["sibling"], 4)
    _merge_base(sandbox, sandbox["sibling"], sandbox["base"])
    _open_pulls(sandbox, [(902, sandbox["sibling"], "two")])

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    assert "#902" in result.stdout.split("inheriting", 1)[1]
    assert _collisions(result) == []


def test_one_claim_repeated_by_a_head_stacked_above_it_is_not_a_collision(sandbox):
    """ARM 3. A bump low in a stack is claimed again by every head above it,
    legitimately -- #163 and #166 are exactly this shape on RULE_VERSION today
    -- and refusing it would refuse every stacked branch."""
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    for sha in (sandbox["stacked"], sandbox["higher"]):
        _serve(sandbox, sha, 5)
        _merge_base(sandbox, sha, sandbox["base"])
    _open_pulls(
        sandbox,
        [(901, sandbox["stacked"], "one"), (902, sandbox["higher"], "two")],
    )

    result = _run(sandbox)

    assert result.returncode == 0, result.stderr
    assert "prefix-related -- OK" in result.stdout
    assert _collisions(result) == []


def test_a_head_the_forge_will_not_serve_refuses_rather_than_reporting_clean(sandbox):
    """ARM 1. The alternative is a gate that concludes from the heads it could
    read and calls the answer complete."""
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    _serve(sandbox, sandbox["stacked"], 5)
    _merge_base(sandbox, sandbox["stacked"], sandbox["base"])
    _merge_base(sandbox, MISSING, sandbox["base"])
    _open_pulls(
        sandbox,
        [(901, sandbox["stacked"], "one"), (999, MISSING, "gone")],
    )

    result = _run(sandbox)

    assert result.returncode == 1
    assert "REFUSING TO REPORT" in result.stderr
    assert "#999" in result.stderr
    assert MISSING in result.stderr


def test_a_head_with_no_merge_base_refuses(sandbox):
    """The claim test needs two commits, so a head whose merge base the forge
    will not give up is a head this gate cannot classify -- and an
    unclassified head must not become an inheriting one."""
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    _serve(sandbox, MISSING, 5)
    _open_pulls(sandbox, [(999, MISSING, "gone")])

    result = _run(sandbox)

    assert result.returncode == 1
    assert "no merge base" in result.stderr
    assert "#999" in result.stderr


def test_a_merge_base_the_forge_would_not_answer_is_not_a_head_that_lacks_one(sandbox):
    """hy-so04, and the one place this script collapsed the distinction it is
    built on. `merge_base_of` had a single failure exit for three outcomes, so
    a throttled compare was reported as MISSING OBJECT -- a statement about
    the head, made by a gate the forge never answered. The compare is read
    like a blob now: a 404 is a fact about the comparison, anything else is
    the read not happening, and an answer carrying no merge base sha is the
    read not happening too, because taking it makes an empty ref and then a
    confidently empty value.
    """
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["sibling"], 5)
    _open_pulls(sandbox, [(902, sandbox["sibling"], "two")])
    (sandbox["stubs"] / "compare-failure").write_text("gh: API rate limit exceeded (HTTP 403)\n")

    throttled = _run(sandbox)

    _merge_base(sandbox, sandbox["sibling"], "no-merge-base-in-this-answer")
    empty_answer = _run(sandbox)

    assert throttled.returncode == 1
    assert "could not read the merge base" in throttled.stderr
    assert "rate limit" in throttled.stderr
    assert "MISSING OBJECT" not in throttled.stderr
    assert empty_answer.returncode == 1
    assert "without a merge base sha" in empty_answer.stderr
    assert "MISSING OBJECT" not in empty_answer.stderr


def test_a_read_that_resolves_but_yields_no_number_refuses(sandbox):
    """ARM 2, from two measured failures of the same shape -- a `git show
    "$mb:path"` in zsh had `$mb:h` eaten as a dirname modifier, so every read
    returned an empty string while every object resolved and every presence
    guard passed, and the verdict column read 'inherits' for all twelve heads.
    A gate must validate its own OUTPUT is well formed, not merely that its
    inputs resolved."""
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    _serve(sandbox, sandbox["stacked"], None, body='SCHEMA_VERSION = "five"\n')
    _merge_base(sandbox, sandbox["stacked"], sandbox["base"])
    _open_pulls(sandbox, [(901, sandbox["stacked"], "one")])

    result = _run(sandbox)

    assert result.returncode == 1
    assert "value not an integer" in result.stderr
    assert "#901" in result.stderr


def test_an_ancestry_check_it_cannot_run_refuses_instead_of_answering_no(sandbox):
    """The missing-object trap, which is why the exit code of
    `git merge-base --is-ancestor` is triaged rather than swallowed.

    Both heads are readable at the forge and absent from this repo. A scan
    that ran the ancestry check anyway would get the same non-zero exit that
    means 'not an ancestor' and report a collision it never established; one
    that swallowed the error with `|| true` would report no prefix relation,
    in the reassuring direction. Neither answer was measured, so neither is
    given.
    """
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    for sha in (MISSING, ALSO_MISSING):
        _serve(sandbox, sha, 5)
        _merge_base(sandbox, sha, sandbox["base"])
    _open_pulls(sandbox, [(901, MISSING, "gone"), (902, ALSO_MISSING, "also-gone")])

    result = _run(sandbox)

    assert result.returncode == 1
    assert "MISSING OBJECT" in result.stderr
    assert _collisions(result) == []


def _state5_sandbox(tmp_path):
    """Reachability state 5 (hy-nijz): both heads present, the link between them cut.

    origin is linear A->B->C->D, so A is a TRUE ancestor of D. The seat shallow-fetches
    D and then A, so `cat-file -e` passes on both while B and C -- the history that
    connects them -- are absent. `git merge-base --is-ancestor A D` returns 1 here and 0
    on a whole clone, and `git merge-base A D` comes back empty. This is the state an
    existence probe cannot see, and the one that made the pre-repair gate fabricate a
    collision from a pair that is prefix-related everywhere the history is intact.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "gate@example.invalid")
    _git(origin, "config", "user.name", "Gate Test")
    shas = [_commit(origin, letter) for letter in ("A", "B", "C", "D")]
    ancestor, descendant = shas[0], shas[3]

    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", "--no-tags", f"file://{origin}", str(repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "fetch", "-q", "--depth", "1", "origin", ancestor],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    assert _git(repo, "rev-parse", "--is-shallow-repository") == "true"
    for sha in (ancestor, descendant):
        assert (
            subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo).returncode == 0
        ), "the state is only the state while both heads are present"
    # The lie is any non-zero: `--is-ancestor` calls a pair "not an ancestor" that is one
    # on a whole clone. The exact code is version-dependent (hy-t0ot) so it is not pinned.
    # On this runner's git the shallow boundary is a CLEAN non-zero that history_incomplete
    # catches; on another it may be noisy and the stderr arm catches it -- either way the
    # gate refuses, which is what the test asserts.
    isanc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert isanc.returncode != 0, "state 5 needs --is-ancestor to LIE with a non-zero exit"

    stubs = tmp_path / "stubs"
    binaries = tmp_path / "bin"
    stubs.mkdir()
    binaries.mkdir()
    gh = binaries / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)
    (stubs / "calls.log").write_text("")
    return {
        "repo": repo,
        "stubs": stubs,
        "bin": binaries,
        "ancestor": ancestor,
        "descendant": descendant,
    }


def test_a_shallow_seat_that_cannot_see_the_link_refuses_instead_of_fabricating_a_collision(
    tmp_path,
):
    """hy-nijz caller repair, on version-collision-gate.sh's `is_ancestor`.

    Two heads, A and D, both claim SCHEMA_VERSION=5; A is a true ancestor of D, so on a
    whole clone they are prefix-related and EXCUSED. This seat is in state 5 -- both
    present, the link cut -- so `--is-ancestor A D` returns 1, and the pre-repair gate
    read that as 'not prefix-related' and fabricated a COLLISION (exit 3), bouncing a
    legitimate stack on a history it could not see. The seat is SHALLOW, so the repair's
    `history_incomplete` reads rc=1 as UNKNOWN and refuses (exit 1), naming the fetch that
    settles it, rather than answering NO. Reverting the repair reddens this arm -- the
    gate would report exit 3 with a COLLISION line instead.

    The discriminator is the SEAT's completeness, not any per-pair query: a non-empty
    merge base only proves SOME shared ancestor survives, not the one that proves descent
    (a deeper root can survive while the connecting commits are cut), so no per-pair test
    is sound on a truncated seat.
    """
    sb = _state5_sandbox(tmp_path)
    forge_base = "b" * 40  # a sha-shaped merge base the forge serves; not a local object
    _serve(sb, "main", 4)
    _serve(sb, forge_base, 4)
    for sha in (sb["ancestor"], sb["descendant"]):
        _serve(sb, sha, 5)
        _merge_base(sb, sha, forge_base)
    _open_pulls(sb, [(901, sb["ancestor"], "ancestor"), (902, sb["descendant"], "descendant")])

    result = _run(sb)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "UNKNOWN" in result.stderr, result.stderr
    assert "git fetch" in result.stderr
    assert _collisions(result) == []


def _missing_link_sandbox(tmp_path):
    """Both heads present, a connecting commit's OBJECT physically absent (not shallow).

    Linear A->B->C->D with B and C deleted from the object store, so A is a true ancestor
    of D that git cannot walk to: `--is-ancestor A D` exits NON-ZERO and writes `could not
    read <sha>` to stderr while nothing here is shallow. This is the raced-gc /
    aborted-fetch / narrow-refspec-never-fetched shape (adversarial review) -- the cut git
    flags loudly, which the shallow/graft `history_incomplete` alone would miss.

    The exit CODE is deliberately not asserted: it is git-version-dependent for this state
    (1 on git 2.39.5, 128 on 2.47.3), the exact hazard hy-t0ot exists for. What is stable,
    and what the gate keys on, is the non-zero exit and the stderr naming the object.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "gate@example.invalid")
    _git(repo, "config", "user.name", "Gate Test")
    shas = [_commit(repo, letter) for letter in ("A", "B", "C", "D")]
    ancestor, descendant = shas[0], shas[3]
    for cut in (shas[1], shas[2]):
        (repo / ".git" / "objects" / cut[:2] / cut[2:]).unlink()

    assert _git(repo, "rev-parse", "--is-shallow-repository") == "false", (
        "the cut is not a shallow one"
    )
    for sha in (ancestor, descendant):
        assert (
            subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=repo).returncode == 0
        ), "both heads must be present"
    probe = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert probe.returncode != 0, "the cut must make --is-ancestor fail (rc is version-dependent)"
    assert "ould not read" in probe.stderr, "git must flag the absent object on stderr"

    stubs = tmp_path / "stubs"
    binaries = tmp_path / "bin"
    stubs.mkdir()
    binaries.mkdir()
    gh = binaries / "gh"
    gh.write_text(GH_STUB)
    gh.chmod(0o755)
    (stubs / "calls.log").write_text("")
    return {
        "repo": repo,
        "stubs": stubs,
        "bin": binaries,
        "ancestor": ancestor,
        "descendant": descendant,
    }


def test_a_seat_missing_the_connecting_objects_refuses_on_gits_own_error(tmp_path):
    """hy-nijz, the cut git flags LOUDLY (adversarial review).

    The connecting commits' objects are absent though nothing is shallow, so
    `--is-ancestor A D` exits non-zero with `could not read <sha>` on stderr. A seat-shape
    check alone reads this seat as complete, so git's stderr is the only tell: the repair
    keys on that stderr (never on the version-dependent code) and refuses (exit 1), naming
    git's own words, rather than reading the non-zero exit as NOT-AN-ANCESTOR and
    fabricating a collision. This is also the narrow-refspec seat that never fetched the
    link -- same absent-object shape -- caught without a refspec check.
    """
    sb = _missing_link_sandbox(tmp_path)
    forge_base = "b" * 40
    _serve(sb, "main", 4)
    _serve(sb, forge_base, 4)
    for sha in (sb["ancestor"], sb["descendant"]):
        _serve(sb, sha, 5)
        _merge_base(sb, sha, forge_base)
    _open_pulls(sb, [(901, sb["ancestor"], "ancestor"), (902, sb["descendant"], "descendant")])

    result = _run(sb)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "UNKNOWN" in result.stderr, result.stderr
    assert "ould not read" in result.stderr, "git's own error must reach the caller"
    assert _collisions(result) == []


def test_a_narrow_refspec_seat_that_holds_the_history_still_reports_a_real_collision(sandbox):
    """The other side of hy-nijz: refusing rc=1 on a narrow-refspec seat WHOLESALE would
    disable rule A on every crew seat (adversarial review). The gate keys on whether git
    could WALK the history, not on the refspec, so a narrow-configured seat that actually
    holds both heads' history reports a genuine collision. Two real siblings claim 5, the
    seat has a main-only refspec but the full local history, and `--is-ancestor` walks
    cleanly (empty stderr, not shallow) -- so the collision fires (exit 3), not a refusal.
    """
    _git(
        sandbox["repo"],
        "config",
        "remote.origin.fetch",
        "+refs/heads/main:refs/remotes/origin/main",
    )
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    for sha in (sandbox["stacked"], sandbox["sibling"]):
        _serve(sandbox, sha, 5)
        _merge_base(sandbox, sha, sandbox["base"])
    _open_pulls(sandbox, [(901, sandbox["stacked"], "one"), (902, sandbox["sibling"], "two")])

    result = _run(sandbox)

    assert result.returncode == 3, result.stdout + result.stderr
    assert len(_collisions(result)) == 1, result.stdout
    assert "SCHEMA_VERSION=5" in _collisions(result)[0]


def _with_origin(sandbox, refspec):
    """Give the sandbox repo an origin it really fetched, under one refspec.

    Crew seats on this rig are clones with a main-only refspec, so the fetch
    scope is the difference between a commit that does not exist and one the
    seat was never configured to see. Both arms below need a real origin: a
    refspec nothing ever fetched proves nothing about what the seat can reach.
    """
    origin = sandbox["repo"].parent / f"origin-{refspec.count('*')}.git"
    if not origin.exists():
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(sandbox["repo"]), str(origin)],
            check=True,
            capture_output=True,
        )
    _git(sandbox["repo"], "remote", "add", "origin", str(origin))
    _git(sandbox["repo"], "config", "remote.origin.fetch", refspec)
    _git(sandbox["repo"], "fetch", "-q", "origin")
    return origin


def _remote_refs(repo):
    """The refs this checkout holds, read the way the gate reads them.

    The count is not a constant. Git 2.39 leaves refs/remotes/origin/HEAD
    unwritten where newer git writes it on fetch, so the main-only arm below
    measured 1 here and a larger number in CI on the same commit (hy-4dci,
    PR #189). Writing the local number down as universal is exactly the defect
    this file exists to catch one level up -- a seat's environment reported as
    a fact about the repository -- so the expected count is measured, and the
    fact that distinguishes the two arms is which refs are present.
    """
    return _git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes").splitlines()


def _absent_heads(sandbox):
    """Two heads the forge serves and this repo does not hold."""
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    for sha in (MISSING, ALSO_MISSING):
        _serve(sandbox, sha, 5)
        _merge_base(sandbox, sha, sandbox["base"])
    _open_pulls(sandbox, [(901, MISSING, "gone"), (902, ALSO_MISSING, "also-gone")])


def test_an_absence_is_reported_with_the_configuration_that_would_explain_it(sandbox):
    """A seat that never fetched a branch cannot tell a commit that does not
    exist from one it was never configured to see, so the refusal publishes
    which of those it can rule out.

    The repair is named on the object, not on the history: `--unshallow`
    lengthens a truncated clone and leaves a main-only seat exactly as blind,
    with a receipt saying it was repaired (hy-4dci).
    """
    _with_origin(sandbox, "+refs/heads/main:refs/remotes/origin/main")
    _absent_heads(sandbox)

    result = _run(sandbox)

    assert result.returncode == 1
    assert "MISSING OBJECT" in result.stderr
    assert "this checkout does not hold it" in result.stderr
    assert "remote.origin.fetch=+refs/heads/main:refs/remotes/origin/main" in result.stderr
    held = _remote_refs(sandbox["repo"])
    # The trailing "." is load-bearing, not cosmetic. The script prints
    # "refs/remotes=<count>. Repair with ...", so an undelimited
    # f"refs/remotes={len(held)}" is a substring of a PREFIX-EXTENDED count --
    # refs/remotes=1 sits inside refs/remotes=17 and refs/remotes=193 -- and the
    # measured count here is small (1 on git 2.39), the worst case for the hazard.
    # Asserting the delimiter binds the whole number (hy-xzlw).
    assert f"refs/remotes={len(held)}." in result.stderr
    assert "refs/remotes/origin/sibling" not in held
    assert f"git fetch origin {MISSING}" in result.stderr
    assert "refs/pull/901/head" in result.stderr
    assert "not `git fetch --unshallow`" in result.stderr


def test_the_published_scope_is_measured_and_not_a_sentence_about_this_rig(sandbox):
    """The same absence in a seat with a full refspec prints the full refspec.

    Without this arm the line above passes on a hardcoded string, which is the
    failure it exists to prevent one level up: a seat's configuration reported
    as a fact about the repository.
    """
    _with_origin(sandbox, "+refs/heads/*:refs/remotes/origin/*")
    _absent_heads(sandbox)

    result = _run(sandbox)

    assert result.returncode == 1
    assert "remote.origin.fetch=+refs/heads/*:refs/remotes/origin/*" in result.stderr
    held = _remote_refs(sandbox["repo"])
    # Delimited for the same prefix-extension reason as the arm above (hy-xzlw).
    assert f"refs/remotes={len(held)}." in result.stderr
    assert "refs/remotes/origin/sibling" in held
    assert "refs/heads/main:refs/remotes/origin/main;" not in result.stderr


def test_an_enumeration_that_returns_nothing_is_not_a_clean_queue(sandbox):
    """Seeing no heads and seeing no collision are different facts, and only
    one of them may exit 0."""
    _serve(sandbox, "main", 4)
    _open_pulls(sandbox, [])

    result = _run(sandbox)

    assert result.returncode == 1
    assert "must not report clean" in result.stderr


def test_the_head_set_cannot_be_supplied(sandbox):
    """A literal PR list in a release gate goes stale the moment someone opens
    a PR, and it goes stale quietly, so the gate offers no way to pass one."""
    _serve(sandbox, "main", 4)
    _open_pulls(sandbox, [(901, sandbox["stacked"], "one")])

    result = _run(sandbox, "--heads", "901,902")

    assert result.returncode == 2
    assert "unknown argument: --heads" in result.stderr


def test_every_constant_on_the_list_is_checked_not_only_the_first(sandbox):
    """The list is configuration, and a gate that read only its first row
    would pass every run for years while the second constant collided."""
    other = "hyperset/processor/rules.py"
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    _serve(sandbox, "main", 1, name="RULE_VERSION", path=other)
    _serve(sandbox, sandbox["base"], 1, name="RULE_VERSION", path=other)
    for sha in (sandbox["stacked"], sandbox["sibling"]):
        _serve(sandbox, sha, 4)
        _serve(sandbox, sha, 2, name="RULE_VERSION", path=other)
        _merge_base(sandbox, sha, sandbox["base"])
    _open_pulls(
        sandbox,
        [(901, sandbox["stacked"], "one"), (902, sandbox["sibling"], "two")],
    )

    result = _run(
        sandbox,
        constants=_constants(
            sandbox,
            [
                ("SCHEMA_VERSION", PATH_UNDER_TEST, "bare_int", "-", "-"),
                ("RULE_VERSION", other, "bare_int", "-", "-"),
            ],
        ),
    )

    assert result.returncode == 3, result.stderr
    assert len(_collisions(result)) == 1, result.stdout
    assert "RULE_VERSION=2" in _collisions(result)[0]


def test_a_name_that_merely_starts_with_the_constants_name_is_not_the_constant(sandbox):
    """`RULE_VERSIONS = {RULE_ID: RULE_VERSION}` sits directly under
    `RULE_VERSION = 1` in the real file, and any neighbour whose name starts
    with the configured one is the same hazard. An extractor anchored on a
    prefix rather than on the whole name reads whichever such line comes
    first, and every value it reports after that is someone else's number.
    """
    other = "hyperset/processor/rules.py"
    body = "RULE_VERSION_FLOOR = 9\nRULE_VERSION = 1\nRULE_VERSIONS = {RULE_ID: RULE_VERSION}\n"
    bumped = "RULE_VERSION_FLOOR = 9\nRULE_VERSION = 2\nRULE_VERSIONS = {RULE_ID: RULE_VERSION}\n"
    _serve(sandbox, "main", None, name="RULE_VERSION", path=other, body=body)
    _serve(sandbox, sandbox["base"], None, name="RULE_VERSION", path=other, body=body)
    _serve(sandbox, sandbox["sibling"], None, name="RULE_VERSION", path=other, body=bumped)
    _merge_base(sandbox, sandbox["sibling"], sandbox["base"])
    _open_pulls(sandbox, [(902, sandbox["sibling"], "two")])

    result = _run(
        sandbox,
        constants=_constants(sandbox, [("RULE_VERSION", other, "bare_int", "-", "-")]),
    )

    assert result.returncode == 0, result.stderr
    assert "main currently: 1" in result.stdout
    assert "#902 " in result.stdout.split("claims", 1)[1]


def test_a_change_the_gate_cannot_see_is_disclosed_rather_than_covered(sandbox):
    """What this gate establishes is not uniform across the list, and the run
    output is what a reader has in front of them when they decide to merge --
    so a constant's caveat is printed whenever the gate speaks about it, not
    only by --help.

    `RULE_VERSION` is the row that needs one. Its number describes a
    JUDGEMENT rather than a payload shape: a head that bumps 1 -> 2 passes on
    values alone however differently it now judges, and two such heads are
    both honestly "version 2 of this rule", which only the diff separates.
    That is a different blindness from the register one -- `RULE_VERSIONS`
    gaining a member DOES move this number (hq-6htz case 3, amended
    2026-07-30) and is refused by `check_register`, not disclosed by a
    caveat. `RULE_VERSIONS` reaches `processor_runs.rule_versions` through
    `engine.py` and `postgres/processor.py`, and back into
    `ProcessorRunRecord` at `repositories/dto.py`; it reaches no client
    surface. The served thing is the scalar `rule_version` on a finding.
    """
    other = "hyperset/processor/rules.py"
    before = "RULE_VERSION = 1\nRULE_VERSIONS = {approved_expression_drift: 1}\n"
    after = "RULE_VERSION = 2\nRULE_VERSIONS = {approved_expression_drift: 2}\n"
    _serve(sandbox, "main", None, name="RULE_VERSION", path=other, body=before)
    _serve(sandbox, sandbox["base"], None, name="RULE_VERSION", path=other, body=before)
    _serve(sandbox, sandbox["sibling"], None, name="RULE_VERSION", path=other, body=after)
    _merge_base(sandbox, sandbox["sibling"], sandbox["base"])
    _open_pulls(sandbox, [(902, sandbox["sibling"], "two")])

    result = _run(
        sandbox,
        constants=_constants(
            sandbox,
            [("RULE_VERSION", other, "bare_int", "RULE_VERSIONS", "the DIFF is not read")],
        ),
    )

    assert result.returncode == 0, result.stderr
    assert "caveat: the DIFF is not read" in result.stdout


def test_an_extraction_the_gate_cannot_perform_is_a_usage_error(sandbox):
    """Not a refusal: exit 1 means the gate looked and could not see, and a
    constant list naming a reader that does not exist was never a looking
    problem. It is also caught when the list is read, before any head is
    classified, so it cannot arrive as an empty value inside a subshell and be
    mistaken for an unreadable file."""
    _serve(sandbox, "main", 4)
    _open_pulls(sandbox, [(901, sandbox["stacked"], "one")])

    result = _run(
        sandbox,
        constants=_constants(sandbox, [("SCHEMA_VERSION", PATH_UNDER_TEST, "yaml_key", "-", "-")]),
    )

    assert result.returncode == 2
    assert "yaml_key" in result.stderr


def test_a_constant_absent_at_the_merge_base_is_a_claim_of_the_value_it_introduced(sandbox):
    """The third read outcome, and the one that looks like good news.

    A constant that does not exist at a head's merge base is the NORMAL case
    for a newly introduced one, and the tempting read is "no value at base, so
    no claim, so nothing to compare, so quiet" -- a clean all-clear from an
    instrument that saw nothing. Absent at base and present at head IS a
    claim, of the value at head, and it goes through the same collision test
    as any other.
    """
    _serve(sandbox, "main", 5)
    _serve(sandbox, sandbox["base"], None, body="# no constant here yet\n")
    _serve(sandbox, sandbox["sibling"], 5)
    _merge_base(sandbox, sandbox["sibling"], sandbox["base"])
    _open_pulls(sandbox, [(902, sandbox["sibling"], "two")])

    result = _run(sandbox)

    assert result.returncode == 3, result.stderr
    assert "#902" in result.stdout.split("claims", 1)[1].split("inheriting", 1)[0]
    assert "main already uses" in _collisions(result)[0]


def test_a_read_that_failed_is_refused_in_different_words_than_one_that_answered(sandbox):
    """Network, auth and rate limit are not "the constant is not there". Both
    arrive as a non-zero exit from the same command, and a gate that reads
    every non-zero as absence turns a throttled minute into a queue-wide
    all-clear."""
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    _merge_base(sandbox, sandbox["sibling"], sandbox["base"])
    _open_pulls(sandbox, [(902, sandbox["sibling"], "two")])
    # No blob for the head, and the stub is told to fail the way a throttled
    # forge fails rather than the way a missing file does.
    (sandbox["stubs"] / "forge-failure").write_text("gh: API rate limit exceeded (HTTP 403)\n")

    result = _run(sandbox)

    assert result.returncode == 1
    assert "could not read" in result.stderr
    assert "rate limit" in result.stderr


def test_a_constant_list_naming_nothing_cannot_report_a_clean_queue(sandbox):
    """Once the list is data, the gate can be pointed at an empty or truncated
    one and correctly find no collisions across zero constants. Checked-zero
    must not be a way to exit 0."""
    _serve(sandbox, "main", 4)
    _open_pulls(sandbox, [(901, sandbox["stacked"], "one")])
    empty = sandbox["stubs"] / "empty.tsv"
    empty.write_text("# every row commented out\n")

    result = _run(sandbox, constants=str(empty))

    assert result.returncode == 2
    assert "names no constants" in result.stderr


def test_a_listed_path_that_main_does_not_have_is_refused(sandbox):
    """A row can name a file that was renamed or never existed. Reading no
    value there and reporting no collision is the same all-clear from the same
    blindness, one layer up."""
    _serve(sandbox, "main", 4)
    _open_pulls(sandbox, [(901, sandbox["stacked"], "one")])

    result = _run(
        sandbox,
        constants=_constants(
            sandbox, [("SCHEMA_VERSION", "hyperset/gone/away.py", "bare_int", "-", "-")]
        ),
    )

    assert result.returncode == 1
    assert "hyperset/gone/away.py" in result.stderr
    assert "main" in result.stderr


def test_a_run_that_reached_the_sweep_states_how_many_constants_and_which(sandbox):
    """A caveat beside a PASS reads the same sentence whether the gate checked
    two constants or none. The count is the only part of the scope a reader
    can act on, so it is printed once the constant list has been read --
    passing, colliding, or refusing. Before that there is no list to state,
    which is every remaining silent exit: an unusable list and an origin the
    repo could not be resolved from."""
    other = "hyperset/processor/rules.py"
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    _serve(sandbox, "main", 1, name="RULE_VERSION", path=other)
    _serve(sandbox, sandbox["base"], 1, name="RULE_VERSION", path=other)
    _serve(sandbox, sandbox["sibling"], 4)
    _serve(sandbox, sandbox["sibling"], 1, name="RULE_VERSION", path=other)
    _merge_base(sandbox, sandbox["sibling"], sandbox["base"])
    _open_pulls(sandbox, [(902, sandbox["sibling"], "two")])

    result = _run(
        sandbox,
        constants=_constants(
            sandbox,
            [
                ("SCHEMA_VERSION", PATH_UNDER_TEST, "bare_int", "-", "-"),
                ("RULE_VERSION", other, "bare_int", "-", "-"),
            ],
        ),
    )

    assert result.returncode == 0, result.stderr
    scope = [row for row in result.stdout.splitlines() if row.startswith("--- scope")]
    assert len(scope) == 1, result.stdout
    assert "2 constant(s)" in scope[0]
    assert "1 open head(s)" in scope[0]
    assert "SCHEMA_VERSION" in scope[0]
    assert "RULE_VERSION" in scope[0]


def test_a_run_that_refuses_partway_still_states_what_it_set_out_to_cover(sandbox):
    """The arm the passing one above cannot reach, and the reason the scope
    line is printed BEFORE the first constant rather than after the last.

    A reader of a refusal needs the scope more than a reader of a PASS does:
    the refusal tells them the sweep stopped, and only the scope tells them
    how much of it they are now missing. Printed at the end, the line was
    present on exactly the runs that did not need it and absent from every
    run that did.
    """
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    _merge_base(sandbox, MISSING, sandbox["base"])
    _open_pulls(sandbox, [(999, MISSING, "gone")])

    result = _run(sandbox)

    assert result.returncode == 1
    assert "REFUSING TO REPORT" in result.stderr
    scope = [row for row in result.stdout.splitlines() if row.startswith("--- scope")]
    assert len(scope) == 1, result.stdout
    assert "1 constant(s)" in scope[0]
    assert "SCHEMA_VERSION" in scope[0]


def test_a_run_that_enumerated_no_head_states_the_scope_it_covered_none_of(sandbox):
    """The refusal that costs the reader the WHOLE sweep, not part of it.

    The constant list has been read by the time enumeration answers, so this
    run knows exactly what it set out to cover and covered none of -- and a
    refusal with no scope line reads as a gate that never had a list. The head
    set is stated as a phrase rather than a count: "0 open head(s)" beside an
    enumeration that failed would collapse "could not read the head set" into
    "there are no heads", which is the same collapse the exit codes keep
    apart.
    """
    _serve(sandbox, "main", 4)
    _open_pulls(sandbox, [])

    result = _run(sandbox)

    assert result.returncode == 1
    scope = [row for row in result.stdout.splitlines() if row.startswith("--- scope")]
    assert len(scope) == 1, result.stdout
    assert "1 constant(s)" in scope[0]
    assert "SCHEMA_VERSION" in scope[0]
    assert "no open head at all" in scope[0]


def test_a_persisted_register_gaining_a_member_with_the_number_still_refuses(sandbox):
    """The arm hq-6htz case 3 was amended into on 2026-07-30.

    A PERSISTED-ONLY register gaining a member MOVES the number: a stored row
    carries no record of which register was in force when it was written, and
    the version on that row is that record. The gate compares values, so a
    member added while the number stands still is a change it is structurally
    blind to -- and the honest answer to a change it cannot weigh is a
    refusal naming the member, not silence. Written as a FIRING arm on
    purpose: the shape it replaces asserted the gate stayed quiet, which
    passes against a gate that does nothing at all.
    """
    other = "hyperset/processor/rules.py"
    before = "RULE_VERSION = 1\nRULE_VERSIONS = {approved_expression_drift: 1}\n"
    after = "RULE_VERSION = 1\nRULE_VERSIONS = {approved_expression_drift: 1, moved_side: 1}\n"
    _serve(sandbox, "main", None, name="RULE_VERSION", path=other, body=before)
    _serve(sandbox, sandbox["base"], None, name="RULE_VERSION", path=other, body=before)
    _serve(sandbox, sandbox["sibling"], None, name="RULE_VERSION", path=other, body=after)
    _merge_base(sandbox, sandbox["sibling"], sandbox["base"])
    _open_pulls(sandbox, [(902, sandbox["sibling"], "two")])

    result = _run(
        sandbox,
        constants=_constants(sandbox, [("RULE_VERSION", other, "bare_int", "RULE_VERSIONS", "-")]),
    )

    assert result.returncode == 1
    assert "moved_side" in result.stderr
    assert "RULE_VERSIONS" in result.stderr
    assert "without moving RULE_VERSION" in result.stderr


def test_a_register_gaining_a_member_with_the_number_moved_is_not_refused(sandbox):
    """The other side of the same rule, so the refusal above is a judgement
    and not a reflex: a head that adds a member AND moves the number has done
    what the amendment requires, and is judged on its value like any claim."""
    other = "hyperset/processor/rules.py"
    before = "RULE_VERSION = 1\nRULE_VERSIONS = {approved_expression_drift: 1}\n"
    after = "RULE_VERSION = 2\nRULE_VERSIONS = {approved_expression_drift: 1, moved_side: 2}\n"
    _serve(sandbox, "main", None, name="RULE_VERSION", path=other, body=before)
    _serve(sandbox, sandbox["base"], None, name="RULE_VERSION", path=other, body=before)
    _serve(sandbox, sandbox["sibling"], None, name="RULE_VERSION", path=other, body=after)
    _merge_base(sandbox, sandbox["sibling"], sandbox["base"])
    _open_pulls(sandbox, [(902, sandbox["sibling"], "two")])

    result = _run(
        sandbox,
        constants=_constants(sandbox, [("RULE_VERSION", other, "bare_int", "RULE_VERSIONS", "-")]),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert _collisions(result) == []


def test_refusal_and_collision_do_not_share_an_exit_code(sandbox):
    """1 says 'I could not look', 3 says 'I looked and found a problem'.
    Collapsing them is the failure this gate exists against, so the two are
    pinned against each other rather than each against a literal."""
    _serve(sandbox, "main", 4)
    _serve(sandbox, sandbox["base"], 4)
    for sha in (sandbox["stacked"], sandbox["sibling"]):
        _serve(sandbox, sha, 5)
        _merge_base(sandbox, sha, sandbox["base"])
    _open_pulls(
        sandbox,
        [(901, sandbox["stacked"], "one"), (902, sandbox["sibling"], "two")],
    )
    collision = _run(sandbox)

    _merge_base(sandbox, MISSING, sandbox["base"])
    _open_pulls(sandbox, [(999, MISSING, "gone")])
    refusal = _run(sandbox)

    assert collision.returncode == 3
    assert refusal.returncode == 1
    assert collision.returncode != refusal.returncode
    assert 0 not in (collision.returncode, refusal.returncode)


def test_the_help_text_states_both_halves_of_what_exit_0_means(sandbox):
    """The second sentence is the scope, not a caveat: this gate catches a
    collision BETWEEN branches, and no test in the repo fails on a wrong
    number on a single one. With no other cross-tree version check anywhere in
    the repository, the gate being green is the only thing anyone can point
    at, so what it does not establish travels with it."""
    result = _run(sandbox, "--help")

    assert result.returncode == 0
    assert "Exit 0 means no collision among CLAIMING heads." in result.stdout
    assert (
        "Exit 0 does not mean the version numbers are correct -- nothing tests that."
        in result.stdout
    )


def test_help_prints_the_caveat_of_every_configured_constant(sandbox):
    result = _run(sandbox, "--help", constants=_one_constant(sandbox, caveat="only 5s"))

    assert "SCHEMA_VERSION: only 5s" in result.stdout


def test_the_shipped_constant_list_covers_what_a_client_or_a_row_can_observe():
    """The inclusion test is whether a CLIENT can observe the number, or a
    stored row's meaning depends on it. `schema_version` is on every bundle,
    `rule_version` on every finding and on every persisted processor run, so
    both are listed, each declaring how it is read rather than leaving the
    gate to assume -- and `RULE_VERSION` declares the register whose
    membership its number is supposed to move for."""
    rows = [
        line.split("\t")
        for line in CONSTANTS.read_text().splitlines()
        if line and not line.startswith("#")
    ]
    listed = {row[0]: row for row in rows}

    assert set(listed) == {"SCHEMA_VERSION", "RULE_VERSION"}
    assert listed["SCHEMA_VERSION"][1] == "hyperset/bundle/schema.py"
    assert listed["RULE_VERSION"][1] == "hyperset/processor/rules.py"
    for row in rows:
        assert len(row) == 5, row
        assert row[2] == "bare_int"
        assert (ROOT / row[1]).exists()
    assert listed["RULE_VERSION"][3] == "RULE_VERSIONS"
    assert listed["RULE_VERSION"][4] != "-", "the judgement caveat is not optional"


def test_the_gate_is_executable():
    assert os.access(GATE, os.X_OK)
