"""What repeating a case says about the harness, reported and never gated (#25, hy-2beh).

GitHub #25's acceptance includes "repeated local runs and scorers are stable",
and the sharper reading is the one this module implements: the harness REPORTS
its stability rather than asserts it. Nothing here computes a threshold, and
nothing here can fail a build. A flapping predicate becomes VISIBLE; acting on
one is a human decision made with the disagreeing answers in hand.

WHAT THE REPETITIONS HOLD FIXED, which decides what the number means. Every
repetition runs the same commit, the same case file, the same prompt hash, the
same tool-schema hash, the same model digest, the same seed and temperature --
`RunPins` in full plus the two pins that live on the `Recording`, `git_commit`
and `task_version` -- and a drift in any of them raises `PinsDrifted` rather
than averaging two different runs into one report. So what this measures is
whether an identically pinned run reproduces.

WHAT "REPRODUCES" MEANS HERE, bounded because the wider phrasing was false.
The comparison is over predicate VERDICTS, ANSWER TEXT and the evidence -- at
both identities below -- and nothing else. `trace` is recorded per repetition
and, apart from the evidence entries derived out of it, never compared, so
two repetitions that reached the same answer through a different tool sequence
read as fully stable -- measured, and hy-9dyv is the bead for the projection
that would compare it. Do not read `flapping=0` as a statement about the tools,
the database or the trace.

The EVIDENCE half of that bound closed in hy-jy2h: a repetition that rested on
different sources was invisible behind `flapping=0 answers_distinct=1`, and
`source_refs_distinct` is that number. It compares SETS -- normalised, so ref
order and a repeated ref are not instability -- because what a run rested on is
the question, and the order the tools returned it in is hy-9dyv's.

THAT NUMBER IDENTIFIES AN ASSET AND NEVER AN ASSET VERSION, which is hy-o79s
and the reason there is a second one. A re-observation of one source between
two repetitions -- new `observed_version`, new `content_sha256`, the same ref
-- is the same set, so `source_refs_distinct=1` says identical evidence about
two repetitions that read different bytes. It is not a labelling problem: the
governed context is read from the store per repetition, so a connector run or
an approval landing mid-record gives repetition 2 a different bundle, the
report prints the flap, and the reader blames the model. `source_versions_
distinct` is that finer identity.

BOTH COUNTS COME OFF ONE WALK OF ONE PAYLOAD, which is what makes them
comparable at all (hy-szg4). `stability_report` calls
`source_identity.observed_entries` once per recording and each `Repetition`
holds that list, so the ref and the version projections are two views of one
set rather than two answers to one question. The earlier arrangement took refs
from the PERSISTED `Recording.source_refs` and versions from a re-derived walk,
and a probe printed `source_refs_distinct=2 source_versions_distinct=1` from
it -- the refinement inverted, and two numbers on one line that nothing made
comparable. The persisted field is NOT redefined: GitHub #25 requires a
recording to carry it, so it is compared against the walk instead, and a
recording that disagrees with its own trace raises `EvidenceMismatch` rather
than being quietly re-derived.

The two counts are therefore a REFINEMENT and not a second opinion, and the
refinement holds BY TYPE (hy-q2mn). Both projections group on the same walked
list: the coarse one keyed by `ref`, the finer one by the whole `(ref,
identity)` PAIR. A ref set is the image of a pair set under `ref`, so the map
from version groups to ref groups is well defined and onto and versions can
never be fewer than refs. The world the pair exists for is
`source_refs_distinct=1 source_versions_distinct=2` -- one asset, two versions
of it, which nothing else on the line can say.

THAT CLAIM WAS FALSE BEFORE IT WAS TRUE, and the reason is worth keeping. The
finer projection used to be a set of `f"{ref}@{identity}"` STRINGS, and that
join is not injective: both halves are arbitrary payload strings, so `'@'` is
legal in either and `('dataset:a@b', 'c')` and `('dataset:a', 'b@c')` were one
key. Two repetitions holding those printed `source_refs_distinct=2
source_versions_distinct=1` and rendered `EVIDENCE 2 distinct sets across 2`
directly above `VERSIONS identical across 2 repetitions` -- hy-szg4's own
finding arriving through the DELIMITER instead of through two sources.
Establishing it BY TYPE rather than by failing to find a counterexample is the
point: the earlier arrangement also had a test asserting versions can never be
fewer, and that test was asserting an invariant the code did not have, which is
worse than no test because it retires the question. The join now happens in
`_render_versions`, after every count is taken.

THE BOUND ON THAT, stated so it is neither dressed up nor dismissed: the
identity must contain `'@'` for the collision to fire, and this repository's
producers never write one -- the governed identity is a store `content_sha256`,
the raw arm's is a `sha256:`-prefixed hash, and all four committed recordings
walk to entries with `'@'` in neither half. `EvidenceMismatch` does not catch it
either, because that compares refs only: a doctored `content_sha256` beside
honest refs is ACCEPTED and prints `source_refs_distinct=1
source_versions_distinct=2`. So what was defective was the universal claim and
the test's invariant, not any number this repository has produced.

A VERSION IDENTITY CAN BE ABSENT, and an absent one must not read as agreement
(hy-szg4). `content_sha256` is `None` for an asset the store holds with no
current version, that entry is named `ref@unversioned`, and a repetition whose
evidence is entirely unversioned would otherwise print `VERSIONS identical`
with a hash beside it -- the same defect as a secret scan reporting "passed"
without its inputs (hy-jnem), in a different module. So `unversioned=` is on
the line, `render` says `vacuous` rather than `identical` when no entry carried
a version, and the refs that carried none are named. There is nothing finer to
promote in that world: `observed_version` is read off the same
`asset.current_version` the hash is, so it is `None` in the same payload.

WHAT THE FINER IDENTITY STILL DOES NOT SEE, because it walks exactly the
payload `source_refs` walks: an asset named under any key other than
`linked_evidence.observed_assets[].ref` or the raw arm's `GET_RAW_ASSET`
`external_id` contributes no ref, and therefore no version -- that repetition
records the empty set and prints `<none>`. On the raw arm the identity is a
content hash of the `raw_payload` that tool returned, which is VERSION-LEVEL
WITH THE STORE'S NARROWING rather than byte-level: the tool serves
`asset.current_version.raw_payload`, and the store writes a new version only
when the hash over the `hash_basis`-NARROWED payload moves, so a re-sync that
touches only Superset's `*_humanized` times or reorders a DataHub
`customProperties` map changes no version, no bytes and neither count. See
`source_identity.observed_entries` for the per-surface bound.

WHAT IT DOES NOT MEASURE, stated because the adjacent question is the more
famous one: roll-to-roll variance. hy-hk5m measured that a cosmetic prompt or
tool-schema edit moves `prompt_hash`/`tools_hash` and re-rolls the arm, so the
headline is a property of one byte-string. Repeating with those hashes VARIED
would measure the re-roll, not the substrate, and this module deliberately does
the opposite: it pins them and says so on the line. hy-hk5m stays open.

AN AGREEMENT PERCENTAGE ALONE IS NOT ACTIONABLE, so `render` prints the exact
answers that differed. A report that says "5/7 predicates unanimous" and throws
away what the disagreeing repetition said leaves the reader with a number and
no way to act on it, which is the defect one layer up from the gate's evidence
line (hy-y91y).
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from hyperset.evals.cases import Case
from hyperset.evals.pins import RunPins
from hyperset.evals.recording import Recording
from hyperset.evals.scorers import Score, said, score
from hyperset.evals.source_identity import UNVERSIONED, observed_entries
from hyperset.evals.trace_shape import render_shape, trace_shape
from hyperset.planner.trace import content_hash

STABILITY_LINE_NAME = "HYPERSET-STABILITY"
STABILITY_LINE_VERSION = "v2"
"""v1 counted; v2 also IDENTIFIES (hy-hk5m).

