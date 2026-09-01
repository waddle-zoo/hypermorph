"""The hy-asip reference check, driven over real repositories.

The script asks one question at merge time: which files mention the changed
module by name, and did main modify any of them since the merge base? First
half alone is a note, both halves is a hold. It is a grep over the merged tree
and not an import graph on purpose -- the references that go stale unseen are
the ones no import graph holds, in docstrings, comments, skip reasons and docs.

It arrived here already base-rated against five landed merges in the refinery's
scratchpad, where it could not be reviewed and could not be run by anyone else.
Porting it is only worth something if the four instrument defects it found by
being tested survive the move, so each of them gets an arm here that fails when
the defect comes back, plus the fifth arm amendment 4 added:

1. A module is matched by base name or dotted path, never the bare stem. The
   first version matched "provenance" as an English word and HELD on four of
   five known-good merges.
2. `git rev-parse --verify <40-hex>` succeeds for an object no repository
   holds. It validates spelling. The hazard is specific to the full 40
   characters -- a short fake sha refuses correctly, so a guard tested with one
   looks sound while being broken.
3. The blind control cannot be Python-shaped. Probing for a def/class skipped
   every non-Python file, which is exactly the docstring and documentation case
   the check exists for; the two real hits in the base rating were Markdown.
4. A file the PR deletes is in the diff and absent from the merged tree. Read
   the pre-merge blob, because a reference to a module that just disappeared is
   the sharpest form of this hazard, not a reason to refuse.
5. An empty result read out of a field nobody checked exists is not an absence.
   `git grep -l <pattern> <tree>` prefixes its output; if that prefix is not
   there the strip is a no-op and both halves intersect to nothing, printing a
   HOLD as a clean answer.

Every arm below is paired with one that makes the script speak. A check that
can only refuse, or can only report clean, passes a suite made entirely of its
own preferred answer -- which is the disease this script was written to catch
in other people's tooling.
"""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REFCHECK = str(ROOT / "scripts" / "refcheck.sh")

# All forty characters. `git rev-parse --verify -q` returns 0 and echoes this
# back in any repository; a shorter fake refuses at rc=1, which is why a test
# written with one passes against a guard that never worked.
ABSENT = "deadbeef" * 5
ABSENT_SHORT = "deadbeef"

# The five merges the script was base-rated against, with the answer each one
# produced in the refinery's scratchpad before the port.
BASE_RATING = (
    ("8ae4a442", 0, "#190"),
    ("28e682c7", 3, "#187"),
    ("9e36a386", 2, "#189"),
    ("c34eb7e7", 2, "#192"),
    ("2e7992b7", 2, "#193"),
)

LONG = "This line is long enough to serve as a control probe for the grep.\n"


def _git(repo, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check)


def _write(repo, path, text):
    target = Path(repo) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _git(repo, "add", "--", path)


