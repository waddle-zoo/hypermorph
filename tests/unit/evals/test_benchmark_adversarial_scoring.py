"""The section-6 exit-gate math, recomputed from a committed run (#141 slice 1).

Deterministic and model-free: every case here builds a committed `BenchmarkRun`
in memory and asserts the report the spec's section 6 requires. The fixtures are
constructed programmatically rather than hand-typed as 60 JSON cases -- a builder
is what makes the INVALID and UNMEASURED branches reachable one guard at a time
without a wall of literals -- but every number a criterion turns on is stated in
the test that turns on it.
"""

from __future__ import annotations

import pytest

from hyperset.evals.benchmark_adversarial import (
    DISCLOSURE,
    FAIL,
    INVALID,
    PASS,
    AxisScores,
    BenchmarkCase,
    BenchmarkRun,
    PairedScore,
    render,
    score_benchmark,
)

MODELS = ("qwen2.5:7b", "frontier-x")


def _case(i: int, *, cross: bool, trap: str | None) -> BenchmarkCase:
    domains = ("revenue", "supply_chain") if cross else ("revenue",)
    return BenchmarkCase(
        case_id=f"c{i:03d}",
        question=f"question {i}",
        domains=domains,
        trap_type=trap,
        reference_answer="advisory reference",
        expected_governed_refs=("superset:dataset:orders",),
    )


def _corpus(*, n: int = 60, cross: int = 12, trap: int = 24) -> tuple[BenchmarkCase, ...]:
    return tuple(
        _case(i, cross=i < cross, trap="missing_required_filter" if i < trap else None)
        for i in range(n)
    )


def _scores(
    corpus: tuple[BenchmarkCase, ...],
    *,
    models: tuple[str, ...] = MODELS,
    hy: tuple[int, int, int] = (4, 4, 4),
    raw: tuple[int, int, int] = (1, 1, 0),
    trap_hy: tuple[int, int, int] = (4, 4, 4),
    trap_raw: tuple[int, int, int] = (1, 1, 0),
) -> tuple[PairedScore, ...]:
    scores = []
    for case in corpus:
        for model in models:
            if case.is_trap:
                pair = PairedScore(case.case_id, model, AxisScores(*trap_hy), AxisScores(*trap_raw))
            else:
                pair = PairedScore(case.case_id, model, AxisScores(*hy), AxisScores(*raw))
            scores.append(pair)
    return tuple(scores)


def _run(
    *,
    corpus: tuple[BenchmarkCase, ...] | None = None,
    scores: tuple[PairedScore, ...] | None = None,
    rescore: tuple[PairedScore, ...] | None = None,
    models: tuple[str, ...] = MODELS,
    judge_model: str = "judge-z",
    blind: bool = True,
    order_randomized: bool = True,
) -> BenchmarkRun:
    corpus = corpus if corpus is not None else _corpus()
    scores = scores if scores is not None else _scores(corpus, models=models)
    rescore = rescore if rescore is not None else scores
    return BenchmarkRun(
        corpus=corpus,
        scores=scores,
        stability_rescore=rescore,
        answering_models=models,
        judge_model=judge_model,
        blind=blind,
        order_randomized=order_randomized,
    )


def test_a_clean_run_passes_on_all_three_criteria():
    report = score_benchmark(_run())

    assert report.valid
    assert report.status == PASS
    assert report.passed
    assert {c.name for c in report.criteria} == {
        "aggregate_advantage",
        "pairwise_win_rate",
        "avoided_mistake_capture",
    }
    assert all(c.measured and c.passed for c in report.criteria)


def test_too_few_cases_is_invalid_and_names_the_shortfall():
    report = score_benchmark(_run(corpus=_corpus(n=59)))

    assert report.status == INVALID
    assert not report.valid
    assert report.criteria == ()
    assert any("N = 59" in reason for reason in report.invalid_reasons)


def test_one_answering_model_is_invalid():
    solo = ("qwen2.5:7b",)
    report = score_benchmark(_run(models=solo, judge_model="judge-z"))

    assert report.status == INVALID
    assert any("K = 1" in reason for reason in report.invalid_reasons)


def test_too_few_cross_domain_cases_is_invalid():
    report = score_benchmark(_run(corpus=_corpus(cross=11)))

    assert report.status == INVALID
    assert any("cross-domain" in reason for reason in report.invalid_reasons)


def test_too_few_trap_cases_is_invalid():
    report = score_benchmark(_run(corpus=_corpus(trap=23)))

    assert report.status == INVALID
    assert any("trap cases" in reason for reason in report.invalid_reasons)


def test_a_judge_that_does_not_re_agree_is_invalid():
    corpus = _corpus()
    scores = _scores(corpus)
    # Re-score every pair with correctness off by 3 -> 5 of 6 axes agree per pair
    # (0.833), below the 0.90 floor.
    rescore = tuple(
        PairedScore(
            s.case_id,
            s.model,
            AxisScores(
                max(0, s.hyperset.correctness - 3), s.hyperset.evidence, s.hyperset.avoided_mistake
            ),
            s.raw,
        )
        for s in scores
    )
    report = score_benchmark(_run(corpus=corpus, scores=scores, rescore=rescore))

    assert report.status == INVALID
    assert any("judge stability" in reason for reason in report.invalid_reasons)


