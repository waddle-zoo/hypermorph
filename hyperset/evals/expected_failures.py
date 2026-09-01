"""The governed arm's known failures, as a ratchet rather than a demotion.

The first honest measurement of the governed arm was red: it failed two of its
own five structural guarantees (hy-pvbu, hy-9lct). Both are real defects in the
substrate, both are filed, and neither predicate was weakened -- what would be
weakened by shipping that red as a REQUIRED gate on main is every other pull
request in the repository, which is a project-wide tax to make a point the
recordings already make on their own.

So the gate compares the failure set to a declared one and passes only on an
EXACT match. That fails in both directions, which is the whole of why it is a
ratchet:

- a failure nobody declared is red, so a regression cannot hide behind a known
  defect;
- a declared failure that stopped failing is ALSO red, so fixing hy-9lct means
  deleting its entry in the same pull request. A mechanism that silently
  absorbed the fix would let the list outlive the defect, and a list of defects
  that are no longer true is how a benchmark ends up agreeing with itself.

WHAT A DECLARATION CLAIMS OVER SEVERAL RUNS (hy-xfhr, ruling hy-5e1p). Since
hy-qc4u the store keeps every session's run of a case, so "this predicate
fails" stopped naming one outcome. The rule it used to be -- any stored run
failing keeps the declaration alive -- makes the second direction unreachable:
fix the defect, keep the pre-fix recording, and red goes green by adding a file.
Measured at main e95ed2a2, one run exhibiting the defect and that same run
retained beside a run where it is fixed produced BYTE-IDENTICAL reports.

So a declaration says which it is, in a REQUIRED `shape` field with no default:

- `every`: the defect is reproducible, every stored run of the case exhibits
  it. Another failing run needs no edit; one run that does not exhibit it is
  red.
- `some`: the defect is intermittent, and the entry carries the measurement it
  rests on -- `runs_exhibiting` of `runs_scored`. A corpus that no longer shows
  those numbers is red until somebody writes down what it shows now.

THE POPULATION THOSE COUNTS ARE OVER, stated because a rate whose denominator
is unnamed is not a measurement. `stability.repeat_and_report` runs N
repetitions, compares all N and commits `recordings[0]` -- see
`tests/unit/evals/test_live_arms_caller.py:223`, which pins WHICH repetition
lands and says why. So the counts here are over REPETITION ZERO of each
committed session, one per session, and that sample is selected rather than
random: index 0 is the coldest repetition of its session, and warm server state
is one of the two things benchmark.md names as separating sessions. A rate
quoted from a stability report is a different population and does not belong in
this file.

WHAT RETIRES THE ENTRY (hy-p8k5). A declaration that names a defect says
nothing about who can delete it, and both shipped entries named a CLOSED bead:
hy-pvbu closed 2026-07-30, hy-9lct closed 2026-07-29. Nothing could see that,
and its consequence is not cosmetic -- #25's close condition is stated over this
file, and an entry no fix can retire makes that condition unreachable while the
ratchet reads green. So a declaration says which kind it is, in a REQUIRED
`retirement` field with no default:

- `fix`: a defect awaiting a patch. `beads` names the OPEN work whose landing
  deletes the entry, as a LIST, because retirement is not always one bead's to
  do -- the revenue entry goes when the last of hy-3dtc and hy-1r0h lands.
- `limit`: an architectural limit no patch retires. `adr` names the accepted
  decision that says so, and that ADR must NAME THIS ROW -- both the case id and
  the predicate appear in its body -- or `limit` would be a downgrade any
  accepted decision could rubber-stamp.

The two are exclusive, on the same rule the counts follow: a bead on a `limit`
row is a pending fix a reader would go looking for, and an ADR on a `fix` row is
a decision nothing compares.

WHICH BRANCH OF THE PREDICATE (hy-1pqa). A predicate name says what was checked
and not what went wrong, and most of these predicates fail in more than one way.
So a declared failure could change MECHANISM completely while the name held
still, and nothing moved: `revenue_by_region`'s entry covered a MISSING
validate call on one re-roll and, on the next, a call that happens and
mis-resolves against a bundle the arm never resolved. Two different defects, one
entry, green throughout. So a declaration names the branch, in a REQUIRED `code`
field with no default, and the code must be a FAILING branch OF THAT PREDICATE
-- a passing code, or a real code belonging to another predicate, is an entry
that can never hold and therefore excuses nothing while looking accepted.

EVERY exhibiting run must carry the declared code (mayor's ruling on hy-1pqa). A
weaker rule -- at least one exhibiting run matches -- is the "at least one run
failed" rule hy-xfhr already removed once, wearing a new field: it compares
nothing on exactly the rows where mechanism drift is happening.

The declarations are data -- `expected_failures.yaml`, one case id, one
predicate name, one branch code, one line of measured reason, one shape with the
counts that shape requires, and one retirement with the owner that retirement
requires -- and this module refuses anything else: no pattern syntax, no arm
field, no entry naming a case, a predicate or a branch that does not exist, no
counts on a shape that does not claim them, no owner the retirement does not
use. A wildcard here would be the demotion this mechanism exists instead of.

WHAT THIS MODULE STILL CANNOT SEE, because a green load reads as "the
declarations are fine": whether a named bead is OPEN. That needs `bd`, which the
forge does not install, so it lives in `scripts/check_expected_failure_owners.py`
and runs at a seat. Everything checkable without a tracker -- the ADR exists, is
accepted, and adjudicates the row -- is checked here, where CI runs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from hyperset.evals.cases import load_cases
from hyperset.evals.scorers import FAILURE_CODES, PREDICATE_NAMES, Code

EXPECTED_FAILURES_PATH = Path(__file__).parent / "expected_failures.yaml"

SCHEMA_VERSION = 4
"""Bumped three times, each time by a required field with no default.

