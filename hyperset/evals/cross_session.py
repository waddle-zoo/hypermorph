"""Two sessions of one case, compared, or refused (hy-hk5m, ruling hy-wwk3).

`stability` compares the repetitions INSIDE one process. hy-hk5m measured what
that cannot see: one tree, one model, one digest, one window, one seed, one
temperature, one prompt hash and one tool-schema hash, three sessions, TWO
behaviours -- one session called `validate_analytics_plan` and failed two
lexical predicates, two sessions never called it and passed them. Each session
was internally unanimous and printed `flapping=0`. Nothing in the harness
compared them -- past tense on that half alone, because this module is the
comparison -- and the required gate's colour IS STILL decided by WHICH session
got recorded. Measured at tree 614ce956 on the declared entry hy-9lct and
re-measured at a6d5adf4: recording the session that behaves BETTER turns the
gate red, and recording the worse one leaves it green.

WHICH red, and by what rule, is not restated here. That belongs to
`expected_failures.ExpectedFailure.holds` and the declaration's `shape`, and it
has already moved once underneath this paragraph: a qualifier naming the escape
where retaining the worse run kept the gate green was load-bearing at 614ce956
and obsolete at a6d5adf4, when hy-xfhr closed that escape (hy-daa5). A sentence
restating a mechanism it does not own goes stale every time the mechanism moves,
silently, because nothing compares prose to code. What this module needs from
the ratchet is only the dependency, not its shape. hy-qc4u changed WHAT IS
STORED and every stored run is now scored; it did not change which session gets
recorded, and the clause rests on session composition and warm server state,
which it does not touch.

WHY THIS COMPARES LINES RATHER THAN REPORTS, and it is no longer because the
line is the only artifact that survives a session. Recordings survive now: the
store is keyed on the run, `recordings/<arm>/<case>/<run_id>.json`, so two
sessions of one case at one tree coexist (hy-qc4u). A pair of them is still not
what this compares, because a `Recording` is ONE REPETITION and carries no
per-axis identity and no flapping count. The aggregate over a session's
repetitions is a `StabilityReport`, and that lives only in the process that ran
it. Its line is what an agent pastes into a bead, a pull request or a release
note, and what a reader holding no copy of the tree that produced it can still
read -- so the line is where the identities had to go and here is what reads two
of them.

THREE OUTCOMES, NEVER TWO (hy-wwk3). `Agreed`, `Disagreed` and `CannotCompare`
are three types with three renderings and no shared branch: a refusal is not
reachable from the code path that says AGREE, and it does not render like it. A
two-valued instrument answers with the safe word when it could not look, which
is the same rule as NOT-MEASURED on #25's evidence sheet -- and here the safe
word would be "stable".

WHAT MAKES TWO SESSIONS COMPARABLE, and why the key is strict. Every pin the
line carries -- INCLUDING `sha` -- must be equal. A disagreement between two
different trees has an explanation that is not the model: any code change
between them can move what the tools return or what the prompt renders, and
`SCHEMA_VERSION` is not even a repository pin (hy-5e19), so response prose the
model reads can change with no pin moving at all. Widening the key to admit two
commits would buy a comparison whose every DISAGREE carries a permanent
alternative explanation. The refusal is the honest answer, and it is a
first-class one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hyperset.evals.stability import (
    STABILITY_LINE_VERSION,
    UnreadableStabilityLine,
    parse_stability_line,
)

AGREE = "AGREE"
DISAGREE = "DISAGREE"
CANNOT_COMPARE = "CANNOT-COMPARE"

COMPARISON_LINE_PREFIX = "HYPERSET-CROSS-SESSION v1"
"""Its own line, versioned on its own, because it answers a different question
from the stability line and a reader must not be able to mistake one for the
other."""

COMPARABILITY_KEY = (
    "sha",
    "arm",
    "case",
    "model",
    "digest",
    "ollama_version",
    "context_window",
    "seed",
    "temperature",
    "prompt_hash",
    "tools_hash",
)
"""What two sessions must agree on BEFORE their behaviour can be compared.

The cross-session form of `PinsDrifted`, and `sha` is in it deliberately:
`RECORDING_PINS` refuses a `git_commit` drift between two repetitions of one
report, and two sessions at two commits are the same drift one level up. `n` is
NOT in it -- a session of three repetitions and a session of one are comparable,
they simply saw different amounts -- and neither is any COUNT, which is the
whole point: counts are what two divergent sessions print identically.

THE HOST PINS, NAMED, because an undeclared exclusion cannot be told from an
oversight and this one WAS an oversight (hy-a1i0). `RunPins` carries nine pins;
this key names eight of them.

`ollama_version` is IN. It is the pin nothing else covers: two sessions run on
two Ollama builds printed BYTE-IDENTICAL lines and compared AGREE -- measured --
while the identical drift between two repetitions of one session is refused by
name. Cross-session is the level where host pins actually diverge; inside one
process they cannot. So this was the pin the comparator most needed and the one
it had dropped.

