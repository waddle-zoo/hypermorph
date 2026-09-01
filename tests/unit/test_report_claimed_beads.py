"""The merge-time report, driven through real repositories and a real merge.

hy-p4ji: merging a branch closes no bead. `gt mq post-merge` is the only thing
in the town that does, and it needs an MR wisp -- so a merge pressed with an
empty queue closes nothing and says nothing.

The Mayor ruled REPORT, never close, never reopen, so the arm that matters most
here is the NEGATIVE one: `test_it_never_changes_a_status` runs every shape
through `--apply` and asserts no bead moved. A suite that only checked the
comments would pass against a script that also closed things.

Two signals are read -- the `Completes-Bead:` body trailer and the parenthesised
group ending a commit subject -- because measured on origin/main at 58270709
only 28 of 501 commits carry a body trailer, and the merge that created the live
hy-xjch drift (#212) carried none. Trailer fixtures use the ORPHANED shape (the
`Completes-Bead:` line as its own paragraph above `Co-Authored-By:`) because
that is the majority shape in this history and the one git's own trailer parser
cannot see.
"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = str(ROOT / "scripts" / "report_claimed_beads.py")
SYNC_SCRIPT = str(ROOT / "scripts" / "gh-to-gastown-sync.sh")

EXIT_OK = 0
EXIT_REFUSE = 2
EXIT_REPORT = 3

# Well-formed and held by nobody, so the existence arm is reached rather than
# short-circuited by a malformed rev. A SHORT absent hex refuses correctly for
# the wrong reason, which is how a guard tested with `deadbeef` looks sound.
UNHELD = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

# The sentence the Mayor made load-bearing: without it the comment reads as an
# accusation of a missing close, and someone helpfully closes the bead.
LOAD_BEARING = "a trailer is a claim, not a completion"

ORPHANED = """{subject}

Completes-Bead: {bead}

Co-Authored-By: Claude <noreply@anthropic.com>
"""

SAME_BLOCK = """{subject}

Completes-Bead: {bead}
Co-Authored-By: Claude <noreply@anthropic.com>
"""

FAKE_BD = r"""#!/usr/bin/env python3
import json, os, sys

state = os.environ["FAKE_BD_STATE"]
db = json.load(open(state))
argv = sys.argv[1:]

argv_log = os.environ.get("FAKE_BD_ARGV_LOG")
if argv_log:
    open(argv_log, "a").write(json.dumps(argv) + "\n")

if argv[0] == "list":
    shape = os.environ.get("FAKE_BD_LIST_SHAPE", "issues")
    rows = [dict(v, id=k) for k, v in db["beads"].items()]
    if shape == "empty":
        rows = []
    payload = rows if shape == "bare" else {"issues": rows, "meta": {}, "schema_version": 1}
    print(json.dumps(payload)); sys.exit(0)

# Real bd's grammar: `bd comments <id> --json` lists; `bd comments list` is
# rejected verbatim ("there is no 'comments list'"). Mirror the rejection, or
# the mock is vacuous on the exact bug it should catch.
if argv[0] == "comments":
    if argv[1] == "list":
        sys.stderr.write("Invalid -- use bd comments <issue-id> to list comments\n")
        sys.exit(1)
    # Real `bd comments --json` names the body field `text`; storing it under
    # any other key makes the structured idempotency parse a vacuous mock.
    print(json.dumps(db["comments"].get(argv[1], []))); sys.exit(0)

if argv[0] == "comment":
    body = sys.stdin.read()
    db["comments"].setdefault(argv[1], []).append({"text": body})
    json.dump(db, open(state, "w")); print("Comment added"); sys.exit(0)

if argv[0] == "close":
    db["beads"][argv[1]]["status"] = "closed"
    json.dump(db, open(state, "w")); print("Closed"); sys.exit(0)