def test_a_re_score_that_misses_pairs_is_invalid_not_silently_passed():
    corpus = _corpus()
    scores = _scores(corpus)
    report = score_benchmark(_run(corpus=corpus, scores=scores, rescore=scores[:-1]))

    assert report.status == INVALID
    assert any("cannot be measured" in reason for reason in report.invalid_reasons)


def test_scores_that_skip_cases_are_invalid_not_a_biased_aggregate():
    corpus = _corpus()
    full = _scores(corpus)
    # Drop every pair for the last 20 cases -> the grid is not covered, so the
    # aggregate would be over a self-selected subset.
    dropped_ids = {case.case_id for case in corpus[-20:]}
    partial = tuple(s for s in full if s.case_id not in dropped_ids)
    report = score_benchmark(_run(corpus=corpus, scores=partial, rescore=partial))

    assert report.status == INVALID
    assert any("self-selected subset" in reason for reason in report.invalid_reasons)


def test_a_corpus_of_duplicate_cases_is_invalid_never_a_false_pass():
    # #370 adversary bounce: 60 COPIES of one PASS-worthy case satisfy every set-based
    # floor and the grid, and would false-PASS without a duplicate check. It must INVALID.
    one = _case(0, cross=True, trap="missing_required_filter")
    corpus = tuple(one for _ in range(60))
    scores = _scores(corpus)  # both models, hyperset dominant -> would pass on the numbers
    report = score_benchmark(_run(corpus=corpus, scores=scores, rescore=scores))

    assert report.status == INVALID
    assert report.status != PASS
    assert any("duplicate BenchmarkCase.case_id" in reason for reason in report.invalid_reasons)


def test_duplicate_score_rows_are_invalid_not_a_biased_aggregate():
    corpus = _corpus()
    scores = _scores(corpus)
    # Append an exact duplicate of the first pair: the set-based grid check still matches,
    # but the mean/win-rate/bootstrap would double-count it.
    biased = scores + (scores[0],)
    report = score_benchmark(_run(corpus=corpus, scores=biased, rescore=biased))

    assert report.status == INVALID
    assert any(
        "duplicate (case_id, model) score rows" in reason for reason in report.invalid_reasons
    )


def test_a_self_judging_model_is_invalid():
    report = score_benchmark(_run(judge_model="qwen2.5:7b"))

    assert report.status == INVALID
    assert any("no self-judging" in reason for reason in report.invalid_reasons)


def test_a_non_blind_or_unrandomized_judge_is_invalid():
    assert score_benchmark(_run(blind=False)).status == INVALID
    assert score_benchmark(_run(order_randomized=False)).status == INVALID


def test_avoided_capture_below_ten_fell_in_is_unmeasured_not_a_pass_or_fail():
    corpus = _corpus()
    # The raw arm never falls in (avoided axis 4 > 1), so the capture denominator
    # is 0 -- below the floor of 10 -- but Hyperset still wins on advantage/rate.
    scores = _scores(corpus, trap_raw=(0, 0, 4))
    report = score_benchmark(_run(corpus=corpus, scores=scores))

    capture = next(c for c in report.criteria if c.name == "avoided_mistake_capture")
    assert capture.measured is False
    assert "UNMEASURED" in capture.detail
    # The run is still judged on criteria 1-2, and passes them.
    assert report.status == PASS


def test_the_bootstrap_is_deterministic_across_two_scorings():
    run = _run()
    first = score_benchmark(run)
    second = score_benchmark(run)

    assert first == second
    aggregate = next(c for c in first.criteria if c.name == "aggregate_advantage")
    assert "CI lower bound" in aggregate.detail


def test_a_tie_counts_as_not_a_win():
    assert PairedScore("c000", "m", AxisScores(2, 2, 2), AxisScores(2, 2, 2)).hyperset_wins is False

    corpus = _corpus()
    even = _scores(corpus, hy=(2, 2, 2), raw=(2, 2, 2), trap_hy=(2, 2, 2), trap_raw=(2, 2, 2))
    report = score_benchmark(_run(corpus=corpus, scores=even))

    win = next(c for c in report.criteria if c.name == "pairwise_win_rate")
    assert not win.passed
    assert "0.000" in win.detail
    assert report.status == FAIL


def test_a_valid_run_below_the_advantage_floor_fails_rather_than_invalidates():
    corpus = _corpus()
    # Hyperset wins every pair (3 > 2) so the win rate passes, but the mean
    # advantage is only +1, below the +2.0 floor: a FAIL, not an INVALID.
    thin = _scores(corpus, hy=(1, 1, 1), raw=(1, 1, 0), trap_hy=(1, 1, 1), trap_raw=(1, 1, 0))
    report = score_benchmark(_run(corpus=corpus, scores=thin))

    assert report.valid
    assert report.status == FAIL
    aggregate = next(c for c in report.criteria if c.name == "aggregate_advantage")
    assert not aggregate.passed


def test_an_axis_score_outside_zero_to_four_is_refused():
    with pytest.raises(ValueError, match="correctness"):
        AxisScores(5, 0, 0)
    with pytest.raises(ValueError, match="avoided_mistake"):
        AxisScores(0, 0, -1)


def test_the_report_leads_with_the_disclosure_and_names_the_infra_blocker():
    report = score_benchmark(_run())

    assert report.disclosure == DISCLOSURE
    assert "ADR 0013" in DISCLOSURE
    assert "hy-2tg6" in DISCLOSURE
    assert render(report).startswith(DISCLOSURE)
