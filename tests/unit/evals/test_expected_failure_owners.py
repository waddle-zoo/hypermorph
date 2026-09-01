"""Does the owner check see a bead that can no longer land the fix? (hy-p8k5)

`scripts/check_expected_failure_owners.py` is a script rather than a test
because answering "is this bead open" means asking `bd`, and the forge does not
install `bd` (`.github/workflows/ci.yml` runs `uv sync`, ruff and pytest, and
nothing in this repository ships a beads export). A guard that skips on CI is
the same green as no guard, so the tracker call stays in the script and the
CLASSIFICATION is gated here, through a stubbed `bd` that answers whatever an
arm needs.

The defect being guarded is not hypothetical: both shipped entries named a
CLOSED bead -- hy-pvbu 2026-07-30, hy-9lct 2026-07-29 -- so no fix could ever
delete either entry, and the ratchet read green throughout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from hyperset.evals.expected_failures import FIX, LIMIT, SCHEMA_VERSION
from hyperset.evals.scorers import Code
from scripts.check_expected_failure_owners import (
    CANNOT_CHECK,
    LINE_PREFIX,
    PASS,
    ROTTED,
    check,
)

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check_expected_failure_owners.py"
ADJUDICATED_ADR = "docs/adr/0016-declared-coverage-claim-not-question-reading.md"

LIMIT_ROW = {
    "case_id": "supply_chain_lead_time",
    "predicate": "no_governed_answer_without_a_governed_domain",
    "code": Code.GOVERNED_CONTEXT_FOR_AN_UNGOVERNED_QUESTION.value,
    "retirement": LIMIT,
    "adr": ADJUDICATED_ADR,
    "reason": "measured",
    "shape": "every",
}


def declaring(tmp_path, beads):
    """A file holding one `fix` row owned by `beads`, beside the shipped limit."""
    path = tmp_path / "expected.yaml"
    path.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "expected": [
                    {
                        "case_id": "revenue_by_region",
                        "predicate": "plan_validated_before_the_answer",
                        "code": Code.DID_NOT_VALIDATE.value,
                        "retirement": FIX,
                        "beads": list(beads),
                        "reason": "measured",
                        "shape": "every",
                    },
                    LIMIT_ROW,
                ],
            }
        )
    )
    return path


def bd_stub(tmp_path, statuses, monkeypatch):
    """A `bd` that answers `statuses`, and logs every bead it was asked about.

    A status of None is `bd`'s own answer for an id it does not know: exit 1
    with an error object rather than an issue, which is what a deleted or
    mistyped bead looks like.
    """
    log = tmp_path / "asked.log"
    log.write_text("")
    lines = ["#!/usr/bin/env bash", 'printf "%s\\n" "$2" >> "$STUB_LOG"', 'case "$2" in']
    for bead, status in statuses.items():
        if status is None:
            lines.append(f"""  {bead}) printf '{{"error":"no issue"}}\\n'; exit 1 ;;""")
        else:
            lines.append(f"""  {bead}) printf '[{{"id":"{bead}","status":"{status}"}}]\\n' ;;""")
    lines.append("""  *) printf '{"error":"unknown"}\\n'; exit 1 ;;""")
    lines.append("esac")
    path = tmp_path / "bd"
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    monkeypatch.setenv("STUB_LOG", str(log))
    return str(path), log


def test_open_owners_pass_and_the_limit_row_is_never_asked(tmp_path, monkeypatch):
    """The green case, and the boundary that makes `limit` checkable at all.

    A limit row has no beads by construction, so there is nothing here to ask
    about -- the loader already read its ADR. Asserted against the stub's log
    rather than inferred from the outcome, because "did not ask" and "asked and
    liked the answer" are the same PASS from the outside.
    """
    bd, log = bd_stub(tmp_path, {"hy-3dtc": "open", "hy-1r0h": "in_progress"}, monkeypatch)

    outcome, lines, entries = check(declaring(tmp_path, ["hy-3dtc", "hy-1r0h"]), bd)

    assert outcome == PASS
    assert entries == 2
    assert log.read_text().split() == ["hy-3dtc", "hy-1r0h"]
    assert [line.split()[0] for line in lines] == ["open", "open", "limit"]


def test_a_closed_owner_is_rotted_and_the_line_names_the_entry_and_the_bead(tmp_path, monkeypatch):
    """The defect this exists for, in the state the file shipped in.

    One of two owners closed is enough: the entry is retired by whichever lands
    last, so a closed bead in the list is a name a reader would follow to work
    that is already done.
    """
    bd, _ = bd_stub(tmp_path, {"hy-3dtc": "open", "hy-1r0h": "closed"}, monkeypatch)

    outcome, lines, _ = check(declaring(tmp_path, ["hy-3dtc", "hy-1r0h"]), bd)

    assert outcome == ROTTED
    rotted = [line for line in lines if line.startswith(ROTTED)]
    assert len(rotted) == 1
    assert "revenue_by_region/plan_validated_before_the_answer" in rotted[0]
    assert "hy-1r0h" in rotted[0]


@pytest.mark.parametrize(
    ("statuses", "bd_name", "expected_in_line"),
    [
        ({"hy-3dtc": "open"}, "bd-that-is-not-there", "not on PATH"),
        ({"hy-3dtc": None}, None, "exited 1"),
        ({"hy-3dtc": "quantum"}, None, "'quantum'"),
    ],
)
def test_an_owner_the_script_could_not_read_is_never_a_pass(
    tmp_path, monkeypatch, statuses, bd_name, expected_in_line
):
    """THREE OUTCOMES, NEVER TWO, the rule `cross_session` follows.

    `bd` absent, `bd` refusing the id, and `bd` answering a word this script
    does not know are three different failures to look, and none of them is
    evidence that the owner is open. A checker that returned success when it
    could not look would be the exact defect it exists to catch, so each is
    CANNOT-CHECK -- distinguishable from ROTTED, and a non-zero exit either way.
    """
    stub, _ = bd_stub(tmp_path, statuses, monkeypatch)
    bd = str(tmp_path / bd_name) if bd_name else stub

    outcome, lines, _ = check(declaring(tmp_path, ["hy-3dtc"]), bd)

    assert outcome == CANNOT_CHECK
    assert outcome != PASS
    unreadable = [line for line in lines if line.startswith(CANNOT_CHECK)]
    assert len(unreadable) == 1
    assert expected_in_line in unreadable[0]


def test_a_bead_it_could_not_read_outranks_a_bead_it_found_closed(tmp_path, monkeypatch):
    """A file with both defects reports both and exits non-zero, and the outcome
    is the weaker claim: some owner's state is unknown, so ROTTED is more than
    this run measured even though one row demonstrably rotted."""
    bd, _ = bd_stub(tmp_path, {"hy-3dtc": "closed", "hy-1r0h": None}, monkeypatch)

    outcome, lines, _ = check(declaring(tmp_path, ["hy-3dtc", "hy-1r0h"]), bd)

    assert outcome == CANNOT_CHECK
    assert [line.split()[0] for line in lines] == [ROTTED, CANNOT_CHECK, "limit"]


def test_the_script_prints_one_line_and_exits_non_zero_on_rot(tmp_path, monkeypatch):
    """End to end, because the line and the exit code are what a seat reads.

    `entries` and `owners` are separate numbers on it: a `fix` row with two
    beads prints two lines, so a reader counting lines would read three owners
    as three declarations.
    """
    bd, _ = bd_stub(tmp_path, {"hy-3dtc": "open", "hy-1r0h": "closed"}, monkeypatch)
    path = declaring(tmp_path, ["hy-3dtc", "hy-1r0h"])

    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(path), "--bd", bd],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )

    assert done.returncode == 1
    line = done.stdout.strip().splitlines()[-1]
    assert line.startswith(LINE_PREFIX)
    assert f"entries=2 owners=3 path={path} result={ROTTED}" in line