Bumped on both of the standing reasons at once: v2 carries always-present keys
v1 does not -- an identity per axis and the trace projection -- and it narrows
what the line claims, because `flapping=0` on a v2 line is a statement about a
trace shape as well. A reader holding a v1 line has neither, and a cross-session
comparator that treated the absent fields as agreement would answer "stable"
about an axis nobody measured, so `parse_stability_line` refuses a v1 line by
its version rather than by a missing key.
"""

STABILITY_LINE_PREFIX = f"{STABILITY_LINE_NAME} {STABILITY_LINE_VERSION}"

_Key = TypeVar("_Key", str, tuple[str, str])
"""What an evidence set may be grouped on: a ref, or a `(ref, identity)` pair.

Constrained rather than open so the two projections cannot drift apart in what
they compare, and so no third key -- a joined token especially -- can be
introduced without changing this line."""

RESULT = "REPORT-ONLY"
"""On the line itself, every time. A number that looks like a gate's eventually
gets treated as one, and the ruling on hy-2beh is that this reports until there
is data about what stable looks like here."""

DEFAULT_REPETITIONS = 3
"""Three, not two, and not five.

Two only answers "did they agree"; it cannot tell a predicate that flapped once
from one that alternates, and both are single disagreements in a set of two.
Three is the smallest N where a minority verdict is distinguishable from a
split, which is what makes the report actionable rather than a coin-flip
notification.

Five is what the cost forbids: ADR 0013 measured one happy-path run of the
pinned model at 314 seconds CPU-only, so N=3 across two arms and two cases is
roughly an hour of inference and N=5 is nearly two. This runs in the SCHEDULED
job only -- never on a pull request -- and `benchmark-live.yml` has a 330
minute budget to spend."""

REPETITIONS_ENV = "HYPERSET_STABILITY_REPETITIONS"

PASS = "pass"
FAIL = "fail"
ABSENT = "absent"
"""A predicate one repetition produced and another did not.

Scorers return `None` for a predicate that does not apply to a case, so a
verdict set that changes between identically pinned repetitions is itself
instability -- and it is a kind a pass/fail tally would hide by silently
comparing different denominators."""


class PinsDrifted(RuntimeError):
    """Two repetitions in one report did not run under the same pins.

    Fatal rather than a footnote: repetitions whose pins differ measure the
    difference, and a report that averaged them would answer a question nobody
    asked with a number that looks like the answer to this one.

    "Pins" here is `RunPins` plus `RECORDING_PINS`, because two of them are
    fields of the `Recording` rather than of `RunPins` and drift the same way.
    """


RECORDING_PINS = ("git_commit", "task_version")
"""The two pins that sit on the `Recording` instead of inside `RunPins`.