sys.exit(64)
"""


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _init(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "p4ji@example.invalid")
    _git(path, "config", "user.name", "Report Claimed Test")
    (path / "base.txt").write_text("base\n")
    _git(path, "add", "base.txt")
    _git(path, "commit", "-q", "-m", "base")
    return path


def _merge(repo, subject, body_bead=None, name="feature", shape=ORPHANED, message=None):
    """Branch, commit, merge back with a real merge commit.

    `subject` is written verbatim so a test can control the trailer group; the
    body trailer is added only when `body_bead` is given. `message` overrides the
    whole commit message verbatim, so a test can plant an author-controlled body.
    """
    _git(repo, "switch", "-q", "-c", name)
    (repo / f"{name}.txt").write_text(f"{name}\n")
    _git(repo, "add", f"{name}.txt")
    if message is None:
        message = shape.format(subject=subject, bead=body_bead) if body_bead else subject
    _git(repo, "commit", "-q", "-m", message)
    _git(repo, "switch", "-q", "main")
    _git(repo, "merge", "--no-ff", "-q", name, "-m", f"Merge pull request from {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def bd(tmp_path):
    """A stand-in `bd` whose entire state is one json file.

    A real one would write the town's ledger, and a test that comments on real
    beads is a test nobody can run twice.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "beads.json"
    state.write_text(json.dumps({"beads": {}, "comments": {}}))
    (bin_dir / "bd").write_text(FAKE_BD)
    (bin_dir / "bd").chmod(0o755)

    class Ledger:
        def add(self, bead, status):
            db = json.loads(state.read_text())
            db["beads"][bead] = {"status": status}
            state.write_text(json.dumps(db))

        def status(self, bead):
            return json.loads(state.read_text())["beads"][bead]["status"]

        def comments(self, bead):
            return [c["text"] for c in json.loads(state.read_text())["comments"].get(bead, [])]

        def seed_comment(self, bead, text):
            """Plant a pre-existing comment, as a human or a prior run would."""
            db = json.loads(state.read_text())
            db["comments"].setdefault(bead, []).append({"text": text})
            state.write_text(json.dumps(db))

    ledger = Ledger()
    ledger.bin_dir = bin_dir
    ledger.state = state
    return ledger


