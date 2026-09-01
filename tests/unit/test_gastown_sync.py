"""The intake watcher, driven through stubbed `gh`, `bd` and `gt`.

The script's own reads and writes are the whole contract here: which beads it
re-sends to `bd import`, what it counts as new, and whether it summons the
mayor. Each test therefore runs the real functions with the three commands
they shell out to replaced by recording stubs, rather than asserting on
internal variables.
"""

import json
from pathlib import Path
from subprocess import run

ROOT = Path(__file__).resolve().parents[2]

# One bead per status the intake guard has to tell apart.
BEADS = [
    {"id": "hy-gh-70", "status": "open", "assignee": None},
    {"id": "hy-gh-99", "status": "in_progress", "assignee": "hyperset/crew/hyperion"},
    {"id": "hy-gh-42", "status": "closed", "assignee": None},
]

BD_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$1" in
  list)
    if [[ "$*" == *"--no-assignee"* ]]; then
      cat "$STUB_DIR/unassigned.json"
    else
      cat "$STUB_DIR/beads.json"
    fi
    ;;
  import)
    cp "$2" "$STUB_DIR/imported.jsonl"
    cat "$STUB_DIR/import-result.json"
    ;;
  where) printf '{"prefix":"hy"}\\n' ;;
  dolt) ;;
esac
"""

GH_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$1" in
  issue)
    case "$2" in
      list) cat "$STUB_DIR/issues.json" ;;
      *) printf 'CLOSED\\n' ;;
    esac
    ;;
esac
"""