2, `shape` (hy-xfhr, ruling hy-5e1p): a default would decide what hy-pvbu claims
by omission. 3, `retirement` and the list-valued `beads` (hy-p8k5, ruling of
2026-08-01): a default would decide whether an entry is a pending fix or a
declared architectural limit, which is the distinction the scalar `bead` field
could not carry and nobody could see it failing to carry. 4, `code` (hy-1pqa): a
default would decide which BRANCH of the predicate the entry accepts, and the
only available default is "any of them" -- which is the rule this bump exists to
end.

An older file does not load, deliberately, and the version moves with the file
in ONE commit: a bump that lands in two is a window where the loader and the
declarations disagree about what the declarations mean."""

EVERY_RUN = "every"
"""The defect is reproducible: EVERY stored run of the case exhibits it.

Growth-tolerant -- committing another run that also fails needs no edit here --
and it is the shape that carries its own ratchet, because one run that stops
exhibiting the defect falsifies the declaration outright."""

SOME_RUN = "some"
"""The defect is intermittent: it is exhibited by `runs_exhibiting` of the
`runs_scored` runs the declaration was measured over, and both numbers are part
of the claim.

Growth-SENSITIVE, and that is what stops it being an opt-out from hy-xfhr. A
bare "at least one run fails" is that opt-out: fix the defect, keep the old
failing recording, and the declaration is true forever. Carrying the counts
means a corpus that has moved no longer matches what was declared, so the entry
goes red until somebody writes the new measurement down -- in a diff, as a
claim, rather than by leaving a sentence that has quietly stopped being the
thing that was measured."""

SHAPES = (EVERY_RUN, SOME_RUN)

COUNT_FIELDS = ("runs_scored", "runs_exhibiting")
"""Required on `some` and REFUSED on `every`. An `every` entry carrying counts
would state a population it does not claim over, and nothing would ever compare
those numbers to the corpus -- decoration a later reader reads as evidence."""

FIX = "fix"
"""The entry is a defect awaiting a patch, and `beads` names the OPEN work whose
landing deletes it.