def _run(repo, bd, *args, ref="main", shape="issues"):
    env = dict(os.environ)
    env["PATH"] = f"{bd.bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_BD_STATE"] = str(bd.state)
    env["FAKE_BD_LIST_SHAPE"] = shape
    return subprocess.run(
        ["python3", SCRIPT, *args, "--ref", ref],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def test_a_subject_trailer_alone_is_enough(tmp_path, bd):
    """The #212 case, which a body-trailer-only reader misses entirely.

    #212 landed `...(hy-xjch)` with ZERO `Completes-Bead:` lines and left the
    bead in_progress. Measured: the body-trailer grep returns 0 commits over
    that merge's range. This is the arm that catches it.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-aaaa", "in_progress")
    merge = _merge(repo, "fix(compose): a namespace two seats cannot share (hy-aaaa)")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT, result.stderr
    assert bd.status("hy-aaaa") == "in_progress"
    assert len(bd.comments("hy-aaaa")) == 1
    assert "subject trailer" in bd.comments("hy-aaaa")[0]


def test_a_subject_trailer_group_claims_every_id_in_it(tmp_path, bd):
    """A third of the Mayor's own fixture, recovered.

    Real trailers carry several ids -- `(hy-3187, hy-fc01)`,
    `(hy-ytp1, hy-axg9, hy-sszk)`. A single-id `\\((hy-[a-z0-9]{4,6})\\)$`
    matches none of them and reports nothing, in the reassuring direction. Five
    of the fifteen beads in hy-ghu7 are claimed this way.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-aaaa", "open")
    bd.add("hy-bbbb", "open")
    bd.add("hy-cccc", "open")
    merge = _merge(repo, "docs: correct four claims (hy-aaaa, hy-bbbb, hy-cccc)")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT, result.stderr
    for bead in ("hy-aaaa", "hy-bbbb", "hy-cccc"):
        assert len(bd.comments(bead)) == 1, f"{bead} was dropped by the group reader"


def test_a_mid_sentence_id_is_a_citation_and_is_not_claimed(tmp_path, bd):
    """46586122: "...port the hy-asip reference check... (hy-ou0o)".

    hy-asip is cited, hy-ou0o is claimed, and hy-asip is correctly still open.
    Anchoring the group to the END of the subject excludes it by construction.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-cite", "open")
    bd.add("hy-clam", "open")
    merge = _merge(repo, "feat: port the hy-cite reference check out of scratch (hy-clam)")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT, result.stderr
    assert bd.comments("hy-clam"), "the claimed bead was not reported"
    assert bd.comments("hy-cite") == [], "a citation was reported as a claim"


def test_a_body_trailer_is_read_where_the_subject_says_nothing(tmp_path, bd):
    repo = _init(tmp_path / "repo")
    bd.add("hy-bbbb", "open")
    merge = _merge(repo, "fix: a subject naming nobody", body_bead="hy-bbbb")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT, result.stderr
    assert "Completes-Bead" in bd.comments("hy-bbbb")[0]


def test_both_signals_on_one_commit_produce_one_comment_naming_both(tmp_path, bd):
    repo = _init(tmp_path / "repo")
    bd.add("hy-dddd", "open")
    merge = _merge(repo, "fix: the change (hy-dddd)", body_bead="hy-dddd")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT, result.stderr
    assert len(bd.comments("hy-dddd")) == 1
    body = bd.comments("hy-dddd")[0]
    assert "Completes-Bead" in body and "subject trailer" in body


def test_it_never_changes_a_status(tmp_path, bd):
    """The ruling's first decision, asserted rather than assumed.

    Report, never close, never reopen. Every reachable shape runs under
    `--apply` and nothing may move -- including the `hooked` bead, which is the
    exact shape of the one measured false positive (hy-q2mn was hooked with a
    molecule attached while a commit naming it landed).
    """
    repo = _init(tmp_path / "repo")
    for bead, status in (
        ("hy-aaaa", "open"),
        ("hy-bbbb", "in_progress"),
        ("hy-dddd", "hooked"),
        ("hy-eeee", "blocked"),
    ):
        bd.add(bead, status)
    merge = _merge(repo, "fix: everything (hy-aaaa, hy-bbbb, hy-dddd, hy-eeee)")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT, result.stderr
    assert bd.status("hy-aaaa") == "open"
    assert bd.status("hy-bbbb") == "in_progress"
    assert bd.status("hy-dddd") == "hooked"
    assert bd.status("hy-eeee") == "blocked"


def test_the_comment_carries_the_load_bearing_sentence(tmp_path, bd):
    """Without it the comment reads as an accusation of a missing close."""
    repo = _init(tmp_path / "repo")
    bd.add("hy-aaaa", "open")
    merge = _merge(repo, "fix: the change (hy-aaaa)")

    _run(repo, bd, merge, "--apply")

    body = bd.comments("hy-aaaa")[0]
    assert LOAD_BEARING in body
    assert "still open" in body


def test_a_closed_bead_is_left_silent(tmp_path, bd):
    repo = _init(tmp_path / "repo")
    bd.add("hy-aaaa", "closed")
    merge = _merge(repo, "fix: the change (hy-aaaa)")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_OK, result.stderr
    assert bd.comments("hy-aaaa") == []


def test_bead_ids_are_not_assumed_to_be_four_characters(tmp_path, bd):
    """Live ids are 3 to 38 characters and some carry internal hyphens.

    `hy-[a-z0-9]{4}` and `{4,6}` both drop real beads: 19 open beads are three
    characters. The id set comes from bd, not from a width.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-bwo", "open")
    bd.add("hy-gh-206", "open")
    merge = _merge(repo, "fix: narrow and hyphenated (hy-bwo, hy-gh-206)")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT, result.stderr
    assert bd.comments("hy-bwo"), "a three-character id was dropped"
    assert bd.comments("hy-gh-206"), "a hyphenated id was dropped"


def test_a_candidate_bd_cannot_resolve_is_reported_not_dropped(tmp_path, bd):
    repo = _init(tmp_path / "repo")
    bd.add("hy-real", "open")
    merge = _merge(repo, "fix: the change (hy-real, hy-nope)")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT
    assert "hy-nope" in result.stdout
    assert "resolves no such bead" in result.stdout


def test_it_refuses_when_bd_lists_no_beads_at_all(tmp_path, bd):
    """An empty id set makes every claim read as unresolvable.

    That is the reassuring-direction failure wearing a finding's clothes: it
    exits non-zero and names beads, so it looks like it worked.
    """
    repo = _init(tmp_path / "repo")
    merge = _merge(repo, "fix: the change (hy-aaaa)")

    result = _run(repo, bd, merge, "--apply", shape="empty")

    assert result.returncode == EXIT_REFUSE
    assert "REFUSE" in result.stderr


def test_it_accepts_both_shapes_bd_list_returns(tmp_path, bd):
    """`--status open --json` gives a bare list, `--all --json` gives a dict.

    Accepting only one turns a flag change into an empty id set.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-aaaa", "open")
    merge = _merge(repo, "fix: the change (hy-aaaa)")

    for shape in ("issues", "bare"):
        result = _run(repo, bd, merge, shape=shape)
        assert result.returncode == EXIT_REPORT, f"{shape}: {result.stderr}"
        assert "hy-aaaa" in result.stdout


def test_the_trailer_it_reads_is_invisible_to_gits_own_parser(tmp_path, bd):
    """Why this does not use `--format=%(trailers)`, stated executably.

    If this ever fails because git learned to see the orphaned shape, the
    production reader is still correct -- but the justification in the script's
    docstring has expired and should be re-read rather than carried forward.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-bbbb", "open")
    merge = _merge(repo, "fix: a subject naming nobody", body_bead="hy-bbbb")

    parsed = _git(repo, "log", merge, "--format=%(trailers:key=Completes-Bead,valueonly)")
    assert "hy-bbbb" not in parsed

    assert _run(repo, bd, merge, "--apply").returncode == EXIT_REPORT
    assert bd.comments("hy-bbbb")


@pytest.mark.parametrize("shape", [ORPHANED, SAME_BLOCK], ids=["orphaned", "same_block"])
def test_the_reader_agrees_with_the_consumer_on_both_shapes(tmp_path, bd, shape):
    """The coupling hy-jasw asks for, sourced rather than restated.

    `completion_commit_shas` in the sync script decides whether a closed bead
    may close its GitHub issue. If its predicate changes and this one does not,
    the two disagree silently -- so the consumer is executed, not paraphrased.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-cccc", "open")
    merge = _merge(repo, "fix: a subject naming nobody", body_bead="hy-cccc", shape=shape)

    consumer = subprocess.run(
        ["bash", "-c", f"source {SYNC_SCRIPT}; completion_commit_shas hy-cccc HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    consumer_found = bool(consumer.stdout.strip())

    _run(repo, bd, merge, "--apply")
    we_found = bool(bd.comments("hy-cccc"))

    assert consumer_found is True, "the consumer stopped seeing a shape it used to see"
    assert we_found is consumer_found


def test_it_refuses_a_commit_that_never_landed(tmp_path, bd):
    """Refuse rather than report: the scan cannot speak, it did not find."""
    repo = _init(tmp_path / "repo")
    bd.add("hy-ffff", "in_progress")
    _git(repo, "switch", "-q", "-c", "unmerged")
    (repo / "unmerged.txt").write_text("x\n")
    _git(repo, "add", "unmerged.txt")
    _git(repo, "commit", "-q", "-m", "fix: unlanded (hy-ffff)")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "main")

    result = _run(repo, bd, head, "--apply")

    assert result.returncode == EXIT_REFUSE
    assert "REFUSE" in result.stderr
    assert bd.comments("hy-ffff") == []


def test_it_refuses_a_well_formed_sha_no_repository_holds(tmp_path, bd):
    repo = _init(tmp_path / "repo")

    result = _run(repo, bd, UNHELD, "--apply")

    assert result.returncode == EXIT_REFUSE
    assert "REFUSE" in result.stderr


def test_refuse_and_report_are_not_the_same_exit_code():
    """ "I could not look" must never read as "I looked and it was fine"."""
    assert EXIT_REFUSE != EXIT_REPORT
    assert EXIT_REFUSE != EXIT_OK and EXIT_REPORT != EXIT_OK


def test_it_speaks_only_for_the_branch_that_merge_landed(tmp_path, bd):
    """One invocation, one merge.

    Scanning the whole of main instead would make every run re-assert every past
    merge, so the report becomes the fifteen-line digest the ruling rejected.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-gggg", "in_progress")
    bd.add("hy-hhhh", "in_progress")
    _merge(repo, "fix: first (hy-gggg)", name="first")
    second = _merge(repo, "fix: second (hy-hhhh)", name="second")

    result = _run(repo, bd, second, "--apply")

    assert result.returncode == EXIT_REPORT
    assert bd.comments("hy-hhhh")
    assert bd.comments("hy-gggg") == []
    assert "hy-gggg" not in result.stdout


def test_without_apply_it_comments_nothing_and_still_says_what_it_would_do(tmp_path, bd):
    repo = _init(tmp_path / "repo")
    bd.add("hy-iiii", "in_progress")
    merge = _merge(repo, "fix: the change (hy-iiii)")

    result = _run(repo, bd, merge)

    assert result.returncode == EXIT_REPORT
    assert bd.comments("hy-iiii") == []
    assert "would comment on hy-iiii" in result.stdout


def test_running_it_twice_leaves_one_comment(tmp_path, bd):
    """Post-merge steps get re-run.

    Without the marker the same bead collects one identical comment per
    invocation, which is the fastest way to make the warning unreadable.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-jjjj", "in_progress")
    merge = _merge(repo, "fix: the change (hy-jjjj)")

    assert _run(repo, bd, merge, "--apply").returncode == EXIT_REPORT
    second = _run(repo, bd, merge, "--apply")

    assert len(bd.comments("hy-jjjj")) == 1
    assert "already carries this merge's report" in second.stdout
    assert second.returncode == EXIT_OK


def _load_script():
    """Import the script as a module to call its functions in-process."""
    spec = importlib.util.spec_from_file_location("report_claimed_beads", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_body_cannot_inject_a_field_or_record_boundary(tmp_path, bd):
    """`%B` is author-controlled, so the log reader must not split on a printable
    delimiter a commit body can contain.

    The body below carries the OLD literal separators (`@@@hy-p4ji-record@@@`,
    `@@@hy-p4ji-field@@@`) arranged to forge a whole extra record whose subject
    trailer names hy-aaaa. With NUL separators the body is one inert field: the
    real claim hy-real survives and hy-aaaa is never attributed.

    Mutation proof: revert the separators to the `@@@...@@@` literals and this
    reddens -- the forged record parses and hy-aaaa is claimed, or the real
    record's field-count breaks and hy-real is dropped.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-real", "in_progress")
    bd.add("hy-aaaa", "in_progress")

    evil_body = (
        "fix: real work (hy-real)\n\n"
        "trying to forge a record boundary:\n"
        "@@@hy-p4ji-record@@@deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
        "@@@hy-p4ji-field@@@forged subject (hy-aaaa)"
        "@@@hy-p4ji-field@@@forged body"
    )
    merge = _merge(repo, "fix: real work (hy-real)", message=evil_body)

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT, result.stderr
    assert bd.comments("hy-real"), "the real claim was dropped by a body-injected boundary"
    assert not bd.comments("hy-aaaa"), "a body-injected record forged a claim for hy-aaaa"


def test_marker_key_is_the_full_sha_not_a_prefix(tmp_path, bd, monkeypatch):
    """Two claiming commits sharing a truncated prefix must not collide.

    A prefix key (`sha[:12]`) makes a bead reported for commit A read as
    already-reported for a different commit B that shares those 12 chars, so B's
    report is silently skipped -- the drop this tool prevents, via the key.

    Mutation proof: restore `marker_line` to `f"[{MARKER}:{sha[:12]}]"` and this
    reddens -- A's marker clears B, so `already_reported(bead, b)` returns True.
    """
    mod = _load_script()
    monkeypatch.setenv("PATH", f"{bd.bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_BD_STATE", str(bd.state))
    bd.add("hy-full", "in_progress")

    a = "a" * 12 + "1" * 28
    b = "a" * 12 + "2" * 28
    assert a[:12] == b[:12] and a != b and len(a) == len(b) == 40

    bd.seed_comment("hy-full", "a prior run left:\n" + mod.marker_line(a))

    assert mod.already_reported("hy-full", b) is False, "prefix collision false-cleared B"
    assert mod.already_reported("hy-full", a) is True, "A's own full-sha marker must match"


def test_a_body_merely_containing_the_marker_does_not_false_clear(tmp_path, bd):
    """The silent-drop this whole step exists to prevent, reintroduced in the read.

    A substring scan over the serialized comment treats ANY body that contains
    the token -- a quoted commit subject, a human paste, a related sha's marker
    -- as "already reported" and skips the bead with no signal. The match must be
    STRUCTURED: a comment counts only if one of its LINES equals the bracketed
    marker.

    Mutation proof: revert already_reported to
    `any(token in json.dumps(c) for c in payload)` and this reddens -- the decoy
    below false-clears and the real report is never posted, so len() is 1 not 2.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-mmmm", "in_progress")
    merge = _merge(repo, "fix: the change (hy-mmmm)")
    claiming = _git(repo, "rev-parse", f"{merge}^2")
    token = f"hy-p4ji-report:{claiming}"

    # The exact token, but embedded mid-line -- never a standalone marker line.
    bd.seed_comment("hy-mmmm", f"an earlier note mentioned [{token}] in passing")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_REPORT, result.stderr
    bodies = bd.comments("hy-mmmm")
    assert len(bodies) == 2, "the decoy false-cleared the real report"
    assert any(f"[{token}]" == line.strip() for b in bodies for line in b.splitlines())


def test_the_written_marker_is_the_one_the_reader_matches(tmp_path, bd):
    """Write and check are one source, so a re-run neither double-posts nor skips.

    The real structured marker planted by the first run IS detected by the
    second -- no second comment -- which only holds if comment_text writes and
    already_reported matches the same `[MARKER:sha12]` from the same sha.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-nnnn", "in_progress")
    merge = _merge(repo, "fix: the change (hy-nnnn)")

    assert _run(repo, bd, merge, "--apply").returncode == EXIT_REPORT
    claiming = _git(repo, "rev-parse", f"{merge}^2")
    marker = f"[hy-p4ji-report:{claiming}]"
    bodies = bd.comments("hy-nnnn")
    assert any(marker == line.strip() for b in bodies for line in b.splitlines())

    second = _run(repo, bd, merge, "--apply")
    assert second.returncode == EXIT_OK
    assert len(bd.comments("hy-nnnn")) == 1


def test_the_idempotency_read_uses_bds_real_grammar(tmp_path, bd):
    """The `already-reported?` read must call `bd comments <id> --json`.

    `bd comments list` is INVALID -- bd rejects it verbatim ("there is no
    'comments list'") -- so the wrong subcommand exits non-zero, the read returns
    False, and every run re-posts. The earlier mock mirrored the same invalid
    grammar, so it never caught this; here the fake bd rejects `comments list`
    like the real one, and this test pins the exact argv.

    Mutation proof: revert the script to `["bd","comments","list",bead,...]` and
    both assertions red -- the valid-grammar line is absent and a `comments list`
    line appears.
    """
    repo = _init(tmp_path / "repo")
    bd.add("hy-kkkk", "in_progress")
    merge = _merge(repo, "fix: the change (hy-kkkk)")

    log = tmp_path / "argv.log"
    env = dict(os.environ)
    env["PATH"] = f"{bd.bin_dir}{os.pathsep}{env['PATH']}"
    env["FAKE_BD_STATE"] = str(bd.state)
    env["FAKE_BD_LIST_SHAPE"] = "issues"
    env["FAKE_BD_ARGV_LOG"] = str(log)
    result = subprocess.run(
        ["python3", SCRIPT, merge, "--ref", "main", "--apply"],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == EXIT_REPORT, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert ["comments", "hy-kkkk", "--json"] in calls
    assert not any(c[:2] == ["comments", "list"] for c in calls)


def test_a_merge_claiming_nobody_is_not_an_error(tmp_path, bd):
    repo = _init(tmp_path / "repo")
    merge = _merge(repo, "chore: a change that names no bead at all")

    result = _run(repo, bd, merge, "--apply")

    assert result.returncode == EXIT_OK
    assert "nothing to report" in result.stdout