`task_version` is re-read on EVERY repetition -- it content-hashes the case file
at call time -- and `git_commit` used to be, from `git rev-parse HEAD` in a
subprocess inside `run_case`. So an hour-long local re-record that spans a `git
checkout` produced three commits in one report, and the line named repetition
0's -- which is precisely what `MixedRepetitions` exists to prevent, a line
false about its own inputs.

`git_commit` is now the session's, observed once and re-checked per case by
`hyperset.evals.provenance` (hy-r1i0), so a moving HEAD is refused one layer
earlier and this comparison should never be what catches it. It is kept anyway,
and not because it is free: this reader takes recordings from anywhere, and the
comparison is what holds a caller that stamps its own `git_commit` -- including
every recording already committed at a commit that moved.

The measurement itself survives that drift, because the interpreter imported
`hyperset` at process start and the repetitions execute the code that was
loaded then. What drifts is the LABEL, plus one artifact: under
`HYPERSET_RECORD=1` a `task_version` drift would write repetition 0's answer to
exam A into a tree that now asks exam B, an hour of inference landing on a
recording `refuse_a_different_exam` refuses later."""


def _refuse_drift(drifted: Mapping[str, tuple[object, object]], *, index: int) -> None:
    """Raise `PinsDrifted` naming every field that moved, or return."""
    if not drifted:
        return
    rendered = "; ".join(
        f"{field}: repetition 0 {expected!r}, repetition {index} {got!r}"
        for field, (expected, got) in sorted(drifted.items())
    )
    raise PinsDrifted(
        f"repetitions in one stability report must run under identical pins -- {rendered}"
    )


def _render_refs(refs: Sequence[str]) -> str:
    """The refs themselves, and a visible marker for the empty set.

    Printed in full even when every repetition agreed, where an identical ANSWER
    is reduced to a hash: a ref set is one short line, and two reports that each
    held steady on DIFFERENT sources are not the same result -- which
    `source_refs_distinct=1` on its own cannot say.
    """
    return ", ".join(refs) if refs else "<none>"


def _render_versions(entries: Sequence[tuple[str, str]]) -> str:
    """The pairs as `ref@identity`, for a HUMAN and for nothing else (hy-q2mn).

    The only place the two halves are ever joined. `'@'` is legal in both, so
    the token is ambiguous in principle -- `('a@b', 'c')` and `('a', 'b@c')`
    render alike -- and that is tolerable here and nowhere else: this string is
    read, never counted, compared or grouped on. Counting it was the defect.
    """
    return _render_refs([f"{ref}@{identity}" for ref, identity in entries])


def _group(
    sets: Iterable[tuple[int, Sequence[_Key]]],
) -> tuple[tuple[tuple[_Key, ...], tuple[int, ...]], ...]:
    """Distinct normalised sets, each with the repetitions that produced it.

    Normalised by `sorted(set(...))`, so the same entries in another order or
    an entry returned twice is one set: the question is what the run rested on,
    and the order the tools returned it in is hy-9dyv's. Shared by the ref and
    the version projections because two counts on one line that normalised
    differently would not be comparable to each other.

    GENERIC IN THE KEY, and that is what makes the refinement structural
    (hy-q2mn). The ref projection groups on `str` and the version projection on
    the `(ref, identity)` PAIR, so the version key carries the ref key inside it
    and no encoding step can lose a distinction the pair holds. Grouping the
    versions on a joined `ref@identity` string was the defect: `'@'` is legal in
    both halves, so two distinct pairs collapsed into one key and the finer
    count came out SMALLER than the coarse one.
    """
    grouped: dict[tuple[_Key, ...], list[int]] = {}
    for index, entries in sets:
        grouped.setdefault(tuple(sorted(set(entries))), []).append(index)
    return tuple((entries, tuple(indexes)) for entries, indexes in grouped.items())


def _identity(values: Iterable[str]) -> str:
    """One axis's whole observed content, as one comparable token.

    THE CANONICALISER, and therefore the place a false agreement would hide
    (hy-wwk3). It sorts and de-duplicates -- a session is a SET of behaviours on
    each axis, and two sessions that saw the same two answers in the other order
    saw the same thing -- and it does NOTHING ELSE. It does not strip, case-fold,
    collapse whitespace or truncate, because every one of those makes two
    genuinely different observations identical, and an identity that agrees more
    often than the content does is worse than no identity: it reports agreement
    with the authority of a hash.

    EACH VALUE IS HASHED BEFORE ANYTHING IS JOINED, which is hy-q2mn's lesson
    applied one module over. Concatenating the values under any separator is a
    map that is not injective while a value may contain that separator, and an
    ANSWER contains newlines by construction -- a session whose one answer is
    `"A\\nB"` and a session whose two answers are `"A"` and `"B"` would hash
    alike, which is a false AGREEMENT arriving through the delimiter. A content
    hash is fixed-length and holds no separator, so the join over hashes cannot
    lose a boundary. De-duplicating on the hash is de-duplicating on the
    content, since equal content hashes alike.
    """
    return content_hash("\n".join(sorted({content_hash(value) for value in values})))


def configured_repetitions(environ: Mapping[str, str]) -> int:
    """`HYPERSET_STABILITY_REPETITIONS`, or the default, and never below one."""
    raw = (environ.get(REPETITIONS_ENV) or "").strip()
    if not raw:
        return DEFAULT_REPETITIONS
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{REPETITIONS_ENV}={raw!r} is not an integer") from error
    if value < 1:
        raise ValueError(f"{REPETITIONS_ENV}={raw!r}: a run repeated fewer than once is not a run")
    return value


@dataclass(frozen=True)
class Repetition:
    """One scored run of one case on one arm, kept whole.

    The answer is held beside the scores because the predicates are lexical:
    two answers that differ in wording can score identically, and the report
    that only compared verdicts would call that stable.
    """

    index: int
    pins: RunPins
    scores: tuple[Score, ...]
    answer: str
    trace_shape: tuple[str, ...]
    """What this repetition DID, in call order, as `trace_shape` projects it
    (hy-9dyv).

    ORDERED, and therefore not grouped by `_group`, which normalises with
    `sorted(set(...))`. The two questions this axis exists for are whether the
    catalog came before the resolve and whether validate was called at all, and
    a set answers the first wrong and drops a repeated call. Kept beside the
    scores for the same reason the answer is: hy-hk5m measured one session that
    validated and two that never did, at identical pins, agreeing on every
    number the v1 line could print."""
    source_entries: tuple[tuple[str, str], ...]
    """This repetition's evidence as `source_identity.observed_entries` walked
    it: one `(ref, version identity)` pair per observed asset, unnormalised and
    in trace order.

    ONE FIELD RATHER THAN TWO, which is the fix for hy-szg4. Both projections
    are views of this list, so a repetition whose refs and versions describe
    different sets is not constructible -- not by this module, not by a test,
    not by a later refactor that re-derived one of them. The previous shape held
    the two tuples side by side and a probe built `source_refs_distinct=2
    source_versions_distinct=1` out of it, which the refinement the report
    prints says is impossible.

    UNJOINED, which is the fix for hy-q2mn. One walk was not sufficient: the
    version projection then ENCODED each pair as `f"{ref}@{identity}"` and
    grouped on the string, and since `'@'` is legal in both halves that encoding
    lost distinctions this list holds -- reproducing the inversion at the same
    numbers, out of one field. Callers group on the pairs; only
    `_render_versions` joins them.

    Required rather than defaulted: the empty set is a real value a run can
    have, so a default would make a repetition constructed without its evidence
    indistinguishable from one that rested on none. The committed recordings
    carry four refs (governed) and two (raw baseline)."""

    @property
    def source_refs(self) -> tuple[str, ...]:
        """The assets, in walk order and with repeats kept, as the recording
        persists them."""
        return tuple(ref for ref, _ in self.source_entries)

    @property
    def unversioned_refs(self) -> tuple[str, ...]:
        """The refs this repetition read with no version identity beside them."""
        return tuple(ref for ref, identity in self.source_entries if identity == UNVERSIONED)

    def verdict(self, predicate: str) -> str:
        for entry in self.scores:
            if entry.predicate == predicate:
                return PASS if entry.passed else FAIL
        return ABSENT

    def explanation(self, predicate: str) -> str:
        for entry in self.scores:
            if entry.predicate == predicate:
                return entry.explanation
        return "the scorers produced no verdict for this predicate in this repetition"


@dataclass(frozen=True)
class PredicateAgreement:
    """One predicate's verdicts across every repetition, in order."""

    predicate: str
    verdicts: tuple[str, ...]
    explanations: tuple[str, ...]

    @property
    def unanimous(self) -> bool:
        return len(set(self.verdicts)) == 1


