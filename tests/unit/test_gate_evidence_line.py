"""The named gate emits evidence, and the documents point at that command (hy-y91y).

Three agents reported 472, 463 and 439 tests at f02cd60 and every number was
recorded as "the gate". Two of them ran character-identical commands. What
was missing from all three records was the environment and the collection
count, so these tests hold the four things `scripts/gate.py` must put on one
line -- invocation, collected count, extras state, SHA -- and hold the
documents to naming the command that prints it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_GATE_SCRIPT = _ROOT / "scripts" / "gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("hyperset_gate_script", _GATE_SCRIPT)
    assert spec and spec.loader, _GATE_SCRIPT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_gate()


def test_the_line_carries_the_four_things_a_bare_pass_count_omitted():
    """Invocation, collected count, extras state and SHA, or it is not evidence.

    Each one is load-bearing for a specific record that went wrong: the path
    list separated refinery from capable, the collected count is what moved
    while everyone quoted passes, the extras state is what separated capable
    from critic under an identical command, and the SHA is the only thing
    that makes two lines comparable at all.
    """
    line = gate.evidence_line(
        sha="f02cd60" * 5 + "aaaaa",
        tree="clean",
        tree_id="b" * 40,
        extras="all",
        collected=475,
        uncollected_modules=0,
        counts={"passed": 472, "skipped": 2, "xfailed": 1},
        result=gate.PASS,
    )

    assert "\n" not in line, "the line must paste as one line"
    fields = gate.parse_evidence_line(line)
    assert fields["sha"] == "f02cd60" * 5 + "aaaaa"
    assert fields["collected"] == "475"
    assert fields["extras"] == "all"
    assert fields["cmd"] == gate.PYTEST_INVOCATION
    for path in gate.GATE_PATHS:
        assert path in fields["cmd"], f"the line must name the path list, missing {path}"
    assert fields["passed"] == "472"
    assert fields["result"] == gate.PASS


def _write_case(tmp_path: Path, stem: str) -> None:
    """The nine-test gap reproduced small: two tests behind a module-level guard.

    `stem` keeps the basenames unique across cases. Two nested `pytest.main`
    runs in one process that both collect a `test_present.py` outside a
    package hit an import-file-mismatch collection error, which looks like a
    failing gate and is not one.
    """
    (tmp_path / f"test_{stem}_present.py").write_text(
        "def test_one():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / f"test_{stem}_absent_extra.py").write_text(
        "import pytest\n\n"
        'pytest.importorskip("a_package_that_is_not_installed_anywhere")\n\n'
        "def test_two():\n    assert True\n\n"
        "def test_three():\n    assert True\n",
        encoding="utf-8",
    )


def test_the_recorder_separates_uncollected_modules_from_ordinary_skips(tmp_path):
    """Two tests vanish from collection and one skip entry stands for them."""
    _write_case(tmp_path, "recorder")
    recorder = gate._Recorder()

    pytest.main([str(tmp_path), "-q", "-p", "no:cacheprovider"], plugins=[recorder])

    assert recorder.collected == 1, "the guarded module's two tests are not collected"
    assert recorder.uncollected_modules == 1
    assert recorder.counts["skipped"] == 1, "one skip entry stands in for the whole module"
    assert recorder.counts["passed"] == 1


def test_the_gate_refuses_rather_than_measuring_without_the_extras(monkeypatch, capsys):
    """A count taken under silent uncollection is worse than no count.

    Refusing is the ruling on this bead: the gate stands on the extras-
    installed side, which is the side CI is permanently on. Running pytest
    anyway would print a smaller, comparable-LOOKING number, which is exactly
    how 439 and 472 both became "the gate".
    """
    monkeypatch.setattr(gate, "_installed", lambda name: False)

    def _must_not_run(*args, **kwargs):  # pragma: no cover - the assertion is that it is unused
        raise AssertionError("the gate ran pytest with an extra missing")

    monkeypatch.setattr(sys.modules["pytest"], "main", _must_not_run)

    exit_code = gate.main()

    assert exit_code == 2
    line = gate.parse_evidence_line(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["result"] == gate.NOT_A_GATE
    assert line["extras"].startswith("missing:")


def test_observed_uncollection_is_not_a_gate_even_when_every_test_passes(
    tmp_path, monkeypatch, capsys
):
    """The refusal must watch what happened, not what the metadata predicted.

    `_installed` asks whether a distribution's METADATA is present, which is a
    prediction: a broken or partial install keeps the dist-info, passes that
    check, and still fails to import, so the module drops out of collection
    and the count is short. Refusing on the predicted signal while accepting
    the observed one is backwards for a script whose thesis is that a count
    computed under silent uncollection is not the gate's number.
    """
    _write_case(tmp_path, "uncollected")
    monkeypatch.setattr(gate, "GATE_PATHS", (str(tmp_path),))

    exit_code = gate.main()

    line = gate.parse_evidence_line(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["uncollected_modules"] == "1", "the fixture drops one module from collection"
    assert line["failed"] == "0", "every collected test passed, which is the whole point"
    assert line["result"] == gate.NOT_A_GATE, "a short set is not a gate, however green it is"
    assert exit_code != 0, "a NOT-A-GATE line must not exit 0 beside a green CI badge"


def _write_pair(tmp_path: Path, stem: str) -> Path:
    """Two healthy modules, one test each, and the path of the second.

    Healthy on purpose: the narrowing this covers prints a SHORTER count with
    `result=PASS` and exit 0, so the fixture must have nothing else wrong with
    it or the refusal would fire for another reason.
    """
    (tmp_path / f"test_{stem}_first.py").write_text(
        "def test_one():\n    assert True\n", encoding="utf-8"
    )
    second = tmp_path / f"test_{stem}_second.py"
    second.write_text("def test_two():\n    assert True\n", encoding="utf-8")
    return second


def test_a_pytest_environment_override_cannot_shrink_the_gate_and_is_disclosed(
    tmp_path, monkeypatch, capsys
):
    """`cmd=` was a constant, so any narrowing made it false and the count short.

    Measured at 9505de1: `PYTEST_ADDOPTS="--ignore=$PWD/tests/unit/evals"`
    printed `collected=411` where the honest run printed 492, with `sha`,
    `tree`, `extras`, `cmd`, `uncollected_modules`, `result` and the exit code
    all identical -- and critic widened the class to any narrowing whose
    remainder is green, `-k <one test>` included (hy-vkh0). Clearing the inputs
    before `pytest.main` makes `cmd=` true by construction; printing what was
    cleared means the override is disclosed rather than erased.
    """
    second = _write_pair(tmp_path, "addopts")
    monkeypatch.setattr(gate, "GATE_PATHS", (str(tmp_path),))
    monkeypatch.setenv("PYTEST_ADDOPTS", f"--ignore={second}")

    exit_code = gate.main()

    line = gate.parse_evidence_line(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["collected"] == "2", "the ignore must not reach pytest"
    assert line["passed"] == "2"
    assert line["result"] == gate.PASS
    assert exit_code == 0
    assert line["env_cleared"] == f"PYTEST_ADDOPTS=--ignore={second}", (
        "the override that was neutralised must be readable on the line"
    )
    assert os.environ["PYTEST_ADDOPTS"] == f"--ignore={second}", (
        "restored: the gate neutralises pytest's inputs for its own run, and callers "
        "-- this suite among them -- keep the environment they set"
    )


def test_the_line_says_so_when_no_pytest_environment_input_was_set(tmp_path, monkeypatch, capsys):
    """The honest run needs a positive statement, not a missing field.

    An absent field reads as an older line; `env_cleared=none` is the claim
    that nothing was there to clear.
    """
    _write_pair(tmp_path, "clean_env")
    monkeypatch.setattr(gate, "GATE_PATHS", (str(tmp_path),))
    for name in gate.PYTEST_ENVIRONMENT_INPUTS:
        monkeypatch.delenv(name, raising=False)

    gate.main()

    line = gate.parse_evidence_line(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["env_cleared"] == gate.NO_ENVIRONMENT_INPUT
    assert line["collected"] == "2"


def test_only_the_inputs_that_can_change_which_tests_run_are_cleared():
    """The list is a decision, so it is asserted rather than described.

    `PYTEST_CURRENT_TEST` is pytest's OUTPUT -- it writes it during the run --
    and clearing it would corrupt the reporting of any run this is nested
    inside. `PYTEST_DEBUG`, `PYTEST_THEME` and `PYTEST_DEBUG_TEMPROOT` change
    tracing, colour and the temp root, none of which changes the set.
    """
    environ = {
        "PYTEST_ADDOPTS": "-k test_one",
        "PYTEST_PLUGINS": "some_plugin",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_CURRENT_TEST": "tests/unit/test_x.py::test_y (call)",
        "PYTEST_DEBUG": "1",
        "PATH": "/usr/bin",
    }

    cleared = gate.strip_pytest_environment(environ)

    assert cleared == {
        "PYTEST_ADDOPTS": "-k test_one",
        "PYTEST_PLUGINS": "some_plugin",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    assert environ == {
        "PYTEST_CURRENT_TEST": "tests/unit/test_x.py::test_y (call)",
        "PYTEST_DEBUG": "1",
        "PATH": "/usr/bin",
    }


def test_the_disclosure_is_one_parseable_token_whatever_the_value_holds():
    """Two properties of the field, at the function that builds it.

    An override's value is arbitrary text arriving from outside, and it is
    echoed inside a quoted field. A quote in it made `parse_evidence_line`
    refuse the whole line ("unparseable field 'or'"); a NEWLINE in it -- a YAML
    block scalar's trailing one is the realistic route -- split the evidence
    line in two, and the fragment carrying `collected` and `result` had no
    prefix and no `sha`. A bare count with no provenance is the exact defect
    this script's first paragraph is about, so the line's integrity comes first
    and the value is escaped rather than the run refused: the count itself was
    never wrong.
    """
    rendered = gate.render_cleared_environment({"PYTEST_ADDOPTS": '-k "a or b"\ntrailing'})

    assert "\n" not in rendered, "a newline in the value must not survive into the field"

    line = gate.evidence_line(
        sha="0" * 40,
        tree="clean",
        tree_id="b" * 40,
        extras="all",
        collected=7,
        uncollected_modules=0,
        counts={"passed": 7},
        result=gate.PASS,
        env_cleared=rendered,
    )

    assert len(line.splitlines()) == 1, "one line, or the count loses its provenance"
    assert '\\"' in line, "the emitted field escapes the quote rather than ending early"
    fields = gate.parse_evidence_line(line)
    assert fields["env_cleared"] == 'PYTEST_ADDOPTS=-k "a or b"\\ntrailing', (
        "POSIX shlex unescapes the quote, so the field round-trips to the value that was set; "
        "the newline stays as two visible characters, which is the point -- restoring it would "
        "re-split the line"
    )
    assert fields["collected"] == "7"
    assert fields["result"] == gate.PASS


def test_an_override_holding_a_quote_and_a_newline_does_not_break_the_line(
    tmp_path, monkeypatch, capsys
):
    """The same two properties through the real `main`, with pytest actually run.

    `-k "a or b"` is the readable case and the trailing newline is the one that
    cost the line its prefix. Both counts were already correct before this fix;
    what was broken was every reader's ability to parse or attribute them.
    """
    _write_pair(tmp_path, "quoted")
    monkeypatch.setattr(gate, "GATE_PATHS", (str(tmp_path),))
    monkeypatch.setenv("PYTEST_ADDOPTS", '-k "a or b"\n')

    exit_code = gate.main()

    printed = [
        candidate
        for candidate in capsys.readouterr().out.splitlines()
        if candidate.startswith(gate.LINE_PREFIX)
    ]
    assert len(printed) == 1, "exactly one line carries the prefix"
    fields = gate.parse_evidence_line(printed[0])
    assert fields["sha"], "the line that carries the count must carry its provenance"
    assert fields["collected"] == "2", "and the override still must not reach pytest"
    assert fields["result"] == gate.PASS
    assert exit_code == 0
    assert fields["env_cleared"] == 'PYTEST_ADDOPTS=-k "a or b"\\n', (
        "the quote comes back through shlex; the newline stays escaped so it cannot split again"
    )
    assert os.environ["PYTEST_ADDOPTS"] == '-k "a or b"\n', "restored exactly as it was set"


def test_the_refusal_line_does_not_claim_nothing_was_set(monkeypatch, capsys):
    """`env_cleared=none` is a positive claim, so the refusal path must not lie.

    The missing-extras path returns before pytest is reached, and it printed
    `none` while an override was sitting in the environment. Clearing the inputs
    BEFORE that check removes the special case rather than adding a word for it:
    one code path, one meaning for the field.
    """
    monkeypatch.setattr(gate, "_installed", lambda name: False)
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k nothing_at_all")

    def _must_not_run(*args, **kwargs):  # pragma: no cover - the assertion is that it is unused
        raise AssertionError("the gate ran pytest with an extra missing")

    monkeypatch.setattr(sys.modules["pytest"], "main", _must_not_run)

    exit_code = gate.main()

    assert exit_code == 2
    line = gate.parse_evidence_line(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["result"] == gate.NOT_A_GATE
    assert line["extras"].startswith("missing:")
    assert line["env_cleared"] == "PYTEST_ADDOPTS=-k nothing_at_all"
    assert os.environ["PYTEST_ADDOPTS"] == "-k nothing_at_all", "restored on this path too"


def test_a_collection_error_is_an_error_and_never_an_uncollected_module(
    tmp_path, monkeypatch, capsys
):
    """What both documents claimed about this member, measured (hy-j4m4).

    `scripts/gate.py` and `AGENTS.md` both listed a collection error among the
    things `uncollected_modules` observes, each contributing "one skip entry"
    and making the run `NOT-A-GATE`. It does not:
    `_Recorder.pytest_collectreport` increments only when `report.skipped`, and
    a collection error is a `CollectReport` whose outcome is FAILED, so it lands
    in `stats["error"]`. pytest then interrupts collection and nothing runs.

    This pins the behaviour the corrected sentences now describe; the code was
    always right and the prose was not, so this test is green at the commit
    that made the claim and the SENTENCE is what moved.
    """
    (tmp_path / "test_collect_healthy.py").write_text(
        "def test_one():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "test_collect_broken.py").write_text(
        "import a_module_that_is_not_installed_anywhere\n\ndef test_two():\n    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "GATE_PATHS", (str(tmp_path),))

    exit_code = gate.main()

    line = gate.parse_evidence_line(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["errors"] == "1"
    assert line["uncollected_modules"] == "0", "no skipped CollectReport, so the field is blind"
    assert line["skipped"] == "0", "there is no skip entry standing in for the module"
    assert line["passed"] == "0", "collection is interrupted, so the healthy test never runs"
    assert line["result"] == gate.FAIL, "a mislabelled RED, which is why this was filed not bounced"
    assert exit_code != 0


def _repository(tmp_path: Path) -> Path:
    """A real git repository, because the subject is what git computes."""
    root = tmp_path / "repo"
    root.mkdir()
    for command in (
        ("init", "-q"),
        ("config", "user.email", "gate@example.test"),
        ("config", "user.name", "Gate"),
    ):
        subprocess.run(["git", *command], cwd=root, check=True, capture_output=True)
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=root, check=True, capture_output=True)
    return root


def _git_in(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


def _porcelain(root: Path) -> str:
    """`git status --porcelain` with its own whitespace, which is the subject.

    Not `_git_in`: that strips, and the first column of a porcelain line is the
    INDEX state, so stripping turns the unstaged ` M` this test is about into a
    staged `M ` and the assertion could no longer tell them apart.
    """
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def test_the_tree_id_of_a_clean_checkout_is_the_commits_own_tree(tmp_path):
    """The field has to mean the same thing on both sides of a merge, so on a
    clean tree it is not a second opinion: it is what `HEAD^{tree}` already
    says, and a reader can check it with one git command."""
    root = _repository(tmp_path)

    assert gate.working_tree_id(root) == _git_in(root, "rev-parse", "HEAD^{tree}")


def test_a_tree_no_commit_names_is_still_identified_and_two_of_them_differ(tmp_path):
    """hy-3esn, refinery's case. Its merge procedure gates a no-ff merge that
    has not been committed, so `sha=` names the BASE and `tree=dirty` only
    flags that the measured tree is not it: two runs at one base print the same
    `sha` for two different trees, and the tree that actually lands is the
    unnamed one.

    A content id answers it without the gate having to know it is inside a
    merge, and it answers the second failure mode too (base superseded before
    the merge landed): the post-merge line's `tree_id` equals the trial line's
    exactly when the tree that landed is the tree that was measured, whatever
    happened to `sha` in between.
    """
    root = _repository(tmp_path)
    committed_tree = gate.working_tree_id(root)

    (root / "tracked.txt").write_text("two\n", encoding="utf-8")
    first_dirty = gate.working_tree_id(root)
    (root / "untracked.txt").write_text("three\n", encoding="utf-8")
    second_dirty = gate.working_tree_id(root)

    assert _git_in(root, "rev-parse", "HEAD^{tree}") == committed_tree, "the commit did not move"
    assert len({committed_tree, first_dirty, second_dirty}) == 3, (
        "three different trees at one commit must carry three different ids"
    )
    assert gate.working_tree_id(root) == second_dirty, "the same tree twice is the same id"


def test_identifying_the_tree_does_not_stage_anything(tmp_path):
    """It is computed in a temporary index, so a gate run cannot turn into a
    commit of whatever was lying around. An agent runs this mid-change, and a
    measurement that edited the index would be a worse defect than the one
    being fixed."""
    root = _repository(tmp_path)
    (root / "tracked.txt").write_text("two\n", encoding="utf-8")
    (root / "untracked.txt").write_text("three\n", encoding="utf-8")
    # `git status` first, then the snapshot: status REFRESHES the index's stat
    # cache and writes it back, so a byte comparison taken before that refresh
    # fails on the reader rather than on the subject.
    before = _porcelain(root)
    index = (root / ".git" / "index").read_bytes()

    gate.working_tree_id(root)

    assert (root / ".git" / "index").read_bytes() == index, "the real index was written"
    assert before == " M tracked.txt\n?? untracked.txt\n", "the fixture is the state it claims"
    assert _porcelain(root) == before, (
        "the modification must still be unstaged and the new file still untracked"
    )


def test_a_tracked_file_git_is_told_to_ignore_is_still_part_of_the_tree(tmp_path):
    """The temporary index is seeded before anything is added.

    Measured on this repository with an EMPTY temporary index: `git add -A`
    applies `.gitignore` to files it has never seen, so `CLAUDE.md` and
    `.claude/settings.json` -- both tracked, both matched by `.gitignore` --
    dropped out and the id disagreed with `HEAD^{tree}` on a clean checkout.
    Seeding first makes the id cover what the repository actually contains.

    This case commits the ignored file BEFORE measuring, so it only ever
    exercises a path already tracked in `HEAD` -- which is why it passed while
    `HEAD`-seeding still dropped an ignored path a merge INTRODUCED.
    `test_an_ignored_path_the_merge_introduces_is_part_of_the_tree_that_lands`
    is the half this one cannot see (hy-hm6h).
    """
    root = _repository(tmp_path)
    (root / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    (root / "ignored.md").write_text("tracked anyway\n", encoding="utf-8")
    subprocess.run(["git", "add", "-Af"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "second"], cwd=root, check=True, capture_output=True)

    assert gate.working_tree_id(root) == _git_in(root, "rev-parse", "HEAD^{tree}")

    (root / "ignored.md").write_text("edited\n", encoding="utf-8")

    assert gate.working_tree_id(root) != _git_in(root, "rev-parse", "HEAD^{tree}"), (
        "an edit to a tracked file must move the id whatever .gitignore says about it"
    )


def _blob_id(content: str) -> str:
    """git's object id for `content`, computed without asking git (hy-ooiv).

    The oracle has to be independent of the plumbing under test: asking
    `git hash-object` would route the expected value through the same stat cache
    and index machinery whose staleness is the subject.
    """
    data = content.encode("utf-8")
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324 - git's format


def _committed_with_a_backdated_stat(root: Path, name: str, content: str) -> None:
    """Commit `name` so the index caches an mtime in a strictly earlier second.

    Backdating is what makes the window CONSTRUCTED instead of raced into. The
    defect only shows when the fresh copy's mtime lands in a LATER second than
    the cached entry -- the refinery saw one call re-hash and the next return the
    stale blob for that reason alone -- so the entry's cached second is pushed a
    minute into the past and every subsequent copy is guaranteed to be later.
    Without this, a first draft of these tests passed or failed on where the
    second boundary happened to fall.

    `core.trustctime=false` removes the LAST timing variable, and it is the only
    knob here. git compares a cached ctime it cannot be made to forget: rewriting
    the file moves ctime to now, and whether that still matches the cached value
    depends on which side of a second boundary the rewrite lands -- measured at
    9 failures in 12 runs before this line, which is a test reporting a real
    defect as noise. Turning it off leaves the fields this fixture CAN control
    and makes the window a construction. It is not what makes the defect real:
    the refinery reproduced it on an ordinary repository with default config, on
    two consecutive calls.
    """
    _git_in(root, "config", "core.trustctime", "false")
    target = root / name
    target.write_text(content, encoding="utf-8")
    backdated = time.time() - 60
    os.utime(target, (backdated, backdated))
    _git_in(root, "add", name)
    _git_in(root, "commit", "-qm", f"backdated {name}")


def _rewrite_inside_the_racy_window(root: Path, name: str, content: str) -> None:
    """Rewrite `name` with same-size `content` and hand git back the stat it cached.

    git re-hashes an entry only when its cached mtime is at least the index
    file's own mtime -- its racy-clean rule, and its own protection against a
    write landing in the same filesystem-timestamp second as the last index
    update. So the window is built by hand: the new bytes are the same SIZE, the
    file's mtime is restored to the value the index cached, and the index file is
    stamped with that same mtime. Every entry is then racy, so a correct reader
    must re-hash, and a reader that trusts the stat cache returns the committed
    blob for a file that no longer holds it.

    Same-size is the load-bearing half and it is ordinary, not exotic: swapping
    one 40-character SHA for another in a doc, a bead reference or a fixture is
    byte-for-byte the same length.

    Nothing here calls `git status` -- a status can refresh and REWRITE the real
    index, which would move the mtime this window is built on.
    """
    target = root / name
    cached = target.stat()
    assert len(content.encode("utf-8")) == cached.st_size, "same SIZE is half the precondition"
    target.write_text(content, encoding="utf-8")
    stamp = (cached.st_atime_ns, cached.st_mtime_ns)
    os.utime(target, ns=stamp)
    index = Path(_git_in(root, "rev-parse", "--path-format=absolute", "--git-path", "index"))
    os.utime(index, ns=stamp)


def test_a_same_second_same_size_edit_is_not_invisible_to_the_seeded_index(tmp_path):
    """The seed must preserve the index's mtime, or git stops re-hashing (hy-ooiv).

    `shutil.copyfile` gave the temporary index a FRESH mtime, which made every
    cached entry strictly older than the index and therefore definitively clean
    in git's eyes. The stat cache was then trusted, and for a same-second
    same-size rewrite the cached stat is indistinguishable from the current one,
    so the COMMITTED blob went into the tree for a file that no longer held it.
    Measured by the refinery outside pytest: two consecutive calls on one
    unchanged working tree returned 805cf4d3 and a4a62f8c, the second carrying
    `one\\n` for a `tracked.txt` holding `two\\n`.

    The oracle is `_blob_id`, computed in Python, so the expected value does not
    come from the machinery under test.
    """
    root = _repository(tmp_path)
    _committed_with_a_backdated_stat(root, "racy.txt", "one\n")
    _rewrite_inside_the_racy_window(root, "racy.txt", "two\n")

    measured = gate.working_tree_id(root)

    assert _git_in(root, "rev-parse", f"{measured}:racy.txt") == _blob_id("two\n"), (
        "the tree must carry the bytes on disk, not the ones the stat cache remembers"
    )


def test_two_different_same_size_edits_in_one_second_do_not_share_one_id(tmp_path):
    """hy-3esn's own sentence, resurfacing through the seed copy instead of `sha`.

    The consequence that is worse than the flake: one `tree_id` naming two
    different trees, which is exactly what the field was introduced to prevent.
    Two same-size contents inside one constructed racy window, and the ids have
    to differ -- with each id also checked against the content it should name,
    because two ids differing proves nothing about either being right.
    """
    root = _repository(tmp_path)
    _committed_with_a_backdated_stat(root, "racy.txt", "one\n")
    measured = {}
    for content in ("two\n", "six\n"):
        _rewrite_inside_the_racy_window(root, "racy.txt", content)
        measured[content] = gate.working_tree_id(root)

    assert measured["two\n"] != measured["six\n"], (
        "two different trees must not share one id, whatever the stat cache says"
    )
    for content, identifier in measured.items():
        assert _git_in(root, "rev-parse", f"{identifier}:racy.txt") == _blob_id(content), (
            f"and each id must name the content it was measured over, {content!r}"
        )


def _branch_with(root: Path, name: str, *files: tuple[str, str]) -> None:
    """A branch off the current HEAD carrying `files`, force-added past .gitignore."""
    _git_in(root, "checkout", "-q", "-b", name)
    for path, content in files:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _git_in(root, "add", "-f", path)
    _git_in(root, "commit", "-qm", name)
    _git_in(root, "checkout", "-q", "-")


def test_an_ignored_path_the_merge_introduces_is_part_of_the_tree_that_lands(tmp_path):
    """hy-hm6h: the trap one commit further along, and the reason the seed is a COPY.

    Seeding the temporary index from `HEAD` covers ignored paths ALREADY
    tracked in HEAD and no others, so a path `.gitignore` matches that the
    MERGE introduces is untracked to `add -A` and drops out again. Critic's
    reproduction, rebuilt here: two branches whose non-ignored content is
    identical, one of which also tracks `.claude/settings.json` under a
    `.gitignore` holding `.claude/`. Two trial merges that land DIFFERENT trees
    printed one `sha` and one `tree_id` -- hy-3esn's own complaint, reproduced
    inside its fix.

    Not hypothetical in this repository: `.gitignore` holds `.claude/` and
    `CLAUDE.md`, and commit 1591be7 added six files under those patterns at
    once.

    The assertion is against `git write-tree` on the REAL index, which during
    an uncommitted merge is the tree that would land: comparing the two
    `tree_id`s to each other alone would pass on any function that returned two
    different values.
    """
    root = _repository(tmp_path)
    (root / ".gitignore").write_text(".claude/\n", encoding="utf-8")
    _git_in(root, "add", "-A")
    _git_in(root, "commit", "-qm", "ignore .claude")
    _branch_with(root, "plain", ("shared.txt", "shared\n"))
    _branch_with(root, "with-claude", ("shared.txt", "shared\n"), (".claude/settings.json", "{}\n"))

    landed = {}
    for branch in ("plain", "with-claude"):
        _git_in(root, "merge", "--no-ff", "--no-commit", "-q", branch)
        landed[branch] = (gate.working_tree_id(root), _git_in(root, "write-tree"))
        _git_in(root, "merge", "--abort")

    for branch, (measured, would_land) in landed.items():
        assert measured == would_land, (
            f"{branch}: the id must name the tree the merge would land, ignored paths included"
        )
    assert landed["plain"][1] != landed["with-claude"][1], (
        "the fixture is the state it claims: two trial merges landing two different trees"
    )
    assert landed["plain"][0] != landed["with-claude"][0], (
        "so two trial merges that land different trees must not print one id"
    )


def test_a_conflicted_merge_names_no_tree_rather_than_one_holding_conflict_markers(tmp_path):
    """The promise AGENTS.md and the docstring both made, never once exercised.

    `unmerged`, `conflict` and `unknown` appeared nowhere in this file, and the
    stated mechanism -- unmerged entries make `write-tree` fail -- cannot hold:
    the seed copies the unmerged entries in and then `git add -A` stages the
    marker-bearing working-tree file at stage 0, resolving the conflict inside
    the temporary index. At 316b940 this printed 8de0d87d, a real tree whose
    conflicted file holds `<<<<<<< HEAD`, while `write-tree` on the real index
    exited 128. An id nobody can land, printed with no hedge, which is the
    failure class this field exists to close.

    Asserted against the REAL index, not against the function's other output:
    the fixture is only a conflict if git itself reports unmerged entries and
    refuses to write a tree.
    """
    root = _repository(tmp_path)
    _branch_with(root, "mine", ("conflicted.txt", "mine\n"))
    _branch_with(root, "other", ("conflicted.txt", "other\n"))
    _git_in(root, "merge", "-q", "mine")

    conflicted = subprocess.run(["git", "merge", "other"], cwd=root, capture_output=True, text=True)
    assert conflicted.returncode != 0, "the fixture has to be a real conflict"
    assert _git_in(root, "ls-files", "-u"), "git's own report of unmerged entries"
    refused = subprocess.run(["git", "write-tree"], cwd=root, capture_output=True, text=True)
    assert refused.returncode != 0, "the real index has no tree to write"

    measured = gate.working_tree_id(root)

    named = (
        _git_in(root, "cat-file", "-p", f"{measured}:conflicted.txt")
        if measured != gate.UNKNOWN
        else ""
    )
    assert measured == gate.UNKNOWN, (
        f"a conflicted merge has no tree to land, but the id named one holding {named!r}"
    )


def _two_branches_that_conflict(root: Path) -> None:
    """`mine` merged, `other` waiting: the next merge conflicts on `conflicted.txt`."""
    _branch_with(root, "mine", ("conflicted.txt", "mine\n"))
    _branch_with(root, "other", ("conflicted.txt", "other\n"))
    _git_in(root, "merge", "-q", "mine")


def _conflicting_merge(root: Path, **environment: str) -> None:
    """Merge `other` -- under `environment` if given -- and assert it conflicted."""
    merged = subprocess.run(
        ["git", "merge", "other"],
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, **environment} if environment else None,
    )
    assert merged.returncode != 0, "the fixture has to be a real conflict"


def _conflict(root: Path) -> None:
    """A repository mid-conflict on `conflicted.txt` in its own index."""
    _two_branches_that_conflict(root)
    _conflicting_merge(root)


def test_a_conflict_resolved_on_disk_but_not_staged_still_names_no_tree(tmp_path):
    """This seat's own order: resolve the conflict, gate, and only then `git add`.

    Behaviour was already correct at 7914731 -- the critic measured it rather
    than leaving the gap unmeasured, and this writes the fact down. It is the
    case a reader is most likely to hit, and the one where the marker text has
    already left the working tree, so nothing about the FILES says conflict any
    more. What still says it is the index, which is what the refusal reads.
    """
    root = _repository(tmp_path)
    _conflict(root)
    (root / "conflicted.txt").write_text("resolved\n", encoding="utf-8")

    assert "<<<<<<<" not in (root / "conflicted.txt").read_text(encoding="utf-8"), (
        "the fixture is the interesting one: no marker left on disk"
    )
    assert _git_in(root, "ls-files", "-u"), "git still reports the entries unmerged"
    refused = subprocess.run(["git", "write-tree"], cwd=root, capture_output=True, text=True)
    assert refused.returncode != 0, "so there is still no tree to write"

    assert gate.working_tree_id(root) == gate.UNKNOWN, (
        "a resolution git has not been told about is not a tree that lands"
    )


def test_the_refusal_follows_the_index_in_force_not_the_one_on_disk(tmp_path, monkeypatch):
    """`GIT_INDEX_FILE` moves which index is unmerged, and the verdict moves with it.

    The first round's prescription was to assemble `.git/index` by hand; this
    locates it with `git rev-parse --git-path index`, which HONOURS
    `GIT_INDEX_FILE`. That was challenged as a risk and measured to be the
    reverse -- a hand-built path would have named an index nobody was using.
    `ls-files -u` reads the same variable, so the refusal and the seed cannot
    disagree about which index they are talking about, and this pins that in
    both directions rather than in the one that happens to be convenient.

    git's own `ls-files -u` is the oracle for which index is unmerged; the
    assertion is that the verdict tracks it, not that two calls of this function
    differ.
    """
    for direction, conflict_in_alt in (
        ("alt index unmerged", True),
        ("real index unmerged", False),
    ):
        home = tmp_path / direction.replace(" ", "-")
        home.mkdir()
        root = _repository(home)
        alt = home / "alt-index"
        _two_branches_that_conflict(root)
        if conflict_in_alt:
            # The merge needs an index matching HEAD, so the alt starts as a copy
            # of the real one and only the conflicting merge runs against it.
            shutil.copyfile(Path(_git_in(root, "rev-parse", "--absolute-git-dir")) / "index", alt)
            _conflicting_merge(root, GIT_INDEX_FILE=str(alt))
        else:
            _conflicting_merge(root)
            subprocess.run(
                ["git", "read-tree", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                env={**os.environ, "GIT_INDEX_FILE": str(alt)},
            )

        with monkeypatch.context() as patched:
            patched.setenv("GIT_INDEX_FILE", str(alt))
            overridden = (_git_in(root, "ls-files", "-u"), gate.working_tree_id(root))
        plain = (_git_in(root, "ls-files", "-u"), gate.working_tree_id(root))

        unmerged, verdict = overridden if conflict_in_alt else plain
        clean, named = plain if conflict_in_alt else overridden
        assert unmerged, f"{direction}: the fixture puts the conflict where it claims"
        assert not clean, f"{direction}: and leaves the other index merged"
        assert verdict == gate.UNKNOWN, f"{direction}: the unmerged index is the one that refuses"
        assert named != gate.UNKNOWN and len(named) == 40, (
            f"{direction}: and the merged one still names a tree"
        )


def test_the_line_carries_the_id_of_the_tree_the_run_measured(tmp_path, monkeypatch, capsys):
    """The whole point is that it is on the LINE, next to the counts."""
    _write_pair(tmp_path, "tree_id")
    monkeypatch.setattr(gate, "GATE_PATHS", (str(tmp_path),))

    gate.main()

    line = gate.parse_evidence_line(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["tree_id"] == gate.working_tree_id(_ROOT)
    assert len(line["tree_id"]) == 40, "a git object id, so a reader can resolve it"


def test_an_input_the_repository_reads_is_disclosed_and_left_alone(tmp_path, monkeypatch, capsys):
    """hy-ia5h, critic's finding at 14c036c. Two tests in `tests/unit/planner`
    skip when `CI` is unset, so one SHA and one tree printed
    `passed=523 skipped=2` on a laptop and `passed=525 skipped=0` on CI with
    every other field identical -- a difference nothing on the line explained.

    `CI` is NOT cleared, and that is the decision: those two tests are
    backstops against a CI that synced without `--all-extras`, so clearing it
    would silence them in the one place they are meant to fire. Neutralising an
    input is right when the input narrows the set and wrong when the input is
    what the environment legitimately IS. So it is disclosed instead, which is
    what makes the two lines tell themselves apart.
    """
    _write_pair(tmp_path, "repository_input")
    monkeypatch.setattr(gate, "GATE_PATHS", (str(tmp_path),))
    monkeypatch.setenv("CI", "true")

    gate.main()

    line = gate.parse_evidence_line(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["env_observed"] == "CI=true"
    assert os.environ["CI"] == "true", "disclosed, never cleared"
    assert "CI" not in gate.PYTEST_ENVIRONMENT_INPUTS, (
        "clearing CI would make the two planner backstops skip on CI, which is where they fire"
    )


def test_the_two_lines_the_same_tree_prints_on_ci_and_off_it_are_distinguishable():
    """The disclosure is only worth a field if it separates the pair that
    caused it: same sha, same tree_id, same extras, same cmd, same collected,
    and `passed`/`skipped` moving by two with nothing to attribute it to."""
    on_ci = gate.render_observed_environment({"CI": "true", "PATH": "/usr/bin"})
    off_ci = gate.render_observed_environment({"PATH": "/usr/bin"})

    assert on_ci == "CI=true"
    assert off_ci == gate.NO_ENVIRONMENT_INPUT
    assert on_ci != off_ci


def test_a_line_an_earlier_version_of_this_script_printed_still_parses():
    """The version moved because the shape did, and the recorded evidence did
    not move with it. A parser that refused every line already sitting in a
    bead would make the bump cost more than the fields are worth."""
    earlier = (
        f"HYPERSET-GATE v1 sha={'0' * 40} tree=clean extras=all "
        f'cmd="{gate.PYTEST_INVOCATION}" env_cleared="none" '
        "collected=526 uncollected_modules=0 passed=523 failed=0 errors=0 skipped=2 "
        "xfailed=1 xpassed=0 result=PASS"
    )

    fields = gate.parse_evidence_line(earlier)

    assert fields["line_version"] == "v1"
    assert fields["passed"] == "523"
    assert "tree_id" not in fields, "an older line cannot answer what it never printed"
    assert (
        gate.parse_evidence_line(
            gate.evidence_line(
                sha="0" * 40,
                tree="clean",
                tree_id="1" * 40,
                extras="all",
                collected=0,
                uncollected_modules=0,
                counts={},
                result=gate.PASS,
            )
        )["line_version"]
        == gate.LINE_PREFIX.split()[-1]
    )


def _documented_lines(document: str) -> list[str]:
    """Every full evidence line shown in a document, prefix-matched as pasted."""
    text = (_ROOT / document).read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip().startswith(gate.LINE_PREFIX)]


# `AGENTS.md` is the document that shows the line; `CLAUDE.md` names the
# command and the prefix in prose and contributes ZERO cases today, so the only
# id below is `AGENTS.md-0`. It is scanned so that a line pasted into it later
# is checked without anyone remembering to add it, and it is not coverage now.
_DOCUMENTED_LINES = [
    pytest.param(line, id=f"{document}-{index}")
    for document in ("AGENTS.md", "CLAUDE.md")
    for index, line in enumerate(_documented_lines(document))
]


def test_a_document_shows_the_whole_line_agents_are_told_to_paste():
    """Without this the parametrised check below would pass on an empty set."""
    assert _documented_lines("AGENTS.md"), "AGENTS.md shows no HYPERSET-GATE line to parse"


@pytest.mark.parametrize("document_line", _DOCUMENTED_LINES)
def test_the_documented_example_line_parses_and_carries_the_fields_emitted_today(document_line):
    """A field renamed in the script leaves the documents printing a dead format.

    The document tests below assert only that each document contains the
    command and the prefix, so renaming an emitted field is invisible to them.
    Parsing the example and comparing key sequences is what makes the documents
    red when the line's shape moves. The comparison is ORDERED because
    `evidence_line` claims a fixed order so that two pasted lines diff
    readably; a sorted comparison would let the emitted order be reversed with
    nothing red, which is a claim in the code that nothing holds.
    """
    emitted = gate.parse_evidence_line(
        gate.evidence_line(
            sha="0" * 40,
            tree="clean",
            tree_id="b" * 40,
            extras="all",
            collected=0,
            uncollected_modules=0,
            counts={},
            result=gate.PASS,
        )
    )

    documented = gate.parse_evidence_line(document_line)

    assert list(documented) == list(emitted), (
        f"documented fields {list(documented)} do not match emitted {list(emitted)}"
    )


def test_every_declared_extra_is_behind_the_gate():
    """Read from pyproject, so a new extra is gated without anyone remembering.

    The two that caused this -- `inspect-ai` and `claude-agent-sdk` -- are the
    ones with module-level guards today, and naming them here is a check that
    the parser reads real requirement strings rather than a proof that the
    list is closed.
    """
    declared = gate.extra_distributions((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert "inspect-ai" in declared
    assert "claude-agent-sdk" in declared
    assert "openai-agents" in declared


@pytest.mark.parametrize("document", ["AGENTS.md", "CLAUDE.md"])
def test_the_documents_name_the_gate_command_this_script_defines(document):
    """A convention nobody can find in the document decays back to three numbers.

    Asserted against the constants rather than a copied string: renaming the
    command in `scripts/gate.py` without updating both documents is red here.
    """
    text = (_ROOT / document).read_text(encoding="utf-8")

    assert gate.GATE_COMMAND in text, f"{document} does not name the gate command"
    assert gate.LINE_PREFIX in text, f"{document} does not show the evidence line it prints"
