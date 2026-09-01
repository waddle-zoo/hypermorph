#!/usr/bin/env python3
"""Can each declared failure still be retired by the bead it names? (hy-p8k5)

`expected_failures.yaml` refuses an entry naming a case or a predicate that
does not exist, because such an entry excuses nothing and hides in the file.
An entry naming a CLOSED bead is the same class of defect one level out: the
bead that owns the fix has already landed, so no fix can ever delete the entry,
and the ratchet reads green the whole time. Both shipped entries were in that
state when this script was written -- hy-pvbu closed 2026-07-30, hy-9lct closed
2026-07-29 -- and nothing could see it.

WHY THIS IS A SCRIPT AND NOT A TEST IN THE GATE, stated because "there is a
check" and "the check runs where it matters" are different claims. Answering
"is this bead open" means asking `bd`, and `bd` is a Gas Town tool that the
forge does not install: `.github/workflows/ci.yml` runs `uv sync`, ruff and
pytest, and nothing in this repository ships a beads export a test could read
instead (`.beads/` holds one gitignored redirect file). A pytest case here
would skip on CI, and a skipped guard over a defect nobody can see is the same
green as no guard at all. So the logic lives in a script an agent runs at a
seat, and `tests/unit/evals/test_expected_failure_owners.py` drives THIS FILE
through a stubbed `bd` so the classification itself is gated everywhere.

THREE OUTCOMES, NEVER TWO, the same rule `cross_session` follows: `bd` missing
or refusing to answer is CANNOT-CHECK and a non-zero exit, never a pass. An
owner check that quietly returns success when it could not look would be the
exact failure it exists to catch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hyperset.evals.expected_failures import (  # noqa: E402
    EXPECTED_FAILURES_PATH,
    LIMIT,
    load_expected_failures,
)

LINE_PREFIX = "HYPERSET-OWNERS v1"

PASS = "PASS"
ROTTED = "ROTTED"
CANNOT_CHECK = "CANNOT-CHECK"

OPEN_STATES = ("open", "in_progress", "hooked", "blocked")
"""Every status from which a bead can still land a fix that deletes an entry.

Read off `bd` rather than assumed: `hy-gh-25` reports `hooked` while `bd ready`
renders the same bead as BLOCKED, so the rendered word and the status field are
not one vocabulary.
"""

CLOSED_STATES = ("closed",)
"""The whole of what this script is about: an owner that can land nothing.

A status in NEITHER tuple is CANNOT-CHECK rather than a guess in either
direction. Guessing open would hide the defect this exists to catch; guessing
closed would fail a pull request over a word `bd` added.
"""


def _status(bead: str, bd: str) -> tuple[str | None, str | None]:
    """The bead's status, or why it could not be read. Never both."""
    try:
        done = subprocess.run(  # noqa: S603
            [bd, "show", bead, "--json"], capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError:
        return None, f"{bd!r} is not on PATH, so no owner could be read"
    except subprocess.TimeoutExpired:
        return None, f"{bd!r} did not answer within 60s"
    if done.returncode != 0:
        return None, f"{bd} show {bead} exited {done.returncode}: {done.stdout.strip()[:200]}"
    try:
        payload = json.loads(done.stdout)
    except json.JSONDecodeError:
        return None, f"{bd} show {bead} did not return JSON: {done.stdout.strip()[:200]}"
    if not isinstance(payload, list) or not payload:
        return None, f"{bd} show {bead} returned no issue"
    status = payload[0].get("status")
    if not isinstance(status, str) or not status:
        return None, f"{bd} show {bead} returned no status field"
    return status, None


def check(path: Path, bd: str) -> tuple[str, list[str], int]:
    """Classify every declared entry's owner. Returns (outcome, lines, entries).

    The entry count is returned rather than derived from the lines: a `fix` row
    with two beads prints two lines, so a reader who counted lines would read
    three owners as three declarations.
    """
    entries = load_expected_failures(path)
    lines: list[str] = []
    rotted = 0
    unreadable = 0
    for entry in entries:
        row = f"{entry.case_id}/{entry.predicate}"
        if entry.retirement == LIMIT:
            # Checked by the loader, which reads the ADR and needs no tracker to
            # do it. Printed rather than skipped silently: a row this script does
            # not judge is a row a reader must not read this line as covering.
            lines.append(f"limit        {row} -> {entry.owner}, checked at load")
            continue
        for bead in entry.beads:
            status, why = _status(bead, bd)
            if why is not None:
                unreadable += 1
                lines.append(f"CANNOT-CHECK {row} -> {bead}: {why}")
            elif status in OPEN_STATES:
                lines.append(f"open         {row} -> {bead}")
            elif status in CLOSED_STATES:
                rotted += 1
                lines.append(
                    f"ROTTED       {row} -> {bead} is {status!r}, so the fix that would "
                    "delete this entry has already landed and no fix can delete it now"
                )
            else:
                unreadable += 1
                lines.append(
                    f"CANNOT-CHECK {row} -> {bead} is {status!r}, which is neither an open "
                    f"state ({', '.join(OPEN_STATES)}) nor {CLOSED_STATES[0]!r}"
                )
    if unreadable:
        return CANNOT_CHECK, lines, len(entries)
    return (ROTTED if rotted else PASS), lines, len(entries)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=EXPECTED_FAILURES_PATH)
    parser.add_argument("--bd", default="bd", help="the beads executable to ask")
    args = parser.parse_args()

    outcome, lines, entries = check(args.path, args.bd)
    for line in lines:
        print(line)
    print(f"{LINE_PREFIX} entries={entries} owners={len(lines)} path={args.path} result={outcome}")
    return 0 if outcome == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
