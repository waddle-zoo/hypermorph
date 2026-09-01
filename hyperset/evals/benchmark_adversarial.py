"""Deterministic exit-gate scoring for the simulated-expert adversarial benchmark (#141).

Slice 1 (hy-gh-141): the RECORDED-REPLAY half of the benchmark -- the corpus
superset shape and the report that recomputes the spec's section-6 exit gate
DETERMINISTICALLY from committed evidence. NO live model runs here. The spec is
`docs/development/benchmark-adversarial-v1.md`.

The live generator/judge/frontier answering arm (slices 2-4) produce their output
INTO this shape; they stay blocked on the hy-2tg6 infra decision. This module
never calls a model, never serves an operation (so `tools_hash` is unmoved), and
never reads or writes a `#25` recording (`hyperset/evals/recording.py`).

JUDGE-DETERMINISM REALITY, documented rather than assumed: a hosted frontier
judge is NOT bit-reproducible even at temperature 0, so this gate does not depend
on re-calling the judge. Determinism is the COMMITTED per-axis scores plus the
stability RE-SCORE guard (spec section 4): the judge's seed and temperature are
RECORDED as provenance, not relied on as a reproducibility guarantee. The report
recomputes identically from the committed numbers, and the only randomness it
uses -- the bootstrap resample -- is seeded (`PINNED_SEED`) and drawn in a sorted
order, so two scorings of one run are byte-identical.

This benchmark is an exploration/regression layer and MUST NOT block a merge
(spec section 1, ADR 0013): the report is a value a human reads, never a required
gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

from hyperset.evals.pins import PINNED_SEED, RunPins

# --- Section-6 thresholds, named once so the report and its messages read one source. ---
MIN_CASES = 60  # N
MIN_DOMAINS = 2  # M
MIN_MODELS = 2  # K
MIN_CROSS_DOMAIN = 12
MIN_TRAP = 24
MIN_AGGREGATE_ADVANTAGE = 2.0
MIN_WIN_RATE = 0.65
MIN_AVOIDED_CAPTURE = 0.80
MIN_AVOIDED_DENOMINATOR = 10
MIN_JUDGE_STABILITY = 0.90
JUDGE_STABILITY_TOLERANCE = 1  # +/- 1 on an axis counts as agreement
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_CI = 0.95

AXIS_MAX = 4
AVOIDED_CUT = 3  # avoided-mistake axis >= 3 => the answer AVOIDED the trap
FELL_IN_CUT = 1  # avoided-mistake axis <= 1 => the answer FELL IN

# The three exit-gate statuses. INVALID is neither a pass nor a fail: a run that
# fails a validity guard measured nothing comparable, so it is disclosed and set
# aside, never counted as evidence either way (spec section 6).
PASS = "PASS"
FAIL = "FAIL"
INVALID = "INVALID"

DISCLOSURE = (
    "This report recomputed the section-6 exit gate from a COMMITTED adversarial "
    "benchmark corpus and its committed per-axis judge scores, not from a live model run. "
    "It proves the scoring math and the recorded scores. A hosted judge is not "
    "bit-reproducible, so the judge's seed and temperature are recorded as provenance and "
    "the judge-stability re-score -- not a re-call -- is what bounds judge noise. The live "
    "generate/answer/judge arms are what check a live model (ADR 0013); they are blocked on "
    "the hy-2tg6 infra decision."
)


@dataclass(frozen=True)
class AxisScores:
    """The three integer 0-4 axes a judge assigns ONE answer (spec section 4)."""

    correctness: int
    evidence: int
    avoided_mistake: int

    def __post_init__(self) -> None:
        for name, value in (
            ("correctness", self.correctness),
            ("evidence", self.evidence),
            ("avoided_mistake", self.avoided_mistake),
        ):
            if type(value) is not int or not 0 <= value <= AXIS_MAX:
                raise ValueError(f"{name} must be an integer 0..{AXIS_MAX}, got {value!r}")

    @property
    def total(self) -> int:
        """The per-answer score, integer 0-12 (spec section 4)."""
        return self.correctness + self.evidence + self.avoided_mistake

    def axes(self) -> tuple[int, int, int]:
        return (self.correctness, self.evidence, self.avoided_mistake)


@dataclass(frozen=True)
class BenchmarkCase:
    """One committed corpus case (spec section 2). The correctness ORACLE is
    `expected_governed_refs` (governed rows in the pinned snapshot); `trap_type` is
    a deterministically checkable governed fact; `reference_answer` is an advisory
    aid for the judge, never the sole authority."""

    case_id: str
    question: str
    domains: tuple[str, ...]
    trap_type: str | None
    reference_answer: str
    expected_governed_refs: tuple[str, ...]

    @property
    def is_cross_domain(self) -> bool:
        return len({domain for domain in self.domains}) >= 2

    @property
    def is_trap(self) -> bool:
        return self.trap_type is not None


@dataclass(frozen=True)
class PairedScore:
    """The judge's scores for BOTH answers to one case under one answering model.

    The comparison holds the answering model fixed and varies only Hyperset vs raw
    (spec section 5), so the unit of the aggregate is a case x answering-model pair."""

    case_id: str
    model: str
    hyperset: AxisScores
    raw: AxisScores

    @property
    def advantage(self) -> int:
        """Hyperset advantage on this case x arm, range -12..+12 (spec section 4)."""
        return self.hyperset.total - self.raw.total

    @property
    def hyperset_wins(self) -> bool:
        # A tie counts as NOT a win (spec section 6, criterion 2).
        return self.hyperset.total > self.raw.total


@dataclass(frozen=True)
class BenchmarkRun:
    """A committed benchmark run: the corpus, the judge's per-axis scores for every
    case x answering-model pair, a judge stability re-score of the SAME answers, and
    the pins/provenance the recording carries. Slice 1 scores this shape; slices 2-4
    fill it from live models."""

    corpus: tuple[BenchmarkCase, ...]
    scores: tuple[PairedScore, ...]
    stability_rescore: tuple[PairedScore, ...]
    answering_models: tuple[str, ...]
    judge_model: str
    blind: bool
    order_randomized: bool
    generator_pins: RunPins | None = None
    judge_pins: RunPins | None = None
    answering_pins: tuple[RunPins, ...] = ()
    git_commit: str | None = None
    tree_oid: str | None = None
    recorded_at: str | None = None


@dataclass(frozen=True)
class CriterionResult:
    """One section-6 criterion's outcome. `measured` is False only for the
    avoided-mistake criterion when its denominator is below the floor: an
    UNMEASURED criterion is neither a pass nor a fail (spec section 6.3)."""

    name: str
    measured: bool
    passed: bool
    detail: str


@dataclass(frozen=True)
class BenchmarkReport:
    status: str
    valid: bool
    invalid_reasons: tuple[str, ...]
    criteria: tuple[CriterionResult, ...]
    disclosure: str = DISCLOSURE

    @property
    def passed(self) -> bool:
        return self.status == PASS


def score_benchmark(run: BenchmarkRun) -> BenchmarkReport:
    """Recompute the section-6 exit gate deterministically from a committed run.

    A run that fails ANY validity guard is INVALID and disclosed -- neither a pass
    nor a fail, and never a merge-blocker. A valid run PASSES iff its aggregate
    advantage, pairwise win rate and (when measured) avoided-mistake capture all
    pass; the avoided-mistake criterion is skipped, not failed, when its
    denominator is below the floor."""
    invalid = _validity_reasons(run)
    if invalid:
        return BenchmarkReport(
            status=INVALID,
            valid=False,
            invalid_reasons=tuple(invalid),
            criteria=(),
        )

    criteria = (
        _aggregate_advantage(run),
        _win_rate(run),
        _avoided_capture(run),
    )
    decisive = [c for c in criteria if c.measured]
    status = PASS if all(c.passed for c in decisive) else FAIL
    return BenchmarkReport(
        status=status,
        valid=True,
        invalid_reasons=(),
        criteria=criteria,
    )


def _validity_reasons(run: BenchmarkRun) -> list[str]:
    reasons: list[str] = []

    # STRUCTURAL INTEGRITY FIRST. Every count and coverage check below is SET-BASED,
    # and a set cannot see a repeat: without this, a corpus of 60 COPIES of one case
    # satisfies N, cross-domain, trap and the grid (the deduped set is tiny but the
    # floors read the list length), and duplicate score rows bias the mean, win rate
    # and bootstrap while `_paired_axes` silently collapses them for the stability
    # check -- a false PASS (adversary bounce, #370). So reject any duplicate before
    # the floors are trusted: unique case ids, unique answering models, and EXACTLY ONE
    # (case_id, model) row in BOTH the scores and the stability re-score.
    corpus_ids = [case.case_id for case in run.corpus]
    duplicate_cases = sorted({cid for cid in corpus_ids if corpus_ids.count(cid) > 1})
    if duplicate_cases:
        reasons.append(
            f"duplicate BenchmarkCase.case_id {duplicate_cases}; every corpus case must be "
            "unique, or the set-based floors count copies as distinct cases"
        )
    if len(set(run.answering_models)) != len(run.answering_models):
        reasons.append(
            "duplicate answering-model entries; each answering model must be listed once"
        )
    score_keys = [(score.case_id, score.model) for score in run.scores]
    if len(score_keys) != len(set(score_keys)):
        reasons.append(
            "duplicate (case_id, model) score rows; each pair must be scored EXACTLY once, "
            "or the mean, win rate and bootstrap are biased by the repeats"
        )
    rescore_keys = [(score.case_id, score.model) for score in run.stability_rescore]
    if len(rescore_keys) != len(set(rescore_keys)):
        reasons.append(
            "duplicate (case_id, model) stability re-score rows; each pair must appear EXACTLY "
            "once, or the axis-agreement is measured against a collapsed subset"
        )

    n = len(run.corpus)
    if n < MIN_CASES:
        reasons.append(f"N = {n} committed cases, below the floor of {MIN_CASES}")

    domains = {domain for case in run.corpus for domain in case.domains}
    if len(domains) < MIN_DOMAINS:
        reasons.append(f"M = {len(domains)} distinct domains, below the floor of {MIN_DOMAINS}")

    models = set(run.answering_models)
    if len(models) < MIN_MODELS:
        reasons.append(f"K = {len(models)} answering models, below the floor of {MIN_MODELS}")

    # Every case must be answered by every arm under every answering model, or the
    # aggregate is computed over a self-selected subset -- a run that scored only the
    # easy cases would read as a real signal. The scored pairs must be EXACTLY the
    # corpus x answering-models grid, no more (a stray model) and no less (a skipped case).
    expected_pairs = {
        (case.case_id, model) for case in run.corpus for model in run.answering_models
    }
    scored_pairs = {(score.case_id, score.model) for score in run.scores}
    if scored_pairs != expected_pairs and run.corpus and run.answering_models:
        missing = len(expected_pairs - scored_pairs)
        extra = len(scored_pairs - expected_pairs)
        reasons.append(
            f"the scores do not cover the corpus x answering-models grid "
            f"({missing} pair(s) unscored, {extra} pair(s) off-grid); the aggregate would be "
            "over a self-selected subset"
        )

    cross = sum(1 for case in run.corpus if case.is_cross_domain)
    if cross < MIN_CROSS_DOMAIN:
        reasons.append(f"{cross} cross-domain cases, below the floor of {MIN_CROSS_DOMAIN}")

    traps = sum(1 for case in run.corpus if case.is_trap)
    if traps < MIN_TRAP:
        reasons.append(f"{traps} trap cases, below the floor of {MIN_TRAP}")

    stability = _judge_stability(run)
    if stability is None:
        reasons.append(
            "the judge stability re-score does not cover exactly the scored case x model "
            "pairs, so its axis agreement cannot be measured"
        )
    elif stability < MIN_JUDGE_STABILITY:
        reasons.append(
            f"judge stability {stability:.3f} axis-agreement within "
            f"+/-{JUDGE_STABILITY_TOLERANCE}, below the floor of {MIN_JUDGE_STABILITY}"
        )

    if not run.blind:
        reasons.append("the judge did not score blind (arm identity hidden, order randomized)")
    if not run.order_randomized:
        reasons.append("answer order was not randomized per case")
    if run.judge_model in set(run.answering_models):
        reasons.append(
            f"the judge model {run.judge_model!r} is also an answering model; the judge must "
            "differ at the model level from both answers it scores (no self-judging)"
        )
    return reasons


def _paired_axes(scores: tuple[PairedScore, ...]) -> dict[tuple[str, str], tuple[int, ...]]:
    """`(case_id, model) -> the six axis values` (hyperset 3 then raw 3), so a
    re-score can be compared axis-for-axis against the original."""
    return {
        (score.case_id, score.model): score.hyperset.axes() + score.raw.axes() for score in scores
    }


def _judge_stability(run: BenchmarkRun) -> float | None:
    """Fraction of axis scores the re-score agrees with within the tolerance, or
    None when the re-score does not cover exactly the same pairs (an unmeasurable
    guard, treated as INVALID rather than silently passed)."""
    original = _paired_axes(run.scores)
    rescored = _paired_axes(run.stability_rescore)
    if set(original) != set(rescored) or not original:
        return None
    agree = 0
    total = 0
    for key, axes in original.items():
        for a, b in zip(axes, rescored[key], strict=True):
            total += 1
            if abs(a - b) <= JUDGE_STABILITY_TOLERANCE:
                agree += 1
    return agree / total if total else None


def _aggregate_advantage(run: BenchmarkRun) -> CriterionResult:
    advantages = [score.advantage for score in run.scores]
    mean = sum(advantages) / len(advantages)
    ci_lower = _bootstrap_ci_lower(run.scores)
    passed = mean >= MIN_AGGREGATE_ADVANTAGE and ci_lower > 0
    return CriterionResult(
        name="aggregate_advantage",
        measured=True,
        passed=passed,
        detail=(
            f"mean Hyperset advantage {mean:.3f} (floor {MIN_AGGREGATE_ADVANTAGE}); "
            f"bootstrap 95% CI lower bound {ci_lower:.3f} (must be > 0)"
        ),
    )


def _bootstrap_ci_lower(scores: tuple[PairedScore, ...]) -> float:
    """The lower bound of the deterministic clustered bootstrap 95% CI of the mean
    Hyperset advantage (spec section 6.1).

    CLUSTERED BY CASE: each resample draws whole CASES with replacement, and every
    drawn case contributes all of its answering-model advantages, so the K arms of
    a case move together and the CI is not narrowed by treating correlated arm
    scores as independent. DETERMINISTIC: the case order is sorted and the RNG is
    seeded with `PINNED_SEED`, so two scorings return the identical bound.

    Percentile method, nearest-rank lower index: with the resample means sorted
    ascending, the 2.5th-percentile bound is the mean at index
    `int(0.025 * BOOTSTRAP_RESAMPLES)`. Stated exactly so the report recomputes it
    identically."""
    by_case: dict[str, list[int]] = {}
    for score in scores:
        by_case.setdefault(score.case_id, []).append(score.advantage)
    case_ids = sorted(by_case)
    n = len(case_ids)
    rng = Random(PINNED_SEED)
    means: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        drawn: list[int] = []
        for _ in range(n):
            drawn.extend(by_case[case_ids[rng.randrange(n)]])
        means.append(sum(drawn) / len(drawn))
    means.sort()
    lower_index = int((1 - BOOTSTRAP_CI) / 2 * BOOTSTRAP_RESAMPLES)
    return means[lower_index]


def _win_rate(run: BenchmarkRun) -> CriterionResult:
    wins = sum(1 for score in run.scores if score.hyperset_wins)
    rate = wins / len(run.scores)
    return CriterionResult(
        name="pairwise_win_rate",
        measured=True,
        passed=rate >= MIN_WIN_RATE,
        detail=(
            f"Hyperset scored strictly higher on {wins}/{len(run.scores)} pairs "
            f"({rate:.3f}); floor {MIN_WIN_RATE} (a tie is not a win)"
        ),
    )


def _avoided_capture(run: BenchmarkRun) -> CriterionResult:
    """Over trap cases where the RAW arm FELL IN (avoided axis <= 1), how often the
    Hyperset arm AVOIDED (axis >= 3). Below a denominator of 10 this is DISCLOSED as
    UNMEASURED -- neither a pass nor a fail (spec section 6.3), never a
    divide-by-zero pass."""
    trap_ids = {case.case_id for case in run.corpus if case.is_trap}
    fell_in = [
        score
        for score in run.scores
        if score.case_id in trap_ids and score.raw.avoided_mistake <= FELL_IN_CUT
    ]
    denominator = len(fell_in)
    if denominator < MIN_AVOIDED_DENOMINATOR:
        return CriterionResult(
            name="avoided_mistake_capture",
            measured=False,
            passed=False,
            detail=(
                f"only {denominator} trap case x arm pairs where the raw arm fell in "
                f"(floor {MIN_AVOIDED_DENOMINATOR}); UNMEASURED, not counted for or against"
            ),
        )
    captured = sum(1 for score in fell_in if score.hyperset.avoided_mistake >= AVOIDED_CUT)
    rate = captured / denominator
    return CriterionResult(
        name="avoided_mistake_capture",
        measured=True,
        passed=rate >= MIN_AVOIDED_CAPTURE,
        detail=(
            f"Hyperset avoided the trap on {captured}/{denominator} pairs the raw arm "
            f"fell in ({rate:.3f}); floor {MIN_AVOIDED_CAPTURE}"
        ),
    )


def render(report: BenchmarkReport) -> str:
    """A human-readable report, disclosure first. Never a machine gate."""
    lines = [report.disclosure, "", f"RESULT: {report.status}"]
    if report.invalid_reasons:
        lines.append("INVALID because:")
        lines.extend(f"  - {reason}" for reason in report.invalid_reasons)
    for criterion in report.criteria:
        state = "UNMEASURED" if not criterion.measured else ("pass" if criterion.passed else "FAIL")
        lines.append(f"  [{state}] {criterion.name}: {criterion.detail}")
    return "\n".join(lines)