@dataclass(frozen=True)
class StabilityReport:
    """N identically pinned runs of one case on one arm, compared."""

    arm: str
    case_id: str
    git_commit: str
    repetitions: tuple[Repetition, ...]

    def __post_init__(self) -> None:
        if not self.repetitions:
            raise ValueError("a stability report over zero repetitions reports nothing")
        first = self.repetitions[0].pins.to_dict()
        for repetition in self.repetitions[1:]:
            actual = repetition.pins.to_dict()
            _refuse_drift(
                {
                    field: (first[field], actual[field])
                    for field in first
                    if first[field] != actual[field]
                },
                index=repetition.index,
            )

    @property
    def pins(self) -> RunPins:
        return self.repetitions[0].pins

    def agreements(self) -> tuple[PredicateAgreement, ...]:
        """Every predicate any repetition produced, in first-seen order."""
        names: list[str] = []
        for repetition in self.repetitions:
            for entry in repetition.scores:
                if entry.predicate not in names:
                    names.append(entry.predicate)
        return tuple(
            PredicateAgreement(
                predicate=name,
                verdicts=tuple(repetition.verdict(name) for repetition in self.repetitions),
                explanations=tuple(repetition.explanation(name) for repetition in self.repetitions),
            )
            for name in names
        )

    def flapping(self) -> tuple[PredicateAgreement, ...]:
        return tuple(entry for entry in self.agreements() if not entry.unanimous)

    def distinct_answers(self) -> tuple[tuple[str, tuple[int, ...]], ...]:
        """Each distinct answer text with the repetitions that produced it."""
        grouped: dict[str, list[int]] = {}
        for repetition in self.repetitions:
            grouped.setdefault(repetition.answer, []).append(repetition.index)
        return tuple((answer, tuple(indexes)) for answer, indexes in grouped.items())

    def distinct_source_refs(self) -> tuple[tuple[tuple[str, ...], tuple[int, ...]], ...]:
        """Each distinct evidence SET with the repetitions that rested on it.

        `_group` normalises, so the same refs in another order or a ref returned
        twice is one set: the question is what the run rested on. Comparing the
        raw lists instead would report tool-call ordering as evidence drift,
        which is hy-9dyv's question and not this field's.
        """
        return _group((repetition.index, repetition.source_refs) for repetition in self.repetitions)

    def distinct_source_versions(
        self,
    ) -> tuple[tuple[tuple[tuple[str, str], ...], tuple[int, ...]], ...]:
        """Each distinct evidence set keyed by VERSION, with its repetitions.

        Sets of `(ref, identity)` PAIRS, normalised by the same `_group`, so the
        only difference from `distinct_source_refs` is the identity itself: two
        repetitions that rested on one ref at two `content_sha256` values are one
        set there and two here (hy-o79s).

        THE PAIR IS THE KEY, which is what makes this a refinement of
        `distinct_source_refs` rather than an independent number (hy-q2mn). Each
        ref key is the image of a pair key under `ref`, so the map from these
        groups to those is well defined and onto: this count can never be the
        smaller. Keyed on a joined `ref@identity` string it could be, and was --
        `'@'` is legal in both halves, so `('dataset:a@b', 'c')` and
        `('dataset:a', 'b@c')` were one key, and two repetitions holding those
        printed `source_refs_distinct=2 source_versions_distinct=1`. Joining
        happens in `_render_versions` and after every count is taken.
        """
        return _group(
            (repetition.index, repetition.source_entries) for repetition in self.repetitions
        )

    def distinct_trace_shapes(self) -> tuple[tuple[tuple[str, ...], tuple[int, ...]], ...]:
        """Each distinct trace shape with the repetitions that produced it.

        Grouped on the SEQUENCE and deliberately not through `_group`: that
        helper normalises every set it is handed with `sorted(set(...))`, which
        is right for evidence and wrong here. `catalog>resolve` and
        `resolve>catalog` are two behaviours, a call made twice is not a call
        made once, and both distinctions die under a set. Sharing the helper
        would have reported hy-hk5m's observed flip -- validate called, then not
        called -- as agreement, which is the failure this axis exists to end.
        """
        grouped: dict[tuple[str, ...], list[int]] = {}
        for repetition in self.repetitions:
            grouped.setdefault(repetition.trace_shape, []).append(repetition.index)
        return tuple((shape, tuple(indexes)) for shape, indexes in grouped.items())

    def verdicts_field(self) -> str:
        """Every predicate with the verdicts it took, as one legible token.

        LEGIBLE RATHER THAN HASHED, unlike the answer and the evidence
        identities, and the asymmetry is deliberate (hy-wwk3). This is the axis
        the benchmark is about: a reader comparing two sessions has to see WHICH
        predicate moved, and two hashes only say THAT something did. It is
        bounded -- one short name per predicate -- where an answer is not.

        Each predicate's verdicts are sorted and de-duplicated, so a predicate
        that flapped prints `pass|fail` in both sessions that flapped it: the
        question across sessions is which verdicts were observed, not which
        repetition index produced them.
        """
        return ",".join(
            f"{entry.predicate}:{'|'.join(sorted(set(entry.verdicts)))}"
            for entry in sorted(self.agreements(), key=lambda entry: entry.predicate)
        )

    def traces_field(self) -> str:
        """Every distinct shape this session took, as one legible token.

        Legible for the reason the verdicts are, and this axis most of all: the
        finding hy-9dyv was filed on is "validate was not called", and a reader
        must be able to see that in the line rather than derive it from a hash
        that differs. Shapes are sorted so the token identifies the SET of
        behaviours a session showed and not the order the repetitions ran in.
        """
        return ",".join(sorted(render_shape(shape) for shape, _ in self.distinct_trace_shapes()))

    def answers_identity(self) -> str:
        """Every distinct answer this session produced, as one token.

        HASHED, where the verdicts and the shapes are printed: an answer is
        unbounded prose and several of them do not belong on a pasteable line.
        `render` prints the answers themselves whenever they disagreed, so the
        content is one scroll away rather than lost.
        """
        return _identity(answer for answer, _ in self.distinct_answers())

    def evidence_identity(self) -> str:
        """Every distinct evidence SET this session rested on, as one token.

        Over the REF sets, the asset identity, and not over the `(ref,
        identity)` pairs. Across sessions a version identity moves whenever the
        store re-observes an asset, which is a fact about the corpus timeline
        and not about the arm's behaviour, so a comparator keyed on it would
        report DISAGREE for a connector run. The finer identity stays on the
        line as `source_versions_distinct` and in `render`, where a reader can
        see it without it deciding a comparison.

        NESTED rather than rendered-then-hashed: the inner call identifies one
        SET of refs and the outer identifies the collection of sets, so a ref
        holding whatever separator a rendering would have used cannot merge two
        sets into one. `_render_refs` joins with `", "`, and `{"a, b"}` and
        `{"a", "b"}` render alike -- the delimiter collision hy-q2mn closed, in
        a fresh place.
        """
        return _identity(_identity(refs) for refs, _ in self.distinct_source_refs())

    def unversioned_refs(self) -> tuple[str, ...]:
        """Every ref any repetition read without a version identity (hy-szg4).

        The measure of how much of the version comparison was vacuous. A ref
        counted here cannot show a re-observation at either identity, so a
        report where this equals the ref count agreed about nothing -- and
        `source_versions_distinct=1` on its own reads as agreement.
        """
        return tuple(
            sorted({ref for repetition in self.repetitions for ref in repetition.unversioned_refs})
        )

    def line(self) -> str:
        """One pasteable line naming its own inputs, as the gate's line does.

        Two stability reports that cannot be compared reproduce the defect the
        gate line closed one layer up (hy-y91y), so the pins that decide the
        roll are ON the line rather than in a paragraph above it.
        """
        pins = self.pins
        agreements = self.agreements()
        fields = [
            STABILITY_LINE_PREFIX,
            f"sha={self.git_commit}",
            f"arm={self.arm}",
            f"case={self.case_id}",
            f"n={len(self.repetitions)}",
            f"model={pins.model}",
            f"digest={pins.digest}",
            # The host pin the digest does NOT determine (hy-a1i0). Two sessions
            # weeks apart run on two Ollama builds, and without this field their
            # lines are byte-identical -- measured -- so the comparator called
            # them comparable while the same drift between two repetitions of
            # one session is refused by name.
            f"ollama_version={pins.ollama_version}",
            f"context_window={pins.context_window}",
            f"seed={pins.seed}",
            f"temperature={pins.temperature}",
            f"prompt_hash={pins.prompt_hash}",
            f"tools_hash={pins.tools_hash}",
            f"predicates={len(agreements)}",
            f"unanimous={sum(1 for entry in agreements if entry.unanimous)}",
            f"flapping={len(self.flapping())}",
            f"answers_distinct={len(self.distinct_answers())}",
            f"source_refs_distinct={len(self.distinct_source_refs())}",
            f"source_versions_distinct={len(self.distinct_source_versions())}",
            f"unversioned={len(self.unversioned_refs())}",
            f"trace_shapes_distinct={len(self.distinct_trace_shapes())}",
            # The four IDENTITY fields, which are what a second session can be
            # compared against (hy-hk5m). Every count above answers "did THIS
            # session hold together"; two sessions that each held together on
            # different content print the same counts, which is measured -- see
            # `cross_session`. No field here contains a space, so the line stays
            # one `shlex` token per field.
            f"verdicts={self.verdicts_field()}",
            f"traces={self.traces_field()}",
            f"answers_id={self.answers_identity()}",
            f"evidence_id={self.evidence_identity()}",
            f"result={RESULT}",
        ]
        return " ".join(fields)

    def render(self) -> str:
        """The line, then everything a reader would otherwise have to ask for."""
        out = [self.line()]
        flapping = self.flapping()
        if not flapping:
            out.append(f"  every predicate agreed across {len(self.repetitions)} repetitions")
        for entry in flapping:
            out.append(f"  FLAP {entry.predicate}: {','.join(entry.verdicts)}")
            for index, repetition in enumerate(self.repetitions):
                out.append(
                    f"    rep {repetition.index} {entry.verdicts[index].upper()}: "
                    f"{entry.explanations[index]}"
                )
        answers = self.distinct_answers()
        if len(answers) == 1:
            out.append(
                f"  ANSWERS identical across {len(self.repetitions)} repetitions "
                f"({content_hash(answers[0][0])})"
            )
        else:
            out.append(f"  ANSWERS {len(answers)} distinct across {len(self.repetitions)}:")
            for answer, indexes in answers:
                joined = ",".join(str(index) for index in indexes)
                out.append(f"    reps {joined} ({content_hash(answer)}):")
                out.append(f"      {answer}")
        shapes = self.distinct_trace_shapes()
        if len(shapes) == 1:
            out.append(
                f"  TRACE identical across {len(self.repetitions)} repetitions: "
                f"{render_shape(shapes[0][0])}"
            )
        else:
            # Named in full in both branches, as the refs are: a shape is one
            # short token, and two sessions that each held steady on DIFFERENT
            # shapes -- one validating, one not -- are not the same result, which
            # `trace_shapes_distinct=1` on its own cannot say.
            out.append(f"  TRACE {len(shapes)} distinct shapes across {len(self.repetitions)}:")
            for shape, indexes in shapes:
                joined = ",".join(str(index) for index in indexes)
                out.append(f"    reps {joined}: {render_shape(shape)}")
        evidence = self.distinct_source_refs()
        if len(evidence) == 1:
            out.append(
                f"  EVIDENCE identical across {len(self.repetitions)} repetitions: "
                f"{_render_refs(evidence[0][0])}"
            )
        else:
            out.append(f"  EVIDENCE {len(evidence)} distinct sets across {len(self.repetitions)}:")
            for refs, indexes in evidence:
                joined = ",".join(str(index) for index in indexes)
                out.append(f"    reps {joined}: {_render_refs(refs)}")
        versions = self.distinct_source_versions()
        if len(versions) > 1:
            out.append(f"  VERSIONS {len(versions)} distinct sets across {len(self.repetitions)}:")
            for entries, indexes in versions:
                joined = ",".join(str(index) for index in indexes)
                out.append(f"    reps {joined}: {_render_versions(entries)}")
        elif not versions[0][0]:
            # Not `identical`, and not a hash: `content_hash("<none>")` is a
            # 64-character token that reads like the identity of something, and
            # what it identifies is the absence of any evidence to compare
            # (hy-szg4).
            out.append(
                f"  VERSIONS none across {len(self.repetitions)} repetitions: no evidence entry "
                "carried a version, so this comparison was vacuous rather than agreement"
            )
        elif all(identity == UNVERSIONED for _, identity in versions[0][0]):
            # The IDENTITY, not a suffix of the rendered token (hy-q2mn). This
            # sentence and `unversioned=` on the line are two readings of one
            # fact, and `unversioned_refs` compares `identity == UNVERSIONED`, so
            # a suffix test here let them disagree: an identity that merely ENDS
            # IN the marker -- `b@unversioned` -- produced a report saying
            # `unversioned=0` above prose claiming every entry was unversioned.
            # A line and its own prose contradicting each other is the defect
            # hy-y91y closed one layer up, so both halves read the same field.
            out.append(
                f"  VERSIONS vacuous across {len(self.repetitions)} repetitions: every entry the "
                "repetitions read is unversioned, so one set here is the absence of an identity "
                "and not agreement about the bytes"
            )
        else:
            # Hashed rather than printed, as an identical ANSWER is, and hashed
            # rather than omitted because two reports that each held steady on
            # DIFFERENT versions are not the same result -- the same argument
            # `_render_refs` makes for naming the refs every time.
            #
            # Hash the PAIRS, not `_render_versions` (hy-2g54). That helper staples
            # the two halves with `'@'`, which is legal in BOTH -- so `('a@b','c')`
            # and `('a','b@c')` render to one token and hashed to one cross-run
            # identity, and two reports steady on DIFFERENT pair sets printed the
            # same sha, defeating the reason this branch hashes at all. Each half
            # goes through `content_hash` first, so the boundary is a fixed-width
            # hex hash no `'@'` can shift -- the same nested-hash canonicaliser
            # `_identity` uses. `_render_versions` stays for the multi-set branch's
            # HUMAN line, where it is read and never compared, which is all its
            # docstring ever promised.
            paired = "\n".join(
                content_hash(ref) + content_hash(identity) for ref, identity in versions[0][0]
            )
            out.append(
                f"  VERSIONS identical across {len(self.repetitions)} repetitions "
                f"({content_hash(paired)})"
            )
        unversioned = self.unversioned_refs()
        if unversioned:
            # Named, in every branch above: which refs went unversioned decides
            # how much of the version comparison happened at all, and a count
            # with the refs thrown away cannot be acted on -- the argument
            # `render` already makes for the flapping explanations.
            out.append(
                f"    UNVERSIONED {len(unversioned)}: {_render_refs(unversioned)} -- read with no "
                "version identity, so a re-observation of these is invisible at both identities"
            )
        return "\n".join(out)