def _commit(repo, message):
    _git(repo, "commit", "-q", "-a", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _scenario(tmp_path, *, base, main_edit=None, pr_edit=None, pr_delete=(), pr_rename=None):
    """A merge base, one commit on main, one on the PR branch, and their merge.

    A real merge rather than a synthesised tree: the script is handed whatever
    the merger has in front of it, and building the input a different way than
    the caller does would test a shape nobody runs.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "refcheck@example.invalid")
    _git(repo, "config", "user.name", "Refcheck Test")
    for path, text in base.items():
        _write(repo, path, text)
    mb = _commit(repo, "merge base")

    _git(repo, "checkout", "-q", "-b", "pr")
    for path, text in (pr_edit or {}).items():
        _write(repo, path, text)
    for path in pr_delete:
        _git(repo, "rm", "-q", "--", path)
    # `git mv` rather than a delete plus a write, because the point of the arm
    # that uses this is what git's rename DETECTION does with the pair, and a
    # rename it declines to detect would leave that arm passing for the wrong
    # reason (hy-8v1k).
    for old, new in (pr_rename or {}).items():
        _git(repo, "mv", "--", old, new)
    _commit(repo, "the pull request")

    _git(repo, "checkout", "-q", "main")
    for path, text in (main_edit or {}).items():
        _write(repo, path, text)
    main = _commit(repo, "main moves on") if main_edit else mb
    _git(repo, "merge", "-q", "--no-edit", "pr")
    tree = _git(repo, "rev-parse", "HEAD").stdout.strip()

    return {"repo": repo, "tree": tree, "main": main, "mb": mb}


def _run(scenario, *, tree=None, main=None, mb=None, git_stub=None):
    env = None
    if git_stub is not None:
        import os

        env = dict(os.environ)
        env["PATH"] = f"{git_stub}:{env['PATH']}"
    return subprocess.run(
        [
            "bash",
            REFCHECK,
            tree or scenario["tree"],
            main or scenario["main"],
            mb or scenario["mb"],
        ],
        cwd=scenario["repo"],
        capture_output=True,
        text=True,
        env=env,
    )


def _stub_git(tmp_path, body):
    """A `git` earlier on PATH that forwards everything except one subcommand."""
    directory = tmp_path / "stub-bin"
    directory.mkdir(exist_ok=True)
    real = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
    script = directory / "git"
    script.write_text(f'#!/usr/bin/env bash\nREAL_GIT={real}\n{body}\nexec "$REAL_GIT" "$@"\n')
    script.chmod(0o755)
    return str(directory)


# A module, a file that uses the word as English prose, and a file that names
# the module. Only the third is a reference.
NAMED = {
    "hyperset/evals/provenance.py": (
        '"""Pins a recording to the commit that produced it."""\n\n'
        "def record_the_commit_that_produced_this_recording(root):\n"
        "    return root\n"
    ),
    "docs/prose.md": (
        "# Provenance\n\n"
        "Provenance is what an evaluation needs before its scores mean anything.\n"
        "Every claim about provenance in this document is about the idea.\n"
    ),
    "docs/names.md": (
        "# Where the pin lives\n\n"
        "The pin is written by hyperset/evals/provenance.py at record time.\n"
    ),
}


def test_an_english_word_that_is_also_the_module_stem_is_not_a_reference(tmp_path):
    """docs/prose.md says "provenance" three times and never names the module.

    Main modified exactly that file, so a check matching the bare stem answers
    HOLD here. The first version of this script did, on four of five known-good
    merges. Matching by base name or dotted path leaves docs/names.md as the
    only hit, and main did not touch it: a note.
    """
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/prose.md": NAMED["docs/prose.md"] + "\nProvenance again.\n" + LONG},
        pr_edit={
            "hyperset/evals/provenance.py": NAMED["hyperset/evals/provenance.py"] + "\n# amended\n"
        },
    )

    result = _run(scenario)

    assert result.returncode == 3, result.stderr
    assert "note  hyperset/evals/provenance.py" in result.stdout
    assert "prose.md" not in result.stdout
    assert "HOLD" not in result.stdout


def test_a_file_that_names_the_module_and_that_main_moved_is_a_hold(tmp_path):
    """The same shape with main touching the file that does name the module.

    Without this arm the one above passes against a check that can never say
    HOLD, which is the failure mode the whole script exists to report on.
    """
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/names.md": NAMED["docs/names.md"] + LONG},
        pr_edit={
            "hyperset/evals/provenance.py": NAMED["hyperset/evals/provenance.py"] + "\n# amended\n"
        },
    )

    result = _run(scenario)

    assert result.returncode == 2, result.stderr
    assert "HOLD  hyperset/evals/provenance.py" in result.stdout
    assert "docs/names.md" in result.stdout


def test_a_module_this_pull_request_renamed_still_reports_its_references(tmp_path):
    """The arm that fails without `--no-renames` (hy-8v1k).

    The PR moves hyperset/evals/provenance.py to hyperset/evals/pins.py and
    docs/names.md, which main modified, still cites the old path. Rename
    detection collapses the pair to the NEW path alone, so CHANGED holds only
    pins.py, nothing ever greps for `provenance.py`, and the stale citation
    reads as NO REFERENCE INTERACTION at exit 0. A reference goes stale by
    continuing to name the old path -- so the detecting form drops precisely
    the class this script exists for, and drops it toward the clean answer.

    Undetected, the old path arrives as a deletion and reaches the
    `[DELETED by this PR]` route that was already here and that a rename had
    no way of reaching.
    """
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/names.md": NAMED["docs/names.md"] + LONG},
        pr_rename={"hyperset/evals/provenance.py": "hyperset/evals/pins.py"},
    )

    assert (
        _git(scenario["repo"], "diff", "--name-only", scenario["main"], scenario["tree"]).stdout
        == "hyperset/evals/pins.py\n"
    ), "the fixture is only the fixture while git DETECTS the rename"

    result = _run(scenario)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "HOLD  hyperset/evals/provenance.py [DELETED by this PR]" in result.stdout
    assert "docs/names.md" in result.stdout


def test_a_rename_nothing_cites_by_its_old_path_reads_clean(tmp_path):
    """`--no-renames` puts a second path in every renamed PR's changed set, so
    the control is that the extra path does not manufacture a hold. The same
    rename, with main touching only docs/prose.md -- which uses "provenance"
    as English and never names the module. Without this arm the one above
    passes against a script that holds on everything."""
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/prose.md": NAMED["docs/prose.md"] + LONG},
        pr_rename={"hyperset/evals/provenance.py": "hyperset/evals/pins.py"},
    )

    result = _run(scenario)

    assert result.returncode == 3, result.stdout + result.stderr
    assert "HOLD" not in result.stdout
    assert "note  hyperset/evals/provenance.py" in result.stdout


MARKDOWN = {
    "docs/development/benchmark.md": (
        "# Benchmark\n\nThe scored arms and what each of them is measuring.\n"
    ),
    "docs/index.md": (
        "# Documentation\n\nThe benchmark is described in docs/development/benchmark.md.\n"
    ),
}


def test_the_control_runs_on_a_markdown_only_change(tmp_path):
    """A Markdown file, referenced by name, that main moved: a HOLD.

    The earlier control probed for a `def` or `class`, found neither in any
    Markdown file, and skipped it -- silently, so the check reported on a file
    set it had never searched. Both real hits in the five-merge base rating
    were Markdown, so the version with that control had never once run on the
    files that produced its findings.
    """
    scenario = _scenario(
        tmp_path,
        base=MARKDOWN,
        main_edit={"docs/index.md": MARKDOWN["docs/index.md"] + LONG},
        pr_edit={"docs/development/benchmark.md": MARKDOWN["docs/development/benchmark.md"] + LONG},
    )

    result = _run(scenario)

    assert result.returncode == 2, result.stderr
    assert "HOLD  docs/development/benchmark.md" in result.stdout
    assert "docs/index.md" in result.stdout


def test_a_file_with_no_usable_probe_refuses_rather_than_reporting_blind(tmp_path):
    """No line long enough to search for is no control, and no control is no
    report. The refusal has to be reachable or the arm above proves nothing."""
    scenario = _scenario(
        tmp_path,
        base=dict(MARKDOWN, **{"docs/tiny.md": "# T\n\nshort\nlines\n"}),
        main_edit={"docs/index.md": MARKDOWN["docs/index.md"] + LONG},
        pr_edit={"docs/tiny.md": "# T\n\nshort\nlines\nmore\n"},
    )

    result = _run(scenario)

    assert result.returncode == 1
    assert "no usable control probe in docs/tiny.md" in result.stderr


DELETED = {
    "hyperset/connectors/legacy_sync.py": (
        '"""The connector this pull request removes."""\n\n'
        "def synchronise_everything_that_the_old_connector_touched(client):\n"
        "    return client\n"
    ),
    "docs/connectors.md": (
        "# Connectors\n\nThe legacy path is hyperset/connectors/legacy_sync.py, on its way out.\n"
    ),
}


def test_a_module_this_pull_request_deletes_still_reports_its_references(tmp_path):
    """A deleted file is in the diff and absent from the merged tree.

    Reading the probe from the merged tree gets an empty blob, which reads as
    blindness and refuses -- on the one case where a stale reference matters
    most, because the thing it names is now gone. The blob comes from main.
    """
    scenario = _scenario(
        tmp_path,
        base=DELETED,
        main_edit={"docs/connectors.md": DELETED["docs/connectors.md"] + LONG},
        pr_delete=["hyperset/connectors/legacy_sync.py"],
    )

    result = _run(scenario)

    assert result.returncode == 2, result.stderr
    assert "[DELETED by this PR]" in result.stdout
    assert "docs/connectors.md" in result.stdout


def test_the_deletion_path_is_not_exempt_from_the_control(tmp_path):
    """A pull request that only deletes ran with no control at all.

    The control does two jobs and only one of them is about the file in hand:
    the other is establishing that the grep can find anything. Skipping it
    whenever the file is absent from the merged tree -- which is every file in
    a deletion-only PR -- leaves a blind run reporting NO REFERENCE
    INTERACTION, and half one cannot notice, because a grep that finds nothing
    and a tree that contains no references produce the same empty set.

    The stub makes every search return "no matches", which is rc=1 and a
    perfectly ordinary answer. Only the control can tell the two apart.
    """
    scenario = _scenario(
        tmp_path,
        base=DELETED,
        main_edit={"docs/connectors.md": DELETED["docs/connectors.md"] + LONG},
        pr_delete=["hyperset/connectors/legacy_sync.py"],
    )
    assert _run(scenario).returncode == 2

    stub = _stub_git(tmp_path, 'if [ "$1" = "grep" ]; then exit 1; fi')
    result = _run(scenario, git_stub=stub)

    assert result.returncode == 1, result.stdout
    assert "control failed for hyperset/connectors/legacy_sync.py" in result.stderr
    assert "NO REFERENCE INTERACTION" not in result.stdout


@pytest.mark.parametrize("position", [0, 1, 2])
def test_a_well_formed_sha_no_repository_holds_is_refused(tmp_path, position):
    """The resolve guard, on all three arguments.

    `git rev-parse --verify -q` returns 0 and echoes this sha back, which is
    how a literal deadbeef walked through the original guard. Both facts are
    asserted here rather than described: the trap is live in this git, and it
    is live only at the full forty characters, so an arm written with a short
    fake would pass against the broken guard it exists to catch.
    """
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/names.md": NAMED["docs/names.md"] + LONG},
        pr_edit={"hyperset/evals/provenance.py": NAMED["hyperset/evals/provenance.py"] + "\n#\n"},
    )
    repo = scenario["repo"]

    assert len(ABSENT) == 40
    assert _git(repo, "cat-file", "-e", ABSENT, check=False).returncode != 0
    assert _git(repo, "rev-parse", "--verify", "-q", ABSENT, check=False).returncode == 0
    assert _git(repo, "rev-parse", "--verify", "-q", ABSENT_SHORT, check=False).returncode != 0

    args = [scenario["tree"], scenario["main"], scenario["mb"]]
    args[position] = ABSENT
    result = _run(scenario, tree=args[0], main=args[1], mb=args[2])

    assert result.returncode == 1
    assert f"REFUSE: cannot resolve {ABSENT}" in result.stderr


def test_a_tree_is_a_valid_first_argument(tmp_path):
    """The merger passes a merged TREE, and `rev-parse --verify <tree>^{commit}`
    refuses one: a guard peeled to a commit refuses every valid call while
    looking like a tightened check. The tree and the commit must agree."""
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/names.md": NAMED["docs/names.md"] + LONG},
        pr_edit={"hyperset/evals/provenance.py": NAMED["hyperset/evals/provenance.py"] + "\n#\n"},
    )
    tree = _git(scenario["repo"], "rev-parse", scenario["tree"] + "^{tree}").stdout.strip()
    assert (
        _git(
            scenario["repo"], "rev-parse", "--verify", "-q", tree + "^{commit}", check=False
        ).returncode
        != 0
    )

    from_commit = _run(scenario)
    from_tree = _run(scenario, tree=tree)

    assert from_commit.returncode == 2, from_commit.stderr
    assert from_tree.returncode == from_commit.returncode, from_tree.stderr
    assert "HOLD" in from_tree.stdout


def test_a_grep_whose_output_lost_its_prefix_refuses_rather_than_reporting_clean(tmp_path):
    """The shape assertion amendment 4 required.

    `git grep -l <pattern> <tree>` prefixes every line with "<tree>:", and this
    script strips it before comparing names. Under a git whose output does not
    carry it the strip is a no-op, every comparison fails, and the run prints
    NO REFERENCE INTERACTION -- a HOLD delivered as a clean answer, with
    nothing in the output to suggest anything went wrong. The stub here removes
    exactly that prefix and nothing else, and the same scenario HOLDs without it.
    """
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/names.md": NAMED["docs/names.md"] + LONG},
        pr_edit={"hyperset/evals/provenance.py": NAMED["hyperset/evals/provenance.py"] + "\n#\n"},
    )
    assert _run(scenario).returncode == 2

    stub = _stub_git(
        tmp_path,
        'if [ "$1" = "grep" ]; then\n'
        '  out=$("$REAL_GIT" "$@"); rc=$?\n'
        '  printf %s "$out" | sed "s|^[0-9a-f]*:||"\n'
        "  exit $rc\n"
        "fi",
    )
    result = _run(scenario, git_stub=stub)

    assert result.returncode == 1, result.stdout
    assert "does not carry" in result.stderr
    # The control's grep runs first, so it is the one that catches this; the
    # refusal prints the line it actually got, not the lookup that failed.
    assert "first line was: hyperset/evals/provenance.py" in result.stderr
    assert "NO REFERENCE INTERACTION" not in result.stdout


def test_a_grep_that_failed_outright_is_not_an_empty_match_set(tmp_path):
    """rc=1 from `git grep` means no matches and is an answer. Anything above
    it means the search did not happen, and an empty result then says only that
    nobody looked."""
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/names.md": NAMED["docs/names.md"] + LONG},
        pr_edit={"hyperset/evals/provenance.py": NAMED["hyperset/evals/provenance.py"] + "\n#\n"},
    )
    stub = _stub_git(tmp_path, 'if [ "$1" = "grep" ]; then exit 128; fi')

    result = _run(scenario, git_stub=stub)

    assert result.returncode == 1, result.stdout
    assert "git grep over" in result.stderr
    assert "rc=128" in result.stderr


def test_a_diff_with_no_files_in_it_is_never_a_clean_answer(tmp_path):
    """Nothing changed between the pair the caller passed means the caller
    passed the wrong pair, not that the merge is clean."""
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/names.md": NAMED["docs/names.md"] + LONG},
        pr_edit={"hyperset/evals/provenance.py": NAMED["hyperset/evals/provenance.py"] + "\n#\n"},
    )

    result = _run(scenario, main=scenario["tree"])

    assert result.returncode == 1
    assert "no changed files" in result.stderr


def test_main_at_the_merge_base_passes_with_no_modifications(tmp_path):
    """MB == MAIN is the SAFEST input, and it must PASS (hy-ezwm).

    A PR based on the current tip has `merge-base == main`, so main modified
    nothing there is anything to conflict with. The script used to REFUSE this
    -- the empty MAINMOD read as "suspect wrong merge-base" -- which vacuously
    blocked the merges most obviously safe to take, including this fix's own PR.
    This arm reds against that behavior and greens with the identity
    short-circuit; the arm below keeps a genuinely wrong base a REFUSE, so the
    fix is not "stop refusing empty diffs".
    """
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        main_edit={"docs/names.md": NAMED["docs/names.md"] + LONG},
        pr_edit={"hyperset/evals/provenance.py": NAMED["hyperset/evals/provenance.py"] + "\n#\n"},
    )

    result = _run(scenario, mb=scenario["main"])

    assert result.returncode == 0, result.stderr
    assert "main is at the merge base" in result.stdout
    assert "suspect wrong merge-base" not in result.stderr


def test_a_wrong_merge_base_that_moved_nothing_still_refuses(tmp_path):
    """The distinction the fix must keep: MB != MAIN with an empty diff.

    main advances by an EMPTY commit, so `git diff MB MAIN` is empty exactly as
    in the MB == MAIN case -- but MB is not main. That is a resolvable yet wrong
    merge base, and it must still REFUSE. Without this arm the fix reads as "an
    empty MAINMOD is always fine", which is the opposite of what it does.
    """
    # No main_edit, so main is at the merge base; the empty commit below is then
    # the ONLY thing separating them, giving MB != MAIN with an empty diff.
    scenario = _scenario(
        tmp_path,
        base=NAMED,
        pr_edit={"hyperset/evals/provenance.py": NAMED["hyperset/evals/provenance.py"] + "\n#\n"},
    )
    _git(scenario["repo"], "checkout", "-q", scenario["main"])
    _git(scenario["repo"], "commit", "-q", "--allow-empty", "-m", "empty advance")
    empty_main = _git(scenario["repo"], "rev-parse", "HEAD").stdout.strip()
    assert empty_main != scenario["mb"]
    assert (
        _git(scenario["repo"], "diff", "--name-only", scenario["mb"], empty_main).stdout.strip()
        == ""
    )

    result = _run(scenario, main=empty_main, mb=scenario["mb"])

    assert result.returncode == 1, result.stdout
    assert "main modified nothing since" in result.stderr


def _incomplete_checkout(root):
    """Why THIS checkout might not be able to answer, or an empty string.

    Same distinction the recording-pin check draws (hy-ow2v): a shallow or
    partial clone cannot see these merges, and "this checkout does not hold it"
    is a different claim from "these merges are gone" (hy-4dci). The refspec is
    deliberately not in here -- every seat fetches main, and all five of these
    are on it.
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


def test_the_five_merge_base_rating_still_reproduces():
    """The reason this was ported rather than rewritten.

    Five merges already on main, rated in the refinery's scratchpad before the
    port: #190 clean, #187 note, #189, #192 and #193 hold. A rewrite that
    passes every arm above and moves one of these has changed the instrument,
    and the base rating is the only thing here that would notice.

    Missing objects fail rather than skip. CI checks out full history for this
    reason already, and a skip is how a check like this goes quietly dead --
    but the message says which of "this checkout cannot" and "nobody can" it
    established, because only the first is ever measured here.
    """
    unresolved = [
        merge
        for merge, _, _ in BASE_RATING
        if _git(ROOT, "cat-file", "-e", merge, check=False).returncode != 0
    ]
    cannot = _incomplete_checkout(ROOT) if unresolved else ""
    assert not unresolved, (
        f"{cannot}, so it cannot rate these merges -- run `git fetch --unshallow origin`"
        if cannot
        else "these merges are not in this repository at all: " + ", ".join(unresolved)
    )

    rated = {}
    for merge, _, pull in BASE_RATING:
        first = _git(ROOT, "rev-parse", f"{merge}^1").stdout.strip()
        second = _git(ROOT, "rev-parse", f"{merge}^2").stdout.strip()
        base = _git(ROOT, "merge-base", first, second).stdout.strip()
        result = subprocess.run(
            ["bash", REFCHECK, merge, first, base], cwd=ROOT, capture_output=True, text=True
        )
        rated[pull] = result.returncode

    assert rated == {pull: expected for _, expected, pull in BASE_RATING}