A list rather than a scalar because retirement is not always one bead's to do:
the revenue entry's own reason says it goes when the LAST of hy-3dtc and hy-1r0h
lands, and a scalar field asked to carry that forced somebody to write a bead
that was not the whole answer."""

LIMIT = "limit"
"""The entry records an architectural limit that no patch retires, and `adr`
names the decision that says so.

ADR 0016 is why this value exists: it decided that the coverage-claim entry
"does not expire", that hy-9lct "owns the record, not a pending fix", and that
the stronger guarantee is out of reach rather than unimplemented. Read as `fix`,
that row looks like rot -- a closed bead nobody can retire the entry with -- and
the only repairs available are reversing GitHub #70 or filing a bead nobody
intends to fix so the field looks right (hy-p8k5)."""

RETIREMENTS = (FIX, LIMIT)

ADR_DIR = Path(__file__).resolve().parents[2] / "docs" / "adr"

ADR_ACCEPTED = "Status: accepted"
"""What a `limit` row's ADR must still say.

A superseded or deprecated decision is exactly the case this check exists for:
the entry would go on claiming a limit the repository had stopped accepting, and
nothing else in the tree reads the two together."""


def adr_number(path: str) -> str:
    """`docs/adr/0016-....md` -> `0016`, for the one-line owner a report prints."""
    return Path(path).name.split("-", 1)[0]


class UnusableExpectation(RuntimeError):
    """A declared failure this harness will not honour, and why.

    Loud at load rather than quiet at comparison: an entry naming a predicate
    that does not exist excuses nothing and matches nothing, so it would sit in
    the file looking like a governed defect somebody had accepted.
    """


@dataclass(frozen=True)
class ExpectedFailure:
    case_id: str
    predicate: str
    code: Code
    reason: str
    shape: str
    retirement: str
    beads: tuple[str, ...] = ()
    adr: str | None = None
    runs_scored: int | None = None
    runs_exhibiting: int | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.case_id, self.predicate)

    @property
    def owner(self) -> str:
        """Who can retire this entry, in one string a report can print.

        A `fix` row's owners are beads and a `limit` row's owner is an ADR, so
        there is no single field to print and the reports had one. Derived
        rather than stored, because a stored copy is a third place for the
        answer to be wrong (hy-p8k5).
        """
        if self.retirement == LIMIT:
            return f"ADR {adr_number(self.adr or '')}"
        return ", ".join(self.beads)

    def holds(self, *, runs_scored: int, runs_exhibiting: int, codes: frozenset[Code]) -> bool:
        """Does this declaration still describe the corpus it is compared to?

        THE WHOLE OF hy-xfhr IS IN THE COUNTS, and the reason they arrive as two
        numbers rather than a boolean. The rule they replace asked "does any
        stored run still fail this?", which is satisfied by a run recorded
        before the fix: red goes green by adding a file, and the direction that
        makes the ratchet a ratchet stops being reachable.

        THE WHOLE OF hy-1pqa IS IN `codes`, which are the branches the
        EXHIBITING runs took. Matching on the predicate name alone let a
        declared failure change mechanism completely and move no exit code --
        `revenue_by_region` carried a MISSING validate call on one re-roll and a
        call that happens and mis-resolves on the next, under one entry, both
        green. EVERY exhibiting run must carry the declared code: a corpus
        producing two codes under one entry is red, because a rule that only
        asked whether ONE run matched would compare nothing on exactly the rows
        where drift is happening.
        """
        if runs_exhibiting == 0:
            return False
        if codes != {self.code}:
            return False
        if self.shape == EVERY_RUN:
            return runs_exhibiting == runs_scored
        return (runs_scored, runs_exhibiting) == (self.runs_scored, self.runs_exhibiting)

    def describe(self, *, runs_scored: int, runs_exhibiting: int, codes: frozenset[Code]) -> str:
        """What a reader has to do about a declaration that no longer holds."""
        observed = f"{runs_exhibiting} of {runs_scored} stored run(s) exhibit it"
        if runs_exhibiting == 0:
            return f"declared {self.shape}; none of {runs_scored} stored run(s) exhibit it"
        if codes != {self.code}:
            # BEFORE the shape, because a mechanism change is the more specific
            # fact and a count that also moved is usually downstream of it. A
            # reader told only that the rate changed would re-measure the rate
            # and re-state a declaration that is now about a different defect.
            return (
                f"declared the {self.code.value!r} branch; the stored runs fail it through "
                f"{', '.join(sorted(code.value for code in codes))}. The defect under this "
                "predicate is not the one the entry describes -- re-read the runs and restate "
                "the entry, or file the new one"
            )
        if self.shape == EVERY_RUN:
            return (
                f"declared every run exhibits it; {observed}. Either the defect is "
                "intermittent and the entry states so with its counts, or it is fixed and "
                "the entry goes"
            )
        return (
            f"declared some, measured {self.runs_exhibiting} of {self.runs_scored}; "
            f"{observed}. Re-state what the corpus now shows or delete the entry"
        )

    def to_dict(self) -> dict:
        declared = {
            "case_id": self.case_id,
            "predicate": self.predicate,
            "code": self.code.value,
            "reason": self.reason,
            "shape": self.shape,
            "retirement": self.retirement,
            "owner": self.owner,
        }
        if self.retirement == FIX:
            declared["beads"] = list(self.beads)
        else:
            declared["adr"] = self.adr
        if self.shape == SOME_RUN:
            declared["runs_scored"] = self.runs_scored
            declared["runs_exhibiting"] = self.runs_exhibiting
        return declared


def load_expected_failures(path: Path | None = None) -> tuple[ExpectedFailure, ...]:
    """Read the declarations, or refuse the file and say which entry.

    The path is resolved at call time rather than bound as a default, for the
    reason `score_recordings` resolves its directory the same way: a rule whose
    red path can only be reached by editing the shipped file is a rule nobody
    has watched turn red.
    """
    path = EXPECTED_FAILURES_PATH if path is None else path
    payload = yaml.safe_load(path.read_text()) or {}
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise UnusableExpectation(
            f"{path}: expected-failure file schema {payload.get('schema_version')!r}; this "
            f"loader understands {SCHEMA_VERSION}"
        )
    known_cases = {case.id for case in load_cases()}
    entries = tuple(_entry(raw, known_cases, path) for raw in payload.get("expected") or ())
    duplicates = sorted(
        {entry.key for entry in entries if [e.key for e in entries].count(entry.key) > 1}
    )
    if duplicates:
        raise UnusableExpectation(f"{path}: the same failure is declared twice: {duplicates}")
    return entries


def _refuse(path: Path, raw: dict, message: str) -> UnusableExpectation:
    """Every load-time refusal, carrying the file and the entry it is about.

    Named rather than described (hy-xfhr condition 3): the alternative to a
    refusal here is not a smaller error, it is a report that says the corpus was
    scored while an entry nobody could parse sat in the file. A reader who
    cannot find the entry cannot delete it either.
    """
    entry = raw.get("case_id") or "?"
    predicate = raw.get("predicate") or "?"
    return UnusableExpectation(f"{path}: entry {entry}/{predicate}: {message}")


def _entry(raw: dict, known_cases: set[str], path: Path) -> ExpectedFailure:
    missing = [
        field
        for field in ("case_id", "predicate", "code", "reason", "shape", "retirement")
        if not raw.get(field)
    ]
    if missing:
        raise _refuse(
            path,
            raw,
            f"expected failure {raw!r} is missing {', '.join(missing)}; every accepted defect "
            "names what was measured, which branch of the predicate produced it, the shape of "
            "the claim, and what retires the entry",
        )
    if raw["case_id"] not in known_cases:
        raise _refuse(
            path,
            raw,
            f"expected failure names case {raw['case_id']!r}, which no case file defines; an "
            "entry that matches nothing excuses nothing and hides in the file",
        )
    if raw["predicate"] not in PREDICATE_NAMES:
        raise _refuse(
            path,
            raw,
            f"expected failure names predicate {raw['predicate']!r}, which no scorer produces; "
            f"the predicates are {', '.join(PREDICATE_NAMES)}",
        )
    code = _failing_branch(raw, path)
    shape = str(raw["shape"])
    if shape not in SHAPES:
        raise _refuse(
            path,
            raw,
            f"expected failure declares shape {shape!r}; the shapes are {', '.join(SHAPES)}. "
            "A declaration says whether every stored run of the case exhibits the defect or "
            "only some, because those are two different claims and the corpus can hold more "
            "than one run",
        )
    retirement = str(raw["retirement"])
    if retirement not in RETIREMENTS:
        raise _refuse(
            path,
            raw,
            f"expected failure declares retirement {retirement!r}; the values are "
            f"{', '.join(RETIREMENTS)}. A declaration says whether a patch retires it, and "
            "names the open beads that will, or whether a decision holds it permanently, and "
            "names the ADR that decided so",
        )
    owner = _owner(raw, retirement, path)
    counts = _counts(raw, shape, path)
    return ExpectedFailure(
        case_id=str(raw["case_id"]),
        predicate=str(raw["predicate"]),
        code=code,
        reason=" ".join(str(raw["reason"]).split()),
        shape=shape,
        retirement=retirement,
        **owner,
        **counts,
    )


def _failing_branch(raw: dict, path: Path) -> Code:
    """The branch this entry accepts, checked against the predicate's own set.

    TWO REFUSALS AND NOT ONE, because they are different mistakes. A code no
    scorer produces is a typo or a branch that was deleted; a real code that
    belongs to ANOTHER predicate reads correct and is the likelier error, since
    the codes are English and several are near-synonyms across predicates.

    Both leave an entry that can never hold, which is why this is a load-time
    refusal rather than a comparison that quietly comes out red: the entry would
    sit in the file looking like an accepted defect while excusing nothing --
    the failure the `case_id` and `predicate` refusals already exist for.

    A PASSING branch is refused by the same check without a separate arm, since
    `FAILURE_CODES` holds only failures. That is deliberate: declaring the
    passing branch of a predicate would be declaring that the arm is expected to
    succeed and calling it an accepted defect.
    """
    predicate = str(raw["predicate"])
    accepted = FAILURE_CODES[predicate]
    try:
        code = Code(str(raw["code"]))
    except ValueError:
        raise _refuse(
            path,
            raw,
            f"expected failure names branch {str(raw['code'])!r}, which no scorer produces; "
            f"the failing branches of {predicate!r} are "
            f"{', '.join(entry.value for entry in accepted)}",
        ) from None
    if code not in accepted:
        raise _refuse(
            path,
            raw,
            f"expected failure names branch {code.value!r}, which {predicate!r} never "
            f"produces as a failure; its failing branches are "
            f"{', '.join(entry.value for entry in accepted)}. A code from another predicate, "
            "or a passing one, is an entry no run can ever match",
        )
    return code


def _owner(raw: dict, retirement: str, path: Path) -> dict:
    """Who can retire the entry, and whether they plausibly can.

    THE FIELDS ARE EXCLUSIVE, on the rule `_counts` already follows: `beads` on
    a `limit` row and `adr` on a `fix` row are values no rule compares, and this
    file is the one place in the benchmark where an unchecked value would excuse
    a failure.

    WHY `limit` IS NOT AN ESCAPE HATCH (mayor's ruling on hy-p8k5). The open-bead
    arm does not apply to a `limit` row, so `limit` is what a fix row would be
    downgraded to if downgrading were cheap. Naming an accepted ADR is not
    enough -- any accepted ADR would do, and none of them would be about the
    row. So the named decision must NAME THE ENTRY: the case id and the
    predicate both appear in the ADR body. ADR 0016 already does, verbatim, so
    the price is only paid by a downgrade that is not adjudicated anywhere.
    """
    beads = raw.get("beads")
    adr = raw.get("adr")
    if retirement == FIX:
        if adr is not None:
            raise _refuse(
                path,
                raw,
                f"expected failure declares retirement {FIX!r} and names an ADR; a patch "
                "retires this row, so the thing to name is the open work that will land it",
            )
        if not isinstance(beads, list) or not beads or not all(isinstance(b, str) for b in beads):
            raise _refuse(
                path,
                raw,
                f"expected failure declares retirement {FIX!r} and must carry `beads` as a "
                "non-empty list of bead ids; the list is not decoration -- retirement is not "
                "always one bead's to do, and a scalar field forced somebody to name a bead "
                "that was not the whole answer",
            )
        return {"beads": tuple(str(bead) for bead in beads)}
    if beads is not None:
        raise _refuse(
            path,
            raw,
            f"expected failure declares retirement {LIMIT!r} and carries `beads`; no bead "
            "retires a limit, and a bead named here is a pending fix a reader would go "
            f"looking for. ADR 0016's own words: the entry 'does not expire'",
        )
    if not isinstance(adr, str) or not adr:
        raise _refuse(
            path,
            raw,
            f"expected failure declares retirement {LIMIT!r} and must name the `adr` that "
            "decided the entry does not expire; a permanent entry whose decision is not on "
            "the record is indistinguishable from one nobody got round to fixing",
        )
    return {"adr": _adjudicating_adr(adr, raw, path)}


def _adjudicating_adr(adr: str, raw: dict, path: Path) -> str:
    """The ADR a `limit` row names, once it is shown to adjudicate that row."""
    document = ADR_DIR / Path(adr).name
    if Path(adr).parent.name != ADR_DIR.name or not document.is_file():
        raise _refuse(
            path,
            raw,
            f"expected failure names ADR {adr!r}, which is not a file in docs/adr/; a "
            "decision a reader cannot open is not a decision they can check",
        )
    body = document.read_text()
    if ADR_ACCEPTED not in body:
        raise _refuse(
            path,
            raw,
            f"expected failure rests on {adr!r}, which does not say {ADR_ACCEPTED!r}; a "
            "superseded decision leaves the entry claiming a limit this repository stopped "
            "accepting",
        )
    unnamed = [field for field in ("case_id", "predicate") if str(raw[field]) not in body]
    if unnamed:
        raise _refuse(
            path,
            raw,
            f"expected failure rests on {adr!r}, which never names its "
            f"{' or '.join(unnamed)}; an ADR that does not adjudicate THIS row would make "
            f"{LIMIT!r} a downgrade any accepted decision could rubber-stamp",
        )
    return adr


def _counts(raw: dict, shape: str, path: Path) -> dict[str, int]:
    """The population a `some` declaration was measured over, or nothing.

    Refused on `every` rather than ignored: a number no rule compares is read
    as evidence by the next person, and this file is the one place in the
    benchmark where an unchecked number would excuse a failure.
    """
    present = [field for field in COUNT_FIELDS if raw.get(field) is not None]
    if shape == EVERY_RUN:
        if present:
            raise _refuse(
                path,
                raw,
                f"expected failure declares shape {EVERY_RUN!r} and carries "
                f"{', '.join(present)}; {EVERY_RUN!r} claims over whatever runs exist, so a "
                "count here is a population nothing compares",
            )
        return {}
    if sorted(present) != sorted(COUNT_FIELDS):
        raise _refuse(
            path,
            raw,
            f"expected failure declares shape {SOME_RUN!r} and must carry "
            f"{' and '.join(COUNT_FIELDS)}; an intermittent defect declared without the "
            "measurement it rests on is 'at least one run failed', which stays true forever "
            "once a failing recording is committed (hy-xfhr)",
        )
    scored, exhibiting = (int(raw[field]) for field in COUNT_FIELDS)
    if not 1 <= exhibiting <= scored:
        raise _refuse(
            path,
            raw,
            f"expected failure declares {exhibiting} of {scored} stored run(s) exhibit it; "
            "a declaration needs at least one run that exhibits the defect and cannot claim "
            "more runs than it was measured over",
        )
    return {"runs_scored": scored, "runs_exhibiting": exhibiting}