class MixedRepetitions(RuntimeError):
    """Recordings of different arms or different cases, handed to one report.

    The report's line names one arm and one case, so a set spanning two of
    either would print a line that is false about its own inputs.
    """


class EvidenceMismatch(RuntimeError):
    """A recording whose persisted `source_refs` disagree with its own trace.

    Fatal for the same reason `MixedRepetitions` is: the report would print two
    evidence counts, and if the coarse one came from the persisted field while
    the finer one came from the trace, the pair on the line would describe two
    different sets while inviting the reader to compare them.

    The refusal rather than the re-derivation is deliberate. `Recording.
    source_refs` is the field GitHub #25 requires a recording to carry, written
    at record time by `source_identity.source_refs` over this same trace, so
    the two agree by construction for any recording this repository produces.
    A disagreement means the artifact is false about itself -- a hand-edited
    recording, or one written by a walk that no longer matches the payload --
    and quietly reporting the trace's answer would hide that.
    """


def stability_report(recordings: Sequence[Recording], case: Case) -> StabilityReport:
    """Score every repetition of one case on one arm and compare them.

    Here rather than in the live test so that the whole path -- scoring each
    repetition, pulling the answer out, refusing drifted pins -- is exercised
    by tests that need no model. Logic whose only exercise is an hour-long
    scheduled job is logic nobody has seen run.

    `RECORDING_PINS` are compared HERE rather than in `StabilityReport`, which
    keeps only repetition 0's `git_commit` and never sees a `task_version` at
    all: the recordings are the only place both values exist per repetition.

    THE EVIDENCE IS WALKED ONCE PER RECORDING, here, and from the recording's
    own trace rather than from any store: both projections are views of that one
    list, so the two counts on the line are comparable, and nothing in the
    reporting path re-reads an asset -- a version identity fetched live would
    answer today's question about yesterday's run. The persisted `source_refs`
    is checked against the walk instead of being used as a second source, and a
    disagreement raises `EvidenceMismatch`.
    """
    if not recordings:
        raise ValueError("a stability report over zero repetitions reports nothing")
    arms = {recording.arm for recording in recordings}
    cases = {recording.case_id for recording in recordings}
    if len(arms) > 1 or len(cases) > 1:
        raise MixedRepetitions(
            f"one report covers one arm and one case; got arms {sorted(arms)} and "
            f"cases {sorted(cases)}"
        )
    for index, recording in enumerate(recordings[1:], start=1):
        _refuse_drift(
            {
                field: (getattr(recordings[0], field), getattr(recording, field))
                for field in RECORDING_PINS
                if getattr(recordings[0], field) != getattr(recording, field)
            },
            index=index,
        )
    entries = [tuple(observed_entries(recording.trace)) for recording in recordings]
    for index, (recording, walked) in enumerate(zip(recordings, entries, strict=True)):
        persisted = sorted(set(recording.source_refs))
        derived = sorted({ref for ref, _ in walked})
        if persisted != derived:
            raise EvidenceMismatch(
                f"repetition {index} is false about its own evidence -- it persists source_refs "
                f"{persisted} and its trace walks to {derived}; a report cannot print two counts "
                "over two sets"
            )
    return StabilityReport(
        arm=recordings[0].arm,
        case_id=recordings[0].case_id,
        git_commit=recordings[0].git_commit,
        repetitions=tuple(
            Repetition(
                index=index,
                pins=recording.pins,
                scores=tuple(score(recording, case)),
                answer=said(recording),
                trace_shape=trace_shape(recording.trace),
                source_entries=walked,
            )
            for index, (recording, walked) in enumerate(zip(recordings, entries, strict=True))
        ),
    )


