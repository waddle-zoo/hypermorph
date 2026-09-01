"""A recorded arm run: the artifact ADR 0013's per-PR gate actually scores.

The split gate says required CI scores a committed, versioned RECORDING rather
than a live model, and that every report must say so. Both halves are here.
`Recording` is what a live run writes and what the gate reads back, and
`DISCLOSURE` is the sentence a report carries -- as a value the scorers attach
to their own output, because a disclosure a human has to remember to write is
the one that goes missing from the report that most needed it.

A recording is evidence, so it is held to a run's obligations rather than a
fixture's: it carries its pins, and it carries what produced it. A trace whose
provenance says `scripted` came from `ScriptedRuntime` and no model ran at all,
which `refuse_a_fixture` rejects -- otherwise the cheapest way to a green
benchmark would be to commit a script that calls the right tools in order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from hyperset.evals.cases import task_version
from hyperset.evals.pins import RunPins, assert_host_pins_recorded, assert_pins
from hyperset.planner.trace import RUNTIME_NAMES, SCRIPTED_RUNTIME

RECORDING_SCHEMA_VERSION = 2
"""Bumped when the shape below changes, so a reader can refuse a shape it does
not understand instead of scoring the fields it recognises and ignoring the
rest -- which is how a recording missing half its steps scores as a clean run.

2 BECAUSE `run_id` WAS ADDED (hy-qc4u). One file per arm and case meant a second
session of one case overwrote the first, so the pair #25's close condition wants
compared could not both exist. Telling two runs apart needs an identifier, the
identifier has to live INSIDE the recording rather than only in its path -- a
path is destroyed by a copy into a scratch directory, which is exactly what
comparing across trees does -- and a field added is a shape change.

IT STAYED 1 THROUGH THE EVIDENCE WALK CHANGE (hy-szg4, hy-o79s, hy-q2mn) on a
bound rather than an observation: that walk reads `trace`, which this shape
already carried, so no field was added, removed or re-meant. This one adds a
field, so the same rule that held it at 1 is the rule that moves it to 2.

