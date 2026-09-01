"""`docs/development/benchmark.md` against the numbers `score_recordings()` emits.

The document states the result as prose -- governed 6/6, raw baseline 4/6, a
per-case table, governed-only 3/5, two declared failures with their beads --
and those are the numbers everyone quotes. Nothing read them: no test compared
the document to the report, and `scripts/check_docs.py` did not list this file
at all, so a re-record could move every number and leave the document claiming
the old ones. That is hy-698's failure mode, and the precedent for the fix is
`tests/unit/test_section_7_matches_the_served_contract.py` -- comparing a
document to code means importing the code.

PARSED rather than searched for, which is the whole difference between this and
a phrase check: a test that looks for the substring "6/6" is green on a
document that says 6/6 in one paragraph and 5/6 two paragraphs later. Every
fraction below is pulled out of the sentence that owns it and compared to the
measured value, and one test additionally refuses any fraction in the section
that the recordings do not produce at all.

The cost, stated so nobody is surprised by it: editing only this document can
now fail the test gate. That is the intended direction. A re-record is supposed
to make these tests red until the document is rewritten to the new numbers --
which is exactly the moment the old ones would otherwise survive.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from hyperset.evals.expected_failures import load_expected_failures
from hyperset.evals.recording import ARMS, GOVERNED_ARM
from hyperset.evals.report import score_recordings
from hyperset.evals.scorers import SHARED_PREDICATES

DOC = Path(__file__).resolve().parents[3] / "docs" / "development" / "benchmark.md"

RESULT_SECTION = "## The recorded result at this commit"
COMPARISON_SECTION = "## How the arms are compared"

RAW_ARM = next(arm for arm in ARMS if arm != GOVERNED_ARM)

FRACTION = re.compile(r"\b(\d+)/(\d+)\b")
TABLE_ROW = re.compile(r"^\| `(\w+)` \| (\d+)/(\d+) \| (\d+)/(\d+)", re.MULTILINE)
HEADLINE = re.compile(r"(\d+)/(\d+) against (\d+)/(\d+) is six predicate instances per arm")
GOVERNED_ONLY = re.compile(r"Governed-only predicates, (\d+)/(\d+)\.")
INSTANCE = re.compile(r"`(\w+)` (once|twice) \(([a-z,\s]+)\)")
DECLARED_FAILURE = re.compile(
    r"^- `(\w+)`,\s+`(\w+)`\s+\(\*\*(fix|limit): ([^*]+)\*\*\)", re.MULTILINE
)
SHARED_SET = re.compile(r"the \*\*shared predicate set\*\* -- ([^-]+) -- and\nonly that set")

COUNT_WORDS = {"once": 1, "twice": 2}


def _section(title: str) -> str:
    """One `##` section, so a number in another section cannot answer for this one."""
    text = DOC.read_text()
    start = text.index(title)
    end = text.index("\n## ", start + len(title))
    return text[start:end]


@cache
def _report() -> dict:
    return score_recordings()


def _measured_shared_by_case() -> dict[tuple[str, str], tuple[int, int]]:
    """Each recording's shared-set score, which is what the table's cells are."""
    measured = {}
    for run in _report()["runs"]:
        shared = [entry for entry in run["scores"] if entry["predicate"] in SHARED_PREDICATES]
        measured[(run["arm"], run["case_id"])] = (
            len([entry for entry in shared if entry["passed"]]),
            len(shared),
        )
    return measured


def test_the_per_case_table_is_the_shared_score_of_each_recording():
    """Both directions: a row for a case nobody scored is a number from a
    deleted recording, and a scored case with no row is a result the document
    does not report."""
    rows = TABLE_ROW.findall(_section(RESULT_SECTION))
    assert rows, "no per-case rows parsed out of the result table"

    documented = {}
    for case_id, governed_passed, governed_scored, raw_passed, raw_scored in rows:
        documented[(GOVERNED_ARM, case_id)] = (int(governed_passed), int(governed_scored))
        documented[(RAW_ARM, case_id)] = (int(raw_passed), int(raw_scored))
    assert documented == _measured_shared_by_case()


def test_the_headline_pair_is_each_arms_shared_score():
    match = HEADLINE.search(_section(RESULT_SECTION))
    assert match, "the headline sentence did not parse"

    governed_passed, governed_scored, raw_passed, raw_scored = (
        int(part) for part in match.groups()
    )
    arms = _report()["arms"]
    assert (governed_passed, governed_scored) == (
        arms[GOVERNED_ARM]["shared_passed"],
        arms[GOVERNED_ARM]["shared_scored"],
    )
    assert (raw_passed, raw_scored) == (
        arms[RAW_ARM]["shared_passed"],
        arms[RAW_ARM]["shared_scored"],
    )


def test_the_governed_only_fraction_is_the_governed_arms_governed_only_score():
    match = GOVERNED_ONLY.search(_section(RESULT_SECTION))
    assert match, "the governed-only fraction did not parse"

    arm = _report()["arms"][GOVERNED_ARM]
    assert (int(match.group(1)), int(match.group(2))) == (
        arm["governed_only_passed"],
        arm["governed_only_scored"],
    )


def test_the_instance_breakdown_is_every_governed_only_instance_and_its_verdicts():
    """The point of that sentence is that 3/5 is five INSTANCES, not five
    guarantees, so the instance counts are checked and not only the total. A
    predicate that stops being scored -- or starts being scored on a second
    case -- moves a count here while leaving 3/5 arithmetically possible."""
    breakdown = INSTANCE.findall(_section(RESULT_SECTION))
    assert breakdown, "no governed-only instance breakdown parsed"

    documented = {}
    for predicate, count, verdicts in breakdown:
        # Wrapped across lines in the document, so split on the comma rather
        # than on ", " -- a check that reds on where a paragraph wraps is one
        # somebody disables instead of fixing (hy-at4).
        outcomes = [verdict.strip() for verdict in verdicts.split(",")]
        assert len(outcomes) == COUNT_WORDS[count], (
            f"{predicate} is documented as {count} with {verdicts!r}"
        )
        documented[predicate] = (outcomes.count("pass"), COUNT_WORDS[count])
    measured = {
        predicate: (counts["passed"], counts["scored"])
        for predicate, counts in _report()["arms"][GOVERNED_ARM]["by_predicate"].items()
        if predicate not in SHARED_PREDICATES
    }
    assert documented == measured


def test_the_documented_failures_are_exactly_the_declared_ones():
    """Case, predicate, RETIREMENT and owner, against `expected_failures.yaml`.

    The owner is checked because it is what a reader follows to the fix: a
    bullet naming an owner the ratchet does not is a defect nobody can find the
    owner of. The retirement is checked with it (hy-p8k5) because the two words
    say different things to that reader -- `fix` sends them to open work,
    `limit` to a decision -- and a bullet that says one while the file says the
    other sends them somewhere the entry is not."""
    bullets = DECLARED_FAILURE.findall(_section(RESULT_SECTION))
    assert bullets, "no declared-failure bullets parsed"

    declared = {
        (entry.case_id, entry.predicate, entry.retirement, entry.owner)
        for entry in load_expected_failures()
    }
    assert set(bullets) == declared


def test_the_result_section_states_no_fraction_the_recordings_do_not_produce():
    """The guard against the stale number a targeted parse would step over:
    every `n/m` in the section has to be one the report actually emits, so a
    superseded fraction left in a paragraph nobody re-read is red."""
    arms = _report()["arms"]
    measured = {
        *_measured_shared_by_case().values(),
        *((arm["shared_passed"], arm["shared_scored"]) for arm in arms.values()),
        *((arm["governed_only_passed"], arm["governed_only_scored"]) for arm in arms.values()),
    }
    stated = {
        (int(passed), int(scored)) for passed, scored in FRACTION.findall(_section(RESULT_SECTION))
    }
    assert stated <= measured, f"fractions no arm scored: {sorted(stated - measured)}"


def test_the_document_lists_the_shared_predicate_set_the_scorers_use():
    """Sorted rather than in the document's order: which four predicates are
    shared is the claim, and reordering a prose list changes nothing a reader
    can act on (hy-at4)."""
    match = SHARED_SET.search(_section(COMPARISON_SECTION))
    assert match, "the shared-predicate-set sentence did not parse"

    documented = re.findall(r"`(\w+)`", match.group(1))
    assert sorted(documented) == sorted(SHARED_PREDICATES)