def repeat_and_report(
    run_one: Callable[[int], Recording],
    case: Case,
    *,
    repetitions: int,
    record: Callable[[Recording], None] | None = None,
) -> StabilityReport:
    """Run one case N times, compare the repetitions, and only then record one.

    THE ORDER IS THE POINT. `assert_pins` compares the six repository pins by
    value on every repetition, so those cannot differ between repetitions
    without that repetition failing on its own; the three host pins -- digest,
    quantization, Ollama version -- are checked for PRESENCE only, so a model
    re-pull or a server upgrade mid-run is drift that only the cross-repetition
    check catches. Recording inside the loop would leave that refreshed
    artifact on disk behind a red run, so the write happens after
    `stability_report` has accepted the set or not at all.

    `run_one` and `record` are passed in so this is exercised without a model:
    the fake stands in for the inference, and what is asserted is the ordering,
    which is not a property of the fake.
    """
    recordings = [run_one(index) for index in range(repetitions)]
    report = stability_report(recordings, case)
    if record is not None:
        record(recordings[0])
    return report


class UnreadableStabilityLine(ValueError):
    """A pasted line this reader will not turn into fields.

    A `ValueError` still, so every caller that already handled one keeps
    working, but NAMED, because the two reasons a line is unreadable are not the
    same finding: something that is not a stability line at all, and a stability
    line from a version whose fields mean something else. `cross_session` has to
    tell those apart to say WHY it could not compare.
    """