THE FOUR COMMITTED RECORDINGS ARE STILL SCHEMA 1 AND ARE NOT REWRITTEN. They
were recorded before runs were told apart and no honest run id can be added to
them now; `_read_schema_1` below is the deliberate, narrow reader for that
frozen corpus, and hy-l13a deletes it when the re-record lands."""

DISCLOSURE = (
    "This report scored a COMMITTED RECORDING of an arm, not a live model run. "
    "It proves the harness, the scorers and the recorded run's scores. It does not "
    "prove that a live model still passes; the scheduled live arms are what check that "
    "(ADR 0013)."
)

GOVERNED_ARM = "governed"
RAW_ARM = "raw_baseline"
FRONTIER_ARM = "frontier_raw"
ARMS = (GOVERNED_ARM, RAW_ARM)
"""#25's arms 1 and 2: the LOCAL Ollama arms. These are the arms the required
per-PR gate scores a committed recording of, and the arms the live scheduled job
records -- every one MUST have a committed recording, so this tuple is exactly
the arms `score_recordings` and the inspect dataset enumerate. `FRONTIER_ARM` is
deliberately NOT here (adding it would make the gate demand a frontier recording
that no credential-free CI can produce); it is scored only when present (hy-0qr6)."""

RECOGNIZED_ARMS = (*ARMS, FRONTIER_ARM)
"""Every arm a recording may name -- arms 1 and 2 plus arm 3, the pinned frontier
model on the SAME raw metadata (hy-0qr6, #25 scope 1). Arm 3 is recognized so a
frontier recording is READABLE and scorable, but it is not REQUIRED (`ARMS`): a
live frontier run needs a hosted credential and an authorized decision (Brandon
fork hy-2tg6), so nothing here produces its recording, and until one is committed
the arm is simply absent from the report."""


class UnreadableRecording(RuntimeError):
    """A recording this reader will not score, with the reason it will not."""


class RefusedComparison(RuntimeError):
    """A comparison that reached a run this store cannot identify.

    Raised rather than answered, and never caught to produce a default: the
    answer this replaces is "these two runs agree", which is the sentence a
    reader of a benchmark most wants to be true and least wants to be guessed.
    """


class Unidentified:
    """The run id of a recording written before runs were told apart.

    A DISTINCT INSTANCE PER READ, and that is the whole design rather than a
    detail. A module-level singleton cannot refuse anything: `is` is checked
    before `__eq__` inside every container and every dataclass-generated
    `__eq__` (`PyObject_RichCompareBool`), so one shared instance reads AGREE
    from `[U] == [U]`, `(U,) == (U,)`, `{'a': U} == {'a': U}`, `U in [U]` and
    `Rec(U) == Rec(U)` without `__eq__` ever being called. Measured, all five.
    With two freshly minted instances the shortcut cannot fire and every one of
    those refuses instead.

    So: `Recording.from_dict` mints a new one on every schema-1 read, nothing
    caches or memoizes them, and identity questions are asked with `isinstance`
    and never with `is`. A later refactor that adds a cache to the reader
    reintroduces the singleton and quietly restores all five agreements, which
    is why the test that two reads of one file mint non-identical sentinels is
    the load-bearing one.

    `__hash__` refuses too, so a set or a dict key cannot launder a comparison
    into a membership test. `repr` is deliberately not identifier-shaped: it
    shows up in failure output, and `<run 0000...>` invites someone to paste it
    into a comparison.
    """

    __slots__ = ()

    def _refuse(self, other: object) -> RefusedComparison:
        return RefusedComparison(
            f"this recording predates run ids (schema 1) and cannot be compared to {other!r}; "
            "an unidentified run is not the same run as another unidentified run, and answering "
            "either way would be a claim about which sessions these were"
        )

    def __eq__(self, other: object) -> bool:
        raise self._refuse(other)

    def __ne__(self, other: object) -> bool:
        raise self._refuse(other)

    def __hash__(self) -> int:
        raise RefusedComparison(
            "an unidentified run has no hash; hashing it would let a set or a dict key answer "
            "the comparison this type exists to refuse"
        )

    def __repr__(self) -> str:
        return "<no run id: schema 1>"


def describe_run_id(run_id: str | Unidentified) -> str:
    """A run id rendered for a person to read, never for anything to compare.

    Reports and logs need something printable for a schema-1 recording. This
    returns the repr, which cannot be mistaken for a real run id -- those are
    hex -- and it is deliberately NOT a comparison surface: two schema-1
    recordings render the same string, so a caller that asks "same run?" of two
    of these strings gets the wrong answer. Ask it of `Recording.run_id`, which
    refuses. When hy-l13a deletes the schema-1 reader this can only ever return
    a real id and the caveat goes with it.
    """
    return run_id if isinstance(run_id, str) else repr(run_id)


@dataclass(frozen=True)
class Recording:
    """One arm answering one case, with everything needed to believe it."""

    run_id: str | Unidentified
    """Which run this was, minted per process at session establishment.

    DECLARED FIRST, and the position is load-bearing rather than stylistic.
    Dataclass `__eq__` compares field tuples and tuple comparison stops at the
    first unequal element, so a field declared ahead of `run_id` that differs
    answers False before `run_id` is ever consulted and the refusal never runs.
    Measured with distinct sentinels and a differing `arm`: declared last, the
    comparison answers False; declared first, it refuses. Moving it down the
    class disarms the refusal and no test above notices unless it is this one.

    Early placement buys reachability for DISTINCT sentinels only. It does not
    rescue a SHARED one: the identity shortcut fires at index 0 and the scan
    walks past it, so `Rec(u) == copy.copy(Rec(u))` is still True. That is what
    the per-read minting is for; the two mechanisms cover different halves.
    """

    schema_version: int
    arm: str
    case_id: str
    task_version: str
    git_commit: str
    recorded_at: str
    pins: RunPins
    trace: dict
    source_refs: list[str]
    """The observed source dependency refs this run's evidence rests on, which
    #25 requires a run to persist. Kept beside the trace rather than derived
    from it at scoring time: what a run depended on is a fact about that run,
    and re-deriving it later applies today's reader to yesterday's payload."""

    def to_dict(self) -> dict:
        """The payload written to disk.

        A schema-1 recording read back carries the sentinel here, so
        `json.dumps` raises `TypeError` and `write` cannot round-trip one of the
        four committed files into a schema-2 artifact. That is the intended
        end: re-recording them is a live run's job (hy-zwoj), not a serializer's.
        """
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "arm": self.arm,
            "case_id": self.case_id,
            "task_version": self.task_version,
            "git_commit": self.git_commit,
            "recorded_at": self.recorded_at,
            "disclosure": DISCLOSURE,
            "pins": self.pins.to_dict(),
            "source_refs": list(self.source_refs),
            "trace": self.trace,
        }

    def write(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n")

    @classmethod
    def from_dict(cls, payload: dict) -> Recording:
        version = payload.get("schema_version")
        reader = _READERS.get(version)
        if reader is None:
            raise UnreadableRecording(
                f"recording schema {version!r}, this reader understands "
                f"{' and '.join(str(known) for known in sorted(_READERS))}; scoring a shape it "
                "does not know would score the fields it recognises and silently ignore the rest"
            )
        arm = payload.get("arm")
        if arm not in RECOGNIZED_ARMS:
            raise UnreadableRecording(
                f"unknown arm {arm!r}; the arms are {', '.join(RECOGNIZED_ARMS)}, and an "
                "arm nothing defines cannot be compared to any of them"
            )
        return cls(
            run_id=reader(payload),
            schema_version=version,
            arm=arm,
            case_id=str(payload.get("case_id") or ""),
            task_version=str(payload.get("task_version") or ""),
            git_commit=str(payload.get("git_commit") or ""),
            recorded_at=str(payload.get("recorded_at") or ""),
            pins=RunPins.from_dict(payload.get("pins") or {}),
            trace=payload.get("trace") or {},
            source_refs=list(payload.get("source_refs") or []),
        )

    @classmethod
    def read(cls, path: Path) -> Recording:
        return cls.from_dict(json.loads(path.read_text()))

    def refuse_a_fixture(self) -> None:
        """Reject a recording no model produced.

        `ScriptedRuntime` reports `{"runtime": "scripted", "model": None}`
        precisely so this check can exist (hy-pqf3). Without it the cheapest
        route to a passing benchmark is a committed script that calls the right
        tools in the right order -- a trace that looks exactly like a good run
        and is a statement about nothing.

        Refusing only the HONEST fake is not the check it reads as, which is
        why the runtime is gated against `RUNTIME_NAMES` rather than compared
        to one name (hy-puiu): `totally-made-up`, `gpt-5-by-hand` and `' '`
        were all measured to pass here, so a hand-written trace naming no
        adapter at all was accepted as evidence about a model.
        """
        runtime = (self.trace.get("provenance") or {}).get("runtime")
        if runtime == SCRIPTED_RUNTIME:
            raise UnreadableRecording(
                f"{self.case_id!r} was produced by the scripted runtime, so no model ran; a "
                "fixture is not evidence about a model and must not be scored as one"
            )
        if runtime not in RUNTIME_NAMES:
            raise UnreadableRecording(
                f"{self.case_id!r} names runtime {runtime!r}, which no adapter in this repository "
                f"reports -- the runtimes that exist are {', '.join(RUNTIME_NAMES)}; a trace "
                "attributed to nothing cannot be told from a fabricated one"
            )

    def refuse_a_mismatched_record(self) -> None:
        """Reject a trace that disagrees with what the adapter says it ran.

        The loop records the prompt and tool declarations it was HANDED; the
        adapter reports in `provenance()` the ones it actually drove the model
        with. Handing the two different pairs is the one way a benchmark can
        produce an internally consistent record of a run that did not happen --
        a raw-arm run wearing the governed arm's prompt hash, which every pin
        check downstream would then confirm. `hyperset.evals.arms` makes that
        mistake hard by building the pair once; this makes it detectable.

        An ABSENT hash is refused the way `PinsIncomplete` refuses an
        unrecorded host pin. The earlier silence was justified by
        `ScriptedRuntime` reporting neither, and that reasoning inverts
        (hy-puiu): the scripted runtime is refused one check earlier, so the
        only runtime still reaching here is one that DOES report both, and the
        silent branch protected nothing but a hand-written trace.
        """
        provenance = self.trace.get("provenance") or {}
        pairs = (("prompt_hash", "instructions_hash"), ("tools_hash", "tools_hash"))
        unreported = [
            reported for _, reported in pairs if not str(provenance.get(reported) or "").strip()
        ]
        if unreported:
            raise UnreadableRecording(
                f"{self.case_id!r} carries a runtime that reported no "
                f"{', '.join(sorted(set(unreported)))}; an adapter that drove a model reports "
                "both, and a hash nobody wrote down must not read as a hash that matched"
            )
        for recorded, reported in pairs:
            claimed = provenance.get(reported)
            if self.trace.get(recorded) != claimed:
                raise UnreadableRecording(
                    f"{self.case_id!r} records {recorded}={self.trace.get(recorded)!r} while the "
                    f"runtime reports it drove the model with {claimed!r}; the record describes a "
                    "run that did not happen"
                )

    def refuse_unrecorded_host_pins(self) -> None:
        """Reject a recording that pinned no host identity.

        The generic pin-COMPLETENESS half of the pin check (hy-myn6, #400):
        `digest`, `quantization` and `ollama_version` must be non-empty. A
        customer runner cannot run the full `assert_pins` -- that compares
        `REPOSITORY_PINS` to the #25 arm spec and would reject a customer's own
        recording -- but it must still refuse one whose host pins are blank, or a
        recording that pinned nothing would reach scoring wearing no host
        identity. This is that half, with no arm comparison.
        """
        assert_host_pins_recorded(self.pins)

    def refuse_a_different_exam(self) -> None:
        """Reject a recording of questions this repository no longer asks.

        The half of the freeze no pin can see (hy-j3ms). `task_version()`
        content-addresses the case file and every recording already carries the
        value; nothing compared them, so weakening `revenue_by_region` to an
        empty `must_state` and `must_cite` and scoring the UNCHANGED recordings
        was measured to tie both arms at 4/4 shared with a green gate. The
        prompt and the tool hashes are untouched by a case edit, which is
        exactly why they cannot catch one.
        """
        # PER-SUITE: the recording's own value names the suite it sat
        # (`<suite>@<hash>`), so a revenue recording is checked against
        # revenue.yaml and a billing recording against billing.yaml -- adding a
        # second suite never changes what an existing recording is verified
        # against (hy-esp). A recording whose suite file has since been removed
        # is a recording of an exam this repository no longer holds, and is
        # refused as such rather than silently kept.
        suite = self.task_version.split("@", 1)[0]
        try:
            expected = task_version(suite)
        except FileNotFoundError:
            raise UnreadableRecording(
                f"{self.case_id!r} answered suite {suite!r}, which this repository no longer "
                "defines; a recording of a removed exam cannot be scored against the current one"
            ) from None
        if self.task_version != expected:
            raise UnreadableRecording(
                f"{self.case_id!r} answered exam {self.task_version!r} and this commit asks "
                f"{expected!r}; an edited case scores yesterday's answers against today's "
                "questions, which is how the exam gets loosened without a pin moving"
            )

    def verify(self) -> None:
        """Everything that must hold before a score means anything.

        Ordered deliberately: what produced it, then whether it describes what
        it ran, then which exam it sat, then what it was pinned to. A fixture
        that also drifted should be reported as a fixture, because re-recording
        it is not the fix.
        """
        self.refuse_a_fixture()
        self.refuse_a_mismatched_record()
        self.refuse_a_different_exam()
        assert_pins(self.pins, arm=self.arm)


def _read_schema_1_run_id(payload: dict) -> Unidentified:
    """The run id of the frozen corpus recorded before runs were told apart.

    A reader written DELIBERATELY for one older version is not the thing
    `from_dict`'s strict equality was protecting against: that refusal exists
    because scoring an unknown shape would score the fields it recognises and
    ignore the rest, and this knows exactly which field schema 1 lacks and what
    its absence means. Every other field of schema 1 is schema 2's, unchanged.

    IT READS SCHEMA 1 AND NOTHING ELSE. If a schema 3 ever wants to come through
    here, that is the signal to stop and rule again rather than to add a branch;
    a second version in this function is how a narrow reader becomes the
    compatibility layer the strict check was written to prevent (hy-l13a
    deletes this one outright once the corpus is re-recorded).
    """
    return Unidentified()


def _read_schema_2_run_id(payload: dict) -> str:
    """The run id a schema-2 recording must carry, or a refusal.

    Absent is refused rather than defaulted, the way `PinsIncomplete` refuses an
    unrecorded host pin: a field nobody wrote down must not read as one that
    matched, and a run id defaulted to the sentinel here would let a schema-2
    producer that forgot to write one look exactly like the frozen corpus.
    """
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise UnreadableRecording(
            f"schema {RECORDING_SCHEMA_VERSION} recording carries no run_id; every run this "
            "repository records mints one, so an absent id means the writer is not the recorder "
            "-- and defaulting it would make an unwritten id read as a matching one"
        )
    return run_id


_READERS = {1: _read_schema_1_run_id, 2: _read_schema_2_run_id}
"""Version to the one field that differs between them.

A dispatch table rather than an `if version == 1` chain, so a version nobody
wrote a reader for is a `KeyError` at the door instead of falling through to the
newest reader -- the same reason `refuse_a_fixture` gates the runtime against
`RUNTIME_NAMES` rather than comparing it to one name."""