GT_STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$STUB_LOG"
"""


def _sandbox(tmp_path, *, issues=None, beads=None, import_result=None, unassigned=None):
    stub_dir = tmp_path / "stubs"
    bin_dir = tmp_path / "bin"
    stub_dir.mkdir()
    bin_dir.mkdir()
    for name, body in (("bd", BD_STUB), ("gh", GH_STUB), ("gt", GT_STUB)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    (stub_dir / "issues.json").write_text(json.dumps(issues or []))
    (stub_dir / "beads.json").write_text(json.dumps(beads if beads is not None else BEADS))
    (stub_dir / "unassigned.json").write_text(json.dumps(unassigned or []))
    (stub_dir / "import-result.json").write_text(
        json.dumps(import_result or {"created": 0, "ids": [], "skipped": 0})
    )
    (stub_dir / "calls.log").write_text("")
    return stub_dir, bin_dir


def _run(snippet, tmp_path, stub_dir, bin_dir):
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin",
        "HOME": str(tmp_path),
        "STUB_DIR": str(stub_dir),
        "STUB_LOG": str(stub_dir / "calls.log"),
    }
    return run(
        ["bash", "-c", f"source ./scripts/gh-to-gastown-sync.sh\n{snippet}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        env=env,
    )


# `config_defaults` fills the rest, so a test states only what it varies and
# the script keeps owning its own defaults.
PRELUDE = """
GH_REPO="waddle-zoo/hyperset"
RIG_PATH="$STUB_DIR"
INBOX_DIR="$STUB_DIR/inbox"
config_defaults
DRY_RUN=0
BEAD_PREFIX="hy"
# Whichever state fetch the script defines, so a run against the version
# before this fix still installs its own guard and the failure names the
# beads that actually leak rather than the rename.
read_bead_state() {
  if declare -f fetch_bead_states >/dev/null; then fetch_bead_states; else fetch_active_bead_ids; fi
}
"""


def test_dependency_mention_is_not_completion_proof():
    result = run(
        [
            "bash",
            "-c",
            "source ./scripts/gh-to-gastown-sync.sh; completion_commit_shas hy-gh-30 HEAD",
        ],
        cwd=ROOT,
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.stdout == ""


def test_intake_re_sends_only_the_beads_that_are_open(tmp_path):
    """A bead that is closed or in progress must not be re-imported.

    `bd import` writes the status it is given, so re-sending a settled bead
    rewrites it: measured against beads 1.1.0, a closed bead re-imported from
    a touched source comes back with 'status closed -> open'.
    """
    issues = [
        {
            "number": n,
            "title": f"#{n}",
            "body": "",
            "url": f"u/{n}",
            "updatedAt": "2026-07-01T00:00:00Z",
            "labels": [],
        }
        for n in (70, 99, 42, 123)
    ]
    stub_dir, bin_dir = _sandbox(tmp_path, issues=issues)
    result = _run(
        PRELUDE + "\nread_bead_state\nimport_github_issues\n", tmp_path, stub_dir, bin_dir
    )
    written = (stub_dir / "imported.jsonl").read_text().splitlines()
    sent = [json.loads(line)["id"] for line in written if line]
    assert sent == ["hy-gh-70", "hy-gh-123"], result.stdout


def test_a_row_that_only_ties_is_not_counted_as_a_new_bead(tmp_path):
    """bd's `created` counts every accepted row, ties included.

    Measured: re-importing two unchanged rows reports created=2 with no
    `updated` at all, which is why the log claimed '7 new' on every pass for
    beads that had existed for days.
    """
    stub_dir, bin_dir = _sandbox(
        tmp_path,
        import_result={
            "created": 2,
            "ids": ["hy-gh-70", "hy-gh-42"],
            "skipped": 1,
            "stale_skipped_ids": ["hy-gh-99"],
            "tie_kept_local_ids": ["hy-gh-70", "hy-gh-42"],
        },
    )
    (stub_dir / "in.jsonl").write_text("")
    result = _run(
        PRELUDE + '\nread_bead_state\nrun_bd_import "$STUB_DIR/in.jsonl" "GitHub issues"\n',
        tmp_path,
        stub_dir,
        bin_dir,
    )
    assert "0 new" in result.stdout, result.stdout


def test_the_mayor_is_not_summoned_when_nothing_new_arrived(tmp_path):
    """The readiness claim is derived, not asserted.

    Three consecutive passes summoned the mayor for ten GitHub issues that
    were already triaged; an unconditional claim trains its reader to ignore
    the notification.
    """
    stub_dir, bin_dir = _sandbox(
        tmp_path,
        import_result={"created": 2, "ids": ["hy-gh-70", "hy-gh-42"], "skipped": 0},
    )
    (stub_dir / "in.jsonl").write_text("")
    result = _run(
        PRELUDE
        + '\nread_bead_state\nrun_bd_import "$STUB_DIR/in.jsonl" "GitHub issues"\nnotify_mayor\n',
        tmp_path,
        stub_dir,
        bin_dir,
    )
    calls = (stub_dir / "calls.log").read_text()
    # An absence proves nothing until the thing being denied was reachable
    # (hy-9vb3). Renaming `notify_mayor` in the snippet above left this test
    # green while three positive siblings went red: bash exited 127 having run
    # nothing, the log was empty, and "no nudge" was indistinguishable from
    # "no run". So the run is required to have succeeded, and the log is
    # required to hold the call that precedes the one being denied.
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    assert "import" in calls, f"the stub log records nothing, so it cannot record a nudge:\n{calls}"
    assert "nudge" not in calls, f"{calls}\n{result.stdout}"


def test_the_mayor_is_summoned_for_a_new_bead_that_is_open_and_unassigned(tmp_path):
    stub_dir, bin_dir = _sandbox(
        tmp_path,
        import_result={"created": 1, "ids": ["hy-gh-123"], "skipped": 0},
        unassigned=[{"id": "hy-gh-123", "status": "open", "assignee": None}],
    )
    (stub_dir / "in.jsonl").write_text("")
    result = _run(
        PRELUDE
        + '\nread_bead_state\nrun_bd_import "$STUB_DIR/in.jsonl" "GitHub issues"\nnotify_mayor\n',
        tmp_path,
        stub_dir,
        bin_dir,
    )
    calls = (stub_dir / "calls.log").read_text()
    assert "nudge" in calls, f"{calls}\n{result.stdout}"
    assert "hy-gh-123" in calls


def _dependency_refs(body, tmp_path):
    stub_dir, bin_dir = _sandbox(tmp_path)
    (stub_dir / "body.md").write_text(body)
    result = _run(
        # The empty list is a real answer here -- a body with no `Depends on`
        # section declares nothing -- and a snippet that never ran produces
        # exactly the same empty stdout. So the symbol is required to exist
        # before its silence is read as an answer (hy-9vb3).
        #
        # NOT the return code, which was measured and cannot do this job: the
        # script runs under `set -o pipefail`, and the honest empty answer
        # exits 1 because the `grep -oE '#[0-9]+'` inside the function matches
        # nothing. `declare -F` asks the only question that separates the two
        # -- is the function this test names actually defined?
        PRELUDE + "\ndeclare -F issue_dependency_refs > /dev/null || exit 97"
        '\nissue_dependency_refs "$(cat "$STUB_DIR/body.md")"\n',
        tmp_path,
        stub_dir,
        bin_dir,
    )
    assert result.returncode != 97, (
        "issue_dependency_refs is not defined, so an empty result here is the "
        f"function's absence and not its answer:\n{result.stderr}"
    )
    return result.stdout.split()


def test_only_the_depends_on_section_declares_a_dependency(tmp_path):
    """Structure, not prose.

    Issue bodies discuss other issues constantly -- "#25 remains the
    authoritative benchmark" is a comparison, not a gate. Only what a
    `## Depends on` heading covers is read as an edge, which is how these
    bodies are already written.
    """
    body = "\n".join(
        [
            "Intro naming #99 in passing.",
            "",
            "## Depends on",
            "",
            "- #31 trusted `ContextBundle` + plan validation",
            "- authoritative Git context from #43",
            "",
            "## Notes",
            "",
            "- Not a replacement for #25's release-gate benchmark.",
        ]
    )
    assert _dependency_refs(body, tmp_path) == ["31", "43"]


def test_a_declared_chain_is_every_issue_in_it(tmp_path):
    """`- #27 -> #17 -> #43` under a Depends-on heading is real: issue #34
    declares that whole chain ahead of it, so each link is an edge."""
    body = "## Depends on\n\n- #27 → #17 → #43\n- Contract checks begin now.\n"
    assert _dependency_refs(body, tmp_path) == ["27", "17", "43"]


def test_a_body_without_the_section_declares_nothing(tmp_path):
    assert _dependency_refs("Plain body mentioning #70 and #71.\n", tmp_path) == []


def test_declared_dependencies_become_bd_edges(tmp_path):
    """The edge is added only when both beads exist.

    A reference to an issue the rig has no bead for (never imported, or
    imported under another prefix) is logged and skipped: `bd dep add`
    fails on an unknown id, and a failed edge must not fail the pass.
    """
    issues = [
        {
            "number": 33,
            "title": "gated",
            "body": "## Depends on\n\n- #25\n- #4242\n",
            "url": "u/33",
            "updatedAt": "2026-07-01T00:00:00Z",
            "labels": [],
        }
    ]
    stub_dir, bin_dir = _sandbox(
        tmp_path,
        issues=issues,
        beads=[
            {"id": "hy-gh-33", "status": "open", "assignee": None},
            {"id": "hy-gh-25", "status": "open", "assignee": None},
        ],
        import_result={"created": 1, "ids": ["hy-gh-33"], "skipped": 0},
    )
    result = _run(
        PRELUDE + "\nread_bead_state\nimport_github_issues\napply_declared_dependencies\n",
        tmp_path,
        stub_dir,
        bin_dir,
    )
    calls = (stub_dir / "calls.log").read_text()
    assert "dep add hy-gh-33 hy-gh-25" in calls, f"{calls}\n{result.stdout}"
    assert "hy-gh-4242" not in calls, calls