def parse_stability_line(line: str) -> dict[str, str]:
    """Split a pasted line back into its fields, or refuse it BY VERSION.

    An older line is refused by the version it announces rather than by a field
    it lacks (hy-hk5m). A v1 line carries no identity per axis, so a reader that
    accepted it and found `answers_id` missing would be one `.get()` away from
    comparing two absences and calling them equal -- an absent field read as
    assent, which is the defect this whole change exists to close. The refusal
    names both versions so the holder of the old line knows it is not garbage,
    it is a measurement of fewer things.
    """
    tokens = shlex.split(line.strip())
    if tokens[:1] != [STABILITY_LINE_NAME]:
        raise UnreadableStabilityLine(f"not a stability line: {line!r}")
    if tokens[1:2] != [STABILITY_LINE_VERSION]:
        found = tokens[1] if len(tokens) > 1 else "<no version>"
        raise UnreadableStabilityLine(
            f"stability line version {found} is not {STABILITY_LINE_VERSION}: a "
            f"{found} line carries no identity per axis, and its missing fields must not read "
            f"as agreement -- re-run the report at this commit to get a "
            f"{STABILITY_LINE_VERSION} line"
        )
    prefix = STABILITY_LINE_PREFIX.split()
    fields: dict[str, str] = {}
    for token in tokens[len(prefix) :]:
        key, separator, value = token.partition("=")
        if not separator:
            raise UnreadableStabilityLine(f"unparseable field {token!r} in {line!r}")
        fields[key] = value
    return fields