`quantization` is OUT, and that is a decision rather than the same omission:
`pins.py` holds that the digest pins the bytes, `digest` is on the line and in
this key, so a quantization change that is a real change moves the digest with
it. The day a store reuses one digest across quantizations, `quantization`
belongs here.
"""

AXES = ("verdicts", "traces", "answers_id", "evidence_id")
"""The four axes of behaviour, each an identity over content rather than a
count. `verdicts` and `traces` are legible so a reader sees WHICH predicate and
WHICH shape moved; `answers_id` and `evidence_id` are hashes because their
content is unbounded (`stability._identity`)."""


@dataclass(frozen=True)
class Session:
    """One parsed stability line, with the fields this module reads."""

    fields: dict[str, str]

    @property
    def label(self) -> str:
        return f"{self.fields.get('arm', '?')}/{self.fields.get('case', '?')}@{self.sha}"

    @property
    def sha(self) -> str:
        return (self.fields.get("sha") or "?")[:12]


@dataclass(frozen=True)
class Agreed:
    """Two or more comparable sessions that saw the same thing on every axis."""

    sessions: tuple[Session, ...]
    outcome: str = AGREE

    def line(self) -> str:
        return (
            f"{COMPARISON_LINE_PREFIX} sessions={len(self.sessions)} outcome={AGREE} "
            f"axes_moved=0 {_pins_field(self.sessions[0])}"
        )

    def render(self) -> str:
        out = [self.line()]
        out.append(
            f"  {len(self.sessions)} sessions at identical pins agreed on all "
            f"{len(AXES)} axes: {', '.join(AXES)}"
        )
        for axis in AXES:
            out.append(f"    {axis} = {self.sessions[0].fields[axis]}")
        return "\n".join(out)


@dataclass(frozen=True)
class Disagreed:
    """Comparable sessions whose behaviour differed on at least one axis."""

    sessions: tuple[Session, ...]
    moved: tuple[str, ...]
    outcome: str = DISAGREE

    def line(self) -> str:
        return (
            f"{COMPARISON_LINE_PREFIX} sessions={len(self.sessions)} outcome={DISAGREE} "
            f"axes_moved={len(self.moved)} moved={','.join(self.moved)} "
            f"{_pins_field(self.sessions[0])}"
        )

    def render(self) -> str:
        """The line, then WHAT each session saw on every axis that moved.

        A count of moved axes is not actionable for the reason a flapping count
        is not (hy-y91y): the reader has to see that one session called validate
        and the other did not, which is the finding, not `axes_moved=2`.
        """
        out = [self.line()]
        for axis in self.moved:
            out.append(f"  MOVED {axis}:")
            for session in self.sessions:
                out.append(f"    {session.label}: {session.fields[axis]}")
        steady = [axis for axis in AXES if axis not in self.moved]
        if steady:
            out.append(f"  held: {', '.join(steady)}")
        return "\n".join(out)


@dataclass(frozen=True)
class CannotCompare:
    """Lines this module refused to compare, and the reason it refused.

    NOT a disagreement and not an agreement. Separate from both by type, so no
    caller can fall into it from either and no rendering of it can be mistaken
    for a verdict about the model. What it reports is a fact about the LINES.
    """

    reason: str
    detail: tuple[str, ...] = ()
    outcome: str = CANNOT_COMPARE

    def line(self) -> str:
        return f"{COMPARISON_LINE_PREFIX} outcome={CANNOT_COMPARE} reason={_slug(self.reason)}"

    def render(self) -> str:
        out = [self.line()]
        out.append(f"  {self.reason}")
        out.extend(f"    {item}" for item in self.detail)
        out.append(
            "  This is not agreement. Nothing here says the sessions behaved alike; "
            "it says they were not comparable."
        )
        return "\n".join(out)


Comparison = Agreed | Disagreed | CannotCompare


def _slug(reason: str) -> str:
    """The reason as one whitespace-free token, so the line stays parseable."""
    return reason.split(":")[0].strip().replace(" ", "-").lower()


def _pins_field(session: Session) -> str:
    return " ".join(f"{field}={session.fields.get(field, '?')}" for field in COMPARABILITY_KEY)


def compare_sessions(lines: Sequence[str]) -> Comparison:
    """Compare pasted stability lines from two or more sessions.

    Every refusal returns `CannotCompare` and every refusal names what it could
    not do. Nothing here raises: a caller holding two lines from two different
    trees has asked a reasonable question and deserves an answer, and an
    exception is an answer that a `try/except` turns into silence.
    """
    if len(lines) < 2:
        return CannotCompare(
            reason="one session is not a comparison: at least two stability lines are needed",
            detail=(f"got {len(lines)}",),
        )
    sessions: list[Session] = []
    for index, line in enumerate(lines):
        try:
            sessions.append(Session(fields=parse_stability_line(line)))
        except UnreadableStabilityLine as error:
            return CannotCompare(
                reason=f"unreadable line: line {index} could not be read as a "
                f"{STABILITY_LINE_VERSION} stability line",
                detail=(str(error),),
            )
    missing = [
        f"line {index} carries no {field}"
        for index, session in enumerate(sessions)
        for field in (*COMPARABILITY_KEY, *AXES)
        if field not in session.fields
    ]
    if missing:
        return CannotCompare(
            reason="missing field: a line without every pin and every axis cannot be compared",
            detail=tuple(missing),
        )
    first = sessions[0]
    drifted = [
        f"{field}: {first.sha} has {first.fields[field]!r}, "
        f"{session.sha} has {session.fields[field]!r}"
        for session in sessions[1:]
        for field in COMPARABILITY_KEY
        if session.fields[field] != first.fields[field]
    ]
    if drifted:
        return CannotCompare(
            reason="pins drifted: two sessions run under different pins measure the difference",
            detail=tuple(drifted),
        )
    moved = tuple(
        axis
        for axis in AXES
        if any(session.fields[axis] != first.fields[axis] for session in sessions[1:])
    )
    if moved:
        return Disagreed(sessions=tuple(sessions), moved=moved)
    return Agreed(sessions=tuple(sessions))
