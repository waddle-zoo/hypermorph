"""The repetition and reporting logic, exercised without a model (hy-2beh).

The live half of this costs roughly an hour of local inference, so none of it
runs here: every repetition below is a hand-built `Score` list and a hand-built
answer string. What is asserted is what an hour-long job would otherwise be the
only exercise of -- that a flapping predicate is visible, that the disagreeing
answers survive into the report, that the line names its own inputs, and that
repetitions which did not run under the same pins are refused rather than
averaged.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hyperset.evals.cases import Case
from hyperset.evals.pins import RunPins, repository_pins
from hyperset.evals.recording import (
    GOVERNED_ARM,
    RAW_ARM,
    RECORDING_SCHEMA_VERSION,
    Recording,
)
from hyperset.evals.scorers import Code, Score
from hyperset.evals.source_identity import UNVERSIONED
from hyperset.evals.stability import (
    ABSENT,
    DEFAULT_REPETITIONS,
    FAIL,
    PASS,
    REPETITIONS_ENV,
    RESULT,
    STABILITY_LINE_PREFIX,
    EvidenceMismatch,
    MixedRepetitions,
    PinsDrifted,
    Repetition,
    StabilityReport,
    UnreadableStabilityLine,
    configured_repetitions,
    parse_stability_line,
    repeat_and_report,
    stability_report,
)
from hyperset.planner.trace import PLANNER_MESSAGE, TOOL_RESULT

HOST = {
    "digest": "sha256:1c2f3d4e5a6b",
    "quantization": "Q4_K_M",
    "ollama_version": "0.32.4",
}


def pins(**overrides) -> RunPins:
    return RunPins(**{**repository_pins(GOVERNED_ARM), **HOST, **overrides})


def scored(*verdicts: tuple[str, bool, str]) -> tuple[Score, ...]:
    """Hand-built verdicts for the stability comparison.

    `code` is a STAND-IN and deliberately not chosen per predicate: these
    repetitions carry invented predicate names, and stability compares verdicts,
    answer text and evidence -- it does not read the branch code. Whether
    `PredicateAgreement` and `cross_session.AXES` should read it is hy-mkw6, an
    ADR-level call this bead does not make. A stand-in that looked
    predicate-specific would read as an answer to that question.
    """
    return tuple(
        Score(
            predicate=name,
            code=Code.ANSWERED if passed else Code.RUN_FAILED,
            passed=passed,
            critical=False,
            explanation=explanation,
        )
        for name, passed, explanation in verdicts
    )


STAND_IN_SHA = "0" * 64
"""What `content_sha256` would be for a repetition that read every ref once.

Defaulted rather than omitted so the helper cannot build a repetition
production could not produce -- refs with no versions beside them is not a
state `stability_report` can derive. A test about a re-observation or an
unversioned asset passes its own `source_entries`."""


STAND_IN_SHAPE = ("list_context_catalog", "resolve_analytics_context")
"""What the governed arm's committed recordings actually did, so a helper that
says nothing about the trace still builds a repetition production could
produce. A test about hy-9dyv's axis passes its own `trace_shape`."""


def repetition(
    index: int,
    *,
    verdicts,
    answer="the same answer",
    source_refs=(),
    source_entries=None,
    trace_shape=STAND_IN_SHAPE,
    **pin_overrides,
) -> Repetition:
    return Repetition(
        index=index,
        pins=pins(**pin_overrides),
        scores=scored(*verdicts),
        answer=answer,
        trace_shape=tuple(trace_shape),
        source_entries=(
            tuple((ref, STAND_IN_SHA) for ref in source_refs)
            if source_entries is None
            else tuple(source_entries)
        ),
    )


def report(*repetitions) -> StabilityReport:
    return StabilityReport(
        arm=GOVERNED_ARM,
        case_id="revenue_by_region",
        git_commit="a" * 40,
        repetitions=tuple(repetitions),
    )


def test_a_predicate_that_flapped_is_reported_with_both_explanations():
    """An agreement percentage with the disagreeing answers thrown away cannot be acted on."""
    subject = report(
        repetition(0, verdicts=[("run_completed", True, "answered"), ("rules", True, "stated it")]),
        repetition(
            1,
            verdicts=[("run_completed", True, "answered"), ("rules", False, "omitted the split")],
        ),
        repetition(2, verdicts=[("run_completed", True, "answered"), ("rules", True, "stated it")]),
    )

    flapping = subject.flapping()

    assert [entry.predicate for entry in flapping] == ["rules"]
    assert flapping[0].verdicts == (PASS, FAIL, PASS)
    rendered = subject.render()
    assert "FLAP rules: pass,fail,pass" in rendered
    assert "omitted the split" in rendered, "the explanation that differed must survive"
    assert "run_completed" not in rendered.split("FLAP")[1], "unanimous predicates need no detail"


def test_a_predicate_missing_from_one_repetition_is_instability_rather_than_a_smaller_denominator():
    """Scorers return None for a predicate that does not apply.

    Two repetitions producing different predicate SETS is the same defect as
    two agents collecting different test sets (hy-y91y): the tally would
    silently compare different denominators and call the result agreement.
    """
    subject = report(
        repetition(0, verdicts=[("run_completed", True, "answered"), ("rules", True, "stated")]),
        repetition(1, verdicts=[("run_completed", True, "answered")]),
    )

    flapping = subject.flapping()

    assert [entry.predicate for entry in flapping] == ["rules"]
    assert flapping[0].verdicts == (PASS, ABSENT)


def test_two_answers_that_score_identically_are_still_two_answers():
    """The predicates are lexical, so verdict agreement is not answer agreement."""
    verdicts = [("run_completed", True, "answered")]
    subject = report(
        repetition(0, verdicts=verdicts, answer="revenue is SUM(gross_amount - tax_amount)"),
        repetition(1, verdicts=verdicts, answer="revenue equals SUM(gross_amount - tax_amount)"),
    )

    assert not subject.flapping(), "every predicate agreed"
    assert len(subject.distinct_answers()) == 2
    rendered = subject.render()
    assert "ANSWERS 2 distinct across 2" in rendered
    assert "revenue is SUM(gross_amount - tax_amount)" in rendered
    assert "revenue equals SUM(gross_amount - tax_amount)" in rendered


def test_two_repetitions_that_rested_on_different_evidence_are_not_fully_stable():
    """Identical verdicts and identical answer text, different sources.

    Measured by critic on #142's head: two recordings alike in everything the
    report compared, one resting on no evidence and one on a dataset ref, and
    the line said `flapping=0 answers_distinct=1` with nothing else to read.
    An answer that arrived from different sources on different repetitions is
    the instability the other two fields cannot see.
    """
    verdicts = [("run_completed", True, "answered")]
    subject = report(
        repetition(0, verdicts=verdicts, source_refs=()),
        repetition(1, verdicts=verdicts, source_refs=("dataset:urn:li:dataset:other_source",)),
    )

    assert not subject.flapping(), "every predicate agreed"
    assert len(subject.distinct_answers()) == 1, "and the answer text is identical"
    assert len(subject.distinct_source_refs()) == 2
    assert parse_stability_line(subject.line())["source_refs_distinct"] == "2"
    rendered = subject.render()
    assert "EVIDENCE 2 distinct sets across 2" in rendered
    assert "dataset:urn:li:dataset:other_source" in rendered, (
        "the refs that differed must survive into the report, as the answers do"
    )
    assert "<none>" in rendered, "an empty set must be visible rather than a blank line"


def test_the_same_refs_in_a_different_order_are_the_same_evidence():
    """A SET, so ordering and repetition of a ref are not instability.

    `source_refs` is a list and nothing promises its order: two runs that rested
    on the same two datasets are the same evidence, and counting the orderings
    would report tool-call ordering as evidence drift while `flapping` and
    `answers_distinct` correctly said nothing moved.
    """
    verdicts = [("run_completed", True, "answered")]
    subject = report(
        repetition(0, verdicts=verdicts, source_refs=("dataset:a", "dataset:b")),
        repetition(1, verdicts=verdicts, source_refs=("dataset:b", "dataset:a", "dataset:b")),
    )

    assert len(subject.distinct_source_refs()) == 1
    refs, indexes = subject.distinct_source_refs()[0]
    assert refs == ("dataset:a", "dataset:b"), "normalised, so two reports print one form"
    assert indexes == (0, 1)


def test_identical_evidence_is_named_once_rather_than_per_repetition():
    """The refs themselves, not a hash: unlike an answer, a ref set is one line.

    Named even when nothing moved, because two reports whose evidence each held
    steady on DIFFERENT sources are not the same result, and `source_refs_
    distinct=1` alone cannot tell them apart.
    """
    verdicts = [("run_completed", True, "answered")]
    subject = report(
        repetition(0, verdicts=verdicts, source_refs=("dataset:a",)),
        repetition(1, verdicts=verdicts, source_refs=("dataset:a",)),
    )

    rendered = subject.render()

    assert "EVIDENCE identical across 2 repetitions: dataset:a" in rendered
    assert rendered.count("dataset:a") == 1


def test_a_re_observed_source_is_one_ref_set_and_two_version_sets():
    """The pair, on the line: `source_refs_distinct=1 source_versions_distinct=2`.

    Critic's measurement at #143's head, carried up to the report: two
    repetitions that read the SAME asset at two `content_sha256` values are one
    ref set, so the line said identical evidence about two runs that read
    different bytes. The assertion that carries the finding is that the two
    counts come apart here -- equal counts in this world would mean the second
    identity is a copy of the first.
    """
    verdicts = [("run_completed", True, "answered")]
    ref = "superset:dataset:5bcf01e3"
    subject = report(
        repetition(0, verdicts=verdicts, source_entries=((ref, "af1865ad"),)),
        repetition(1, verdicts=verdicts, source_entries=((ref, "bbbbbbbb"),)),
    )

    assert not subject.flapping(), "every predicate agreed"
    assert len(subject.distinct_answers()) == 1, "and the answer text is identical"
    assert len(subject.distinct_source_refs()) == 1, "a ref names the asset, so this cannot see it"
    assert len(subject.distinct_source_versions()) == 2
    fields = parse_stability_line(subject.line())
    assert (fields["source_refs_distinct"], fields["source_versions_distinct"]) == ("1", "2")
    rendered = subject.render()
    assert f"EVIDENCE identical across 2 repetitions: {ref}" in rendered
    assert "VERSIONS 2 distinct sets across 2" in rendered
    assert f"reps 0: {ref}@af1865ad" in rendered, (
        "which version each repetition read, as the flapping answers are printed: a count with "
        "the differing versions thrown away cannot be acted on"
    )
    assert f"reps 1: {ref}@bbbbbbbb" in rendered


def test_two_repetitions_that_read_the_same_version_agree_at_both_identities():
    """The world where the pair comes out the other way, so the pair is evidence.

    A version projection that minted a fresh value per repetition -- a read
    timestamp, an id assigned at report time -- would report drift always, and
    the disagreement asserted above would say nothing. Identical bytes must be
    one set at BOTH identities, and the rendered report must not claim a flap.
    """
    verdicts = [("run_completed", True, "answered")]
    ref = "superset:dataset:5bcf01e3"
    subject = report(
        repetition(0, verdicts=verdicts, source_entries=((ref, "af1865ad"),)),
        repetition(1, verdicts=verdicts, source_entries=((ref, "af1865ad"),)),
    )

    fields = parse_stability_line(subject.line())
    assert (fields["source_refs_distinct"], fields["source_versions_distinct"]) == ("1", "1")
    assert "VERSIONS identical across 2 repetitions (sha256:" in subject.render(), (
        "hashed rather than printed once the set held, because the tokens are the refs again "
        "with a hash stapled on -- but printed as SOMETHING, since two reports that each held "
        "steady on different versions are not the same result"
    )


def test_two_ref_sets_that_share_an_identity_are_still_two_version_sets():
    """One walk feeding both projections, at the shape hy-szg4 closed.

    Refs that differ while the identities are shared: the world where a version
    projection sourced separately from the refs could collapse two ref sets into
    one. Both projections read one `source_entries` list, so it cannot.
    """
    verdicts = [("run_completed", True, "answered")]
    shared = "af1865ad"
    subject = report(
        repetition(0, verdicts=verdicts, source_entries=(("dataset:a", shared),)),
        repetition(1, verdicts=verdicts, source_entries=(("dataset:b", shared),)),
    )

    fields = parse_stability_line(subject.line())
    assert (fields["source_refs_distinct"], fields["source_versions_distinct"]) == ("2", "2")


def test_an_at_sign_in_either_half_does_not_collapse_two_version_sets_into_one():
    """The delimiter collision, and the invariant asserted where it used to fail.

    `'@'` is legal in BOTH halves -- `ref` is `asset['ref']` and the identity is
    `asset['content_sha256']`, arbitrary payload strings -- so grouping the
    versions on a joined `ref@identity` token was not injective, and these two
    repetitions were one key. Measured at 2943517: `refs_distinct=2
    versions_distinct=1`, rendering `EVIDENCE 2 distinct sets across 2` directly
    above `VERSIONS identical`, with the report's own refinement inverted.

    The predecessor of this test asserted "versions can never be fewer" over the
    world above, where the claim happens to hold, and so retired a question the
    code could not answer -- worse than no test. Grouping on the PAIR makes the
    refinement true by type, and this is the input that shows it: the ref set is
    the image of the pair set under `ref`, so it can never be the larger.
    """
    verdicts = [("run_completed", True, "answered")]
    subject = report(
        repetition(0, verdicts=verdicts, source_entries=(("dataset:a@b", "c"),)),
        repetition(1, verdicts=verdicts, source_entries=(("dataset:a", "b@c"),)),
    )

    fields = parse_stability_line(subject.line())
    assert (fields["source_refs_distinct"], fields["source_versions_distinct"]) == ("2", "2")
    assert len(subject.distinct_source_versions()) >= len(subject.distinct_source_refs()), (
        "the refinement the line invites the reader to read: versions can never be fewer"
    )
    rendered = subject.render()
    assert "VERSIONS 2 distinct sets across 2" in rendered
    assert "VERSIONS identical" not in rendered, (
        "the render read the same collapsed group, so it called two ref sets one version set"
    )
    assert "reps 0: dataset:a@b@c" in rendered and "reps 1: dataset:a@b@c" in rendered, (
        "the token stays ambiguous for a human to read -- tolerable only because nothing counts it"
    )


def _identical_versions_hash(rendered: str) -> str | None:
    """The hash the identical-versions branch staples on, or None if it did not fire."""
    match = re.search(r"VERSIONS identical across \d+ repetitions \((sha256:[0-9a-f]+)\)", rendered)
    return match.group(1) if match else None


def test_two_reports_steady_on_different_pair_sets_mint_different_cross_run_identities():
    """The CROSS-RUN identity, minted in the identical branch, must not collide (hy-2g54).

    The `@`-sign test above is a COUNT within one report. This is the hash the
    identical branch staples on so that "two reports that each held steady on
    DIFFERENT versions are not the same result" -- a cross-RUN identity, not a
    within-run count. It was taken over `_render_versions`, the joined `ref@identity`
    token, and `'@'` is legal in BOTH halves, so two reports each internally steady
    on pair sets that differ only in where the `'@'` falls minted ONE identity:
    `('dataset:a@b','c')` and `('dataset:a','b@c')` both render `dataset:a@b@c`.
    Measured at 670fa2f: both printed sha256:7b57dab422bc9dad. Hashing the PAIRS --
    each half through `content_hash`, so no `'@'` can shift the boundary -- tells
    them apart.

    Both directions are asserted, because a fix that merely made the hash NOISY
    would pass the inequality and destroy the property. Same pair set -> same
    identity (it is a cross-run identity, reproducible from the recording); different
    pair set -> different identity.
    """
    verdicts = [("run_completed", True, "answered")]
    left = report(
        repetition(0, verdicts=verdicts, source_entries=(("dataset:a@b", "c"),)),
        repetition(1, verdicts=verdicts, source_entries=(("dataset:a@b", "c"),)),
    )
    right = report(
        repetition(0, verdicts=verdicts, source_entries=(("dataset:a", "b@c"),)),
        repetition(1, verdicts=verdicts, source_entries=(("dataset:a", "b@c"),)),
    )
    left_again = report(
        repetition(0, verdicts=verdicts, source_entries=(("dataset:a@b", "c"),)),
        repetition(1, verdicts=verdicts, source_entries=(("dataset:a@b", "c"),)),
    )

    left_identity = _identical_versions_hash(left.render())
    right_identity = _identical_versions_hash(right.render())

    assert left_identity is not None and right_identity is not None, (
        "both reports held steady on one pair set, so both reach the identical-versions branch"
    )
    assert left_identity != right_identity, (
        "two reports steady on DIFFERENT pair sets are not the same result; the hash must be "
        "taken over the pairs, not the joined ref@identity token that collapses a@b|c into a|b@c"
    )
    assert _identical_versions_hash(left_again.render()) == left_identity, (
        "and the SAME pair set mints the SAME identity -- a cross-run identity, not noise"
    )


def test_a_report_whose_evidence_carries_no_version_does_not_read_as_agreement():
    """`VERSIONS identical` from nothing was the finding (hy-szg4).

    `content_sha256=None` is live rather than hypothetical -- `bundle/resolver`
    reads it off `asset.current_version`, which an asset held with no version
    does not have -- and the hashed identical-branch render turned that into a
    64-character token that reads like the identity of something. Same defect
    as the fixture secret scan reporting `passed` without its inputs (hy-jnem):
    a vacuous comparison rendered as a result.
    """
    verdicts = [("run_completed", True, "answered")]
    subject = report(
        repetition(0, verdicts=verdicts, source_entries=(("dataset:a", UNVERSIONED),)),
        repetition(1, verdicts=verdicts, source_entries=(("dataset:a", UNVERSIONED),)),
    )

    fields = parse_stability_line(subject.line())
    assert (fields["source_versions_distinct"], fields["unversioned"]) == ("1", "1"), (
        "one set, and the line says how much of that set was an identity at all"
    )
    rendered = subject.render()
    assert "VERSIONS vacuous across 2 repetitions" in rendered
    assert "VERSIONS identical" not in rendered
    assert "UNVERSIONED 1: dataset:a" in rendered, (
        "which ref carried no version, as the flapping explanations are printed: the count alone "
        "does not say what to go and look at"
    )


def test_an_identity_that_merely_ends_in_the_marker_is_not_a_vacuous_comparison():
    """The line and its own prose read the same field, or they contradict (hy-q2mn).

    `render` decided vacuity with `token.endswith('@unversioned')` while
    `unversioned_refs` compares `identity == UNVERSIONED`, so an identity ending
    in the marker -- a real `content_sha256` value as far as any type here is
    concerned -- split them. Measured at 2943517: the line said `unversioned=0`
    and the prose above it said "every entry the repetitions read is
    unversioned". One entry did carry an identity, so the comparison happened and
    the identical branch is the correct one.
    """
    verdicts = [("run_completed", True, "answered")]
    entries = (("dataset:a", "b@unversioned"),)
    subject = report(
        repetition(0, verdicts=verdicts, source_entries=entries),
        repetition(1, verdicts=verdicts, source_entries=entries),
    )

    fields = parse_stability_line(subject.line())
    assert (fields["source_versions_distinct"], fields["unversioned"]) == ("1", "0")
    rendered = subject.render()
    assert "VERSIONS identical across 2 repetitions (sha256:" in rendered
    assert "VERSIONS vacuous" not in rendered, (
        "the prose claimed every entry was unversioned while the line counted none as such"
    )
    assert "UNVERSIONED" not in rendered


def test_a_report_with_no_evidence_at_all_says_the_version_comparison_was_vacuous():
    """`<none>` hashed is a token that identifies the absence of a comparison.

    The refs branch prints `<none>` plainly and the versions branch hashed it,
    so a run that recorded no evidence printed a hash beside the word
    `identical`. Nothing was compared, and the render says so.
    """
    verdicts = [("run_completed", True, "answered")]
    subject = report(repetition(0, verdicts=verdicts), repetition(1, verdicts=verdicts))

    fields = parse_stability_line(subject.line())
    assert (fields["source_versions_distinct"], fields["unversioned"]) == ("1", "0"), (
        "no entry carried a version because there were no entries -- distinct from an entry that "
        "carried none, which `unversioned` counts"
    )
    rendered = subject.render()
    assert "VERSIONS none across 2 repetitions" in rendered
    assert "VERSIONS identical" not in rendered
    assert "UNVERSIONED" not in rendered


def test_a_partly_unversioned_report_still_names_what_carried_no_version():
    """Agreement over two entries where one could not have disagreed.

    The identical branch is correct here -- one entry did carry an identity --
    and a report that stopped there would let the reader believe both refs were
    compared. So the disclosure is printed in every branch, not only when the
    whole comparison was vacuous.
    """
    verdicts = [("run_completed", True, "answered")]
    entries = (("dataset:a", "af1865ad"), ("dataset:b", UNVERSIONED))
    subject = report(
        repetition(0, verdicts=verdicts, source_entries=entries),
        repetition(1, verdicts=verdicts, source_entries=entries),
    )

    fields = parse_stability_line(subject.line())
    assert (fields["source_versions_distinct"], fields["unversioned"]) == ("1", "1")
    rendered = subject.render()
    assert "VERSIONS identical across 2 repetitions (sha256:" in rendered
    assert "UNVERSIONED 1: dataset:b" in rendered


def test_a_report_whose_repetitions_all_rested_on_nothing_says_so():
    """An empty set is a real value, not a missing one, so it prints as a value.

    The committed recordings carry refs -- four per governed case, two per raw
    baseline -- so a report reading `<none>` is a claim about the run, and a
    blank after the colon would read as a rendering bug instead.
    """
    subject = report(
        repetition(0, verdicts=[("run_completed", True, "answered")]),
        repetition(1, verdicts=[("run_completed", True, "answered")]),
    )

    assert parse_stability_line(subject.line())["source_refs_distinct"] == "1"
    assert "EVIDENCE identical across 2 repetitions: <none>" in subject.render()


def test_identical_answers_are_reported_as_a_hash_rather_than_twice():
    subject = report(
        repetition(0, verdicts=[("run_completed", True, "answered")]),
        repetition(1, verdicts=[("run_completed", True, "answered")]),
    )

    rendered = subject.render()

    assert "ANSWERS identical across 2 repetitions (sha256:" in rendered
    assert "every predicate agreed across 2 repetitions" in rendered


def test_the_line_names_its_own_inputs_and_says_it_is_not_a_gate():
    """N, the arm, the case, the model pin and the SHA, on one pasteable line.

    Two stability reports that cannot be compared reproduce the defect the
    gate's evidence line closed one layer up, and `result=REPORT-ONLY` is on
    the line because a number that looks like a gate's is eventually treated
    as one.
    """
    subject = report(
        repetition(0, verdicts=[("run_completed", True, "answered"), ("rules", True, "stated")]),
        repetition(1, verdicts=[("run_completed", True, "answered"), ("rules", False, "omitted")]),
    )

    line = subject.line()

    assert "\n" not in line
    fields = parse_stability_line(line)
    assert fields["sha"] == "a" * 40
    assert fields["arm"] == GOVERNED_ARM
    assert fields["case"] == "revenue_by_region"
    assert fields["n"] == "2"
    assert fields["model"] == repository_pins(GOVERNED_ARM)["model"]
    assert fields["seed"] == str(repository_pins(GOVERNED_ARM)["seed"])
    assert fields["prompt_hash"] == repository_pins(GOVERNED_ARM)["prompt_hash"]
    assert fields["tools_hash"] == repository_pins(GOVERNED_ARM)["tools_hash"]
    assert fields["predicates"] == "2"
    assert fields["unanimous"] == "1"
    assert fields["flapping"] == "1"
    assert fields["answers_distinct"] == "1"
    assert fields["source_refs_distinct"] == "1"
    assert fields["unversioned"] == "0"
    assert fields["result"] == RESULT


def test_repetitions_under_different_pins_are_refused_rather_than_averaged():
    """A re-roll measured as instability would be a wrong answer to hy-hk5m.

    hy-hk5m measured that a cosmetic prompt or tool-schema edit moves
    `prompt_hash` and re-rolls the arm. Repetitions spanning that edit are two
    different runs, and a report that averaged them would name variance as the
    cause of a difference the byte-string produced.
    """
    with pytest.raises(PinsDrifted) as excinfo:
        report(
            repetition(0, verdicts=[("run_completed", True, "answered")]),
            repetition(
                1, verdicts=[("run_completed", False, "died")], prompt_hash="sha256:something_else"
            ),
        )

    assert "prompt_hash" in str(excinfo.value)


def test_a_report_over_zero_repetitions_is_refused():
    with pytest.raises(ValueError):
        report()


def test_the_repetition_count_defaults_to_three_and_is_configurable():
    """Three is the smallest N where a minority verdict differs from a split."""
    assert DEFAULT_REPETITIONS == 3
    assert configured_repetitions({}) == 3
    assert configured_repetitions({REPETITIONS_ENV: "5"}) == 5
    assert configured_repetitions({REPETITIONS_ENV: "  "}) == 3

    with pytest.raises(ValueError):
        configured_repetitions({REPETITIONS_ENV: "0"})
    with pytest.raises(ValueError):
        configured_repetitions({REPETITIONS_ENV: "many"})


def test_benchmark_md_documents_the_line_this_module_prints():
    """The format lives in one place, and the document is bound to it.

    A report format documented as prose drifts from the code that emits it,
    and the reader who then compares two lines is comparing a document to a
    memory. Asserted against the constants, so renaming a field here without
    touching the document is red.
    """
    doc = (
        Path(__file__).resolve().parents[3] / "docs" / "development" / "benchmark.md"
    ).read_text()

    assert STABILITY_LINE_PREFIX in doc
    assert REPETITIONS_ENV in doc
    assert RESULT in doc
    assert "REPORT-ONLY" in doc, (
        "the literal word, not only the constant: repurposing RESULT moves both sides of the "
        "assertion above together, so the one thing that makes it visible is that the new word "
        "has to be retyped into the document"
    )
    assert "hy-hk5m" in doc, "the document must say which question this report does not answer"
    assert "hash_basis" in doc, (
        "the raw arm's identity is version-level with the store's narrowing, not byte-level: a "
        "document that calls it a content hash of the payload retires the question of what a "
        "re-sync can move without either count noticing (hy-szg4)"
    )
    assert "hy-9dyv" in doc, (
        "the document must name the bead for what the report still does NOT compare -- the "
        "trace -- rather than implying it compares it. hy-jy2h added source_refs_distinct, so "
        "naming hy-jy2h here would point the reader at a closed bead"
    )

    documented = [
        candidate for candidate in doc.splitlines() if candidate.startswith(STABILITY_LINE_PREFIX)
    ]
    assert len(documented) == 1, "one template, so there is one thing to keep true"
    emitted = report(
        repetition(0, verdicts=[("run_completed", True, "answered")]),
        repetition(1, verdicts=[("run_completed", True, "answered")]),
    ).line()
    assert list(parse_stability_line(documented[0])) == list(parse_stability_line(emitted)), (
        "every field this module prints, in the order it prints them. Asserting one field name "
        "at a time is how a field gets added without the template moving; comparing key ORDER "
        "rather than a set is because the document's purpose is that two pasted lines diff "
        "readably"
    )


CASE = Case(
    id="revenue_by_region",
    family="governed_fetch",
    question="What is recognized revenue by customer region?",
    expected_domain="revenue",
    must_cite=(),
    must_not_cite=(),
    must_state=("SUM(gross_amount - tax_amount)",),
    requires_plan_validation=False,
    reason="",
)


def recording_of(
    answer: str,
    *,
    arm=GOVERNED_ARM,
    case_id="revenue_by_region",
    run_id="b" * 32,
    git_commit="a" * 40,
    task_version="revenue@1",
    source_refs=None,
    observed_assets=(),
    **pin_overrides,
) -> Recording:
    """A recording with one planner message, and linked evidence when asked.

    Enough for the scorers to produce verdicts and for the report to pull the
    answer out, and no model anywhere near it. `observed_assets` goes into the
    TRACE rather than beside it, because that is where both identities are
    derived from and a shortcut here would test the shortcut.

    `source_refs` DEFAULTS TO WHAT THE TRACE SAYS, which is what `run_case`
    persists, so the helper cannot casually build a recording production could
    not produce. The test about a recording false about its own evidence passes
    the field explicitly.
    """
    evidence_steps = [
        {
            "kind": TOOL_RESULT,
            "at": "2026-07-29T00:00:00+00:00",
            "detail": {
                "operation": "resolve_context",
                "result": {"linked_evidence": {"observed_assets": list(observed_assets)}},
            },
            "summary": "",
        }
    ]
    return Recording(
        run_id=run_id,
        schema_version=RECORDING_SCHEMA_VERSION,
        arm=arm,
        case_id=case_id,
        task_version=task_version,
        git_commit=git_commit,
        recorded_at="2026-07-29T00:00:00+00:00",
        pins=pins(**{**repository_pins(arm), **pin_overrides}),
        trace={
            "provenance": {"runtime": "openai_agents_sdk"},
            "steps": [
                {
                    "kind": PLANNER_MESSAGE,
                    "at": "2026-07-29T00:00:00+00:00",
                    "detail": {"text": answer},
                    "summary": "",
                }
            ]
            + (evidence_steps if observed_assets else []),
        },
        source_refs=(
            sorted({asset["ref"] for asset in observed_assets})
            if source_refs is None
            else list(source_refs)
        ),
    )


def test_the_evidence_a_repetition_rested_on_comes_off_the_recording():
    """Critic's own two recordings, alike in everything the report compared.

    One thin (no evidence), one fat (a dataset ref), same answer text and the
    same verdicts -- the case the coarse count exists for. The evidence lives in
    the recording's trace, which is what `run_case` derives the persisted field
    from, so this is the path that decides whether the count measures the run or
    a default.
    """
    other = "dataset:urn:li:dataset:other_source"
    subject = stability_report(
        [
            recording_of("revenue is SUM(gross_amount - tax_amount)"),
            recording_of(
                "revenue is SUM(gross_amount - tax_amount)",
                observed_assets=[{"ref": other, "content_sha256": "af1865ad"}],
            ),
        ],
        CASE,
    )

    assert not subject.flapping()
    assert len(subject.distinct_answers()) == 1
    assert [repetition.source_refs for repetition in subject.repetitions] == [(), (other,)]
    assert parse_stability_line(subject.line())["source_refs_distinct"] == "2"


def test_a_recording_false_about_its_own_evidence_is_refused():
    """A recording that persists refs its trace does not walk to (hy-szg4).

    This is the shape the critic's probe used to print `source_refs_distinct=2
    source_versions_distinct=1`: the persisted field carried a ref the trace
    never mentions, the coarse count read the field and the finer count read the
    trace, and the pair on the line described two different sets while inviting
    the reader to compare them. Refused rather than silently re-derived, because
    `Recording.source_refs` is the field GitHub #25 requires a recording to
    carry: a recording whose field and trace disagree is false about itself, and
    reporting the trace's answer would hide that instead of surfacing it.
    """
    answer = "revenue is SUM(gross_amount - tax_amount)"
    with pytest.raises(EvidenceMismatch) as excinfo:
        stability_report(
            [
                recording_of(answer, source_refs=["dataset:a", "dataset:b"]),
                recording_of(answer, source_refs=["dataset:a", "dataset:b"]),
            ],
            CASE,
        )

    assert "dataset:a" in str(excinfo.value) and "repetition 0" in str(excinfo.value), (
        "both sets and which repetition, as PinsDrifted names every field that moved"
    )


def test_both_counts_come_off_one_walk_of_the_recordings_trace():
    """The fix for the inversion, at the level that produces the numbers.

    Two recordings that rest on DIFFERENT assets, each consistent with its own
    persisted field. Under the old arrangement the coarse count came from the
    persisted field and the finer one from a separate walk; both come off one
    walk now, so the pair cannot invert -- asserted here as the refinement
    holding on the recording path and not only on hand-built repetitions.
    """
    answer = "revenue is SUM(gross_amount - tax_amount)"
    subject = stability_report(
        [
            recording_of(
                answer, observed_assets=[{"ref": "dataset:a", "content_sha256": "af1865ad"}]
            ),
            recording_of(
                answer, observed_assets=[{"ref": "dataset:b", "content_sha256": "af1865ad"}]
            ),
        ],
        CASE,
    )

    fields = parse_stability_line(subject.line())
    assert (fields["source_refs_distinct"], fields["source_versions_distinct"]) == ("2", "2")


def test_a_recording_whose_evidence_has_no_version_is_reported_as_vacuous():
    """`content_sha256=None` off the resolver, carried to the render (hy-szg4).

    `bundle/resolver` reads `content_sha256`, `observed_version` and
    `observed_version_id` off `asset.current_version`, so an asset held with no
    current version arrives with all three `None` -- there is nothing finer in
    the payload to promote, and the report's job is to say the comparison was
    vacuous rather than to print `identical` and a hash.
    """
    answer = "revenue is SUM(gross_amount - tax_amount)"
    asset = {"ref": "dataset:a", "content_sha256": None, "observed_version": None}
    subject = stability_report(
        [
            recording_of(answer, observed_assets=[asset]),
            recording_of(answer, observed_assets=[asset]),
        ],
        CASE,
    )

    fields = parse_stability_line(subject.line())
    assert (fields["source_refs_distinct"], fields["unversioned"]) == ("1", "1")
    assert "VERSIONS vacuous across 2 repetitions" in subject.render()


def test_the_version_identity_is_derived_from_the_recordings_own_trace():
    """The path that decides whether the finer count measures a run or a default.

    Two recordings alike in the answer, the verdicts AND the persisted
    `source_refs` -- one asset re-observed between them, which the recording
    carries only inside its trace. Derived here rather than persisted, because
    `Recording.source_refs` is what GitHub #25 requires a recording to carry
    and a report wanting a finer identity does not get to redefine it; derived
    from the recording's own trace rather than from a store, because a version
    fetched at report time would be today's answer about yesterday's run.
    """
    ref = "superset:dataset:5bcf01e3-3f70-50d2-bb31-562b627b09b8"
    answer = "revenue is SUM(gross_amount - tax_amount)"
    subject = stability_report(
        [
            recording_of(
                answer,
                source_refs=[ref],
                observed_assets=[{"ref": ref, "content_sha256": "af1865ad", "observed_version": 1}],
            ),
            recording_of(
                answer,
                source_refs=[ref],
                observed_assets=[{"ref": ref, "content_sha256": "bbbbbbbb", "observed_version": 2}],
            ),
        ],
        CASE,
    )

    assert not subject.flapping()
    assert len(subject.distinct_answers()) == 1
    assert [repetition.source_refs for repetition in subject.repetitions] == [(ref,), (ref,)]
    assert [repetition.source_entries for repetition in subject.repetitions] == [
        ((ref, "af1865ad"),),
        ((ref, "bbbbbbbb"),),
    ]
    fields = parse_stability_line(subject.line())
    assert (fields["source_refs_distinct"], fields["source_versions_distinct"]) == ("1", "2")


def test_the_whole_path_from_recordings_to_a_report_runs_without_a_model():
    """Scoring each repetition, pulling the answer, comparing -- all of it.

    This is the logic the live job would otherwise be the only exercise of.
    Two repetitions whose answers differ in one clause: the report must carry
    both answers and score each repetition separately.
    """
    subject = stability_report(
        [
            recording_of("revenue is SUM(gross_amount - tax_amount) by region"),
            recording_of("revenue is by region"),
        ],
        CASE,
    )

    assert subject.arm == GOVERNED_ARM
    assert subject.case_id == "revenue_by_region"
    assert subject.git_commit == "a" * 40
    assert len(subject.repetitions) == 2
    assert all(repetition.scores for repetition in subject.repetitions)
    assert len(subject.distinct_answers()) == 2
    assert "governed_rules_stated" in {entry.predicate for entry in subject.flapping()}, (
        "one answer stated the rule and the other did not"
    )


def test_repetitions_at_two_commits_are_refused_rather_than_labelled_with_the_first():
    """Three commits in one report, and the line names only one.

    Not `RunPins`, so `StabilityReport.__post_init__` cannot see it: the commit
    used to come from `git rev-parse HEAD` in a subprocess inside `run_case`,
    which a local re-record spanning a checkout moves mid-run. Three repetitions
    at three commits were accepted as one report whose `sha=` named repetition
    0 -- a line false about its own inputs, which is the class
    `MixedRepetitions` already refuses.

    `run_case` now pins one commit per session and refuses a HEAD that moved
    (hy-r1i0), so this is no longer the first check to see that drift. It stays
    because this reader scores recordings it did not produce, including the four
    on disk, which were produced by a caller that moved.
    """
    with pytest.raises(PinsDrifted) as excinfo:
        stability_report(
            [
                recording_of("an answer", git_commit="a" * 40),
                recording_of("an answer", git_commit="b" * 40),
                recording_of("an answer", git_commit="c" * 40),
            ],
            CASE,
        )

    assert "git_commit" in str(excinfo.value)
    assert "repetition 1" in str(excinfo.value), "the message must name which repetition moved"


def test_repetitions_that_sat_two_different_exams_are_refused():
    """`task_version` content-hashes the case file, re-read per repetition.

    Under `HYPERSET_RECORD=1` this drift is the one that costs an artifact:
    repetition 0 answered exam A and the tree now asks exam B, so the hour of
    inference would land on a recording `refuse_a_different_exam` refuses later,
    with nothing in the loop saying so.
    """
    with pytest.raises(PinsDrifted) as excinfo:
        stability_report(
            [
                recording_of("an answer", task_version="revenue@1"),
                recording_of("an answer", task_version="revenue@2-loosened"),
            ],
            CASE,
        )

    assert "task_version" in str(excinfo.value)


def test_recordings_of_two_arms_are_not_one_report():
    with pytest.raises(MixedRepetitions):
        stability_report([recording_of("a"), recording_of("a", arm=RAW_ARM)], CASE)


def test_a_report_over_no_recordings_is_refused():
    with pytest.raises(ValueError):
        stability_report([], CASE)


def test_the_recording_is_written_only_after_the_repetitions_agreed_on_their_pins():
    """A mid-run re-pull must not leave a refreshed recording behind a red run.

    Each repetition asserts the six repository pins by value on its own, but
    the three host pins are checked for presence only, so a model re-pull or a
    server upgrade between repetitions is drift that only the cross-repetition
    check sees. Writing inside the loop meant that check ran after the artifact
    was already on disk.

    The model is faked here and the ordering is not: what is asserted is that
    the write did not happen, which no property of the fake can produce.
    """
    written: list[Recording] = []
    drifted = [
        recording_of("an answer"),
        recording_of("an answer", digest="sha256:a_republished_digest"),
    ]

    with pytest.raises(PinsDrifted):
        repeat_and_report(lambda index: drifted[index], CASE, repetitions=2, record=written.append)

    assert written == [], "a recording was committed before the drift was detected"


def test_the_first_repetition_is_the_one_recorded_once_the_set_held():
    written: list[Recording] = []
    accepted = [recording_of("first"), recording_of("second")]

    report = repeat_and_report(
        lambda index: accepted[index], CASE, repetitions=2, record=written.append
    )

    assert [recording.trace for recording in written] == [accepted[0].trace]
    assert len(report.repetitions) == 2


def test_nothing_is_written_when_no_recorder_was_passed():
    """`HYPERSET_RECORD` unset is the scheduled job's state and the default."""
    report = repeat_and_report(lambda index: recording_of("an answer"), CASE, repetitions=2)

    assert len(report.repetitions) == 2


# The identity fields, and the canonicaliser underneath them (hy-hk5m, hy-wwk3).
#
# Every count above answers "did this session hold together". These answer "what
# did it hold together ON", which is what a second session can be compared
# against -- and the canonicaliser is where a false agreement would hide: a
# normaliser that strips one thing too many makes two genuinely different
# observations identical, and every arm above still passes.


def test_the_line_carries_an_identity_for_every_axis_it_counts():
    """A count without an identity beside it is what two divergent sessions
    print identically (hy-hk5m): each of the three sessions measured there was
    one internally unanimous session, so all three printed the same counts."""
    fields = parse_stability_line(
        report(
            repetition(0, verdicts=[("run_completed", True, "answered")]),
        ).line()
    )

    assert {"verdicts", "traces", "answers_id", "evidence_id"} <= set(fields)
    assert fields["trace_shapes_distinct"] == "1"


def test_an_answer_differing_only_in_trailing_whitespace_is_a_different_identity():
    """The canonicaliser strips NOTHING. Whitespace-stripping is the most
    plausible normalisation to add and it is a false-agreement generator: two
    sessions whose answers differ are two sessions, and an identity that agrees
    more often than the content does reports agreement with the authority of a
    hash."""
    plain = report(repetition(0, verdicts=[("run_completed", True, "a")], answer="the answer"))
    padded = report(repetition(0, verdicts=[("run_completed", True, "a")], answer="the answer "))

    assert plain.answers_identity() != padded.answers_identity()


def test_two_answers_do_not_hash_alike_as_one_answer_that_spans_them():
    """The delimiter collision, at this module's own level (hy-q2mn one over).

    An answer contains newlines by construction, so an identity that joined the
    values under any separator would map one session that answered `"A\\nB"` and
    one that answered `"A"` and `"B"` to the same token -- a false AGREEMENT
    arriving through the join rather than through the content. Each value is
    hashed before anything is joined, and a content hash holds no separator.
    """
    spanning = report(repetition(0, verdicts=[("run_completed", True, "a")], answer="A\nB"))
    two = report(
        repetition(0, verdicts=[("run_completed", True, "a")], answer="A"),
        repetition(1, verdicts=[("run_completed", True, "a")], answer="B"),
    )

    assert spanning.answers_identity() != two.answers_identity()


def test_the_evidence_identity_does_not_merge_two_sets_through_a_rendering():
    """The same argument one layer down. `_render_refs` joins with `", "`, so an
    identity taken over the RENDERED set would map `{"a, b"}` and `{"a", "b"}`
    to one token. The identity is nested -- refs hashed, then sets hashed -- so
    a ref holding the separator cannot merge two sets."""
    one_ref = report(repetition(0, verdicts=[("run_completed", True, "a")], source_refs=("a, b",)))
    two_refs = report(
        repetition(0, verdicts=[("run_completed", True, "a")], source_refs=("a", "b"))
    )

    assert one_ref.evidence_identity() != two_refs.evidence_identity()


def test_the_verdicts_field_names_the_predicate_and_not_only_the_verdict():
    """Two sessions that failed ONE predicate each, and not the same one. The
    verdict multiset is identical, so a field carrying only the verdicts would
    call these one behaviour -- and which predicate failed is the finding the
    benchmark publishes."""
    evidence_failed = report(
        repetition(
            0,
            verdicts=[("evidence_cited", False, "no"), ("governed_rules_stated", True, "yes")],
        )
    )
    rules_failed = report(
        repetition(
            0,
            verdicts=[("evidence_cited", True, "yes"), ("governed_rules_stated", False, "no")],
        )
    )

    assert evidence_failed.verdicts_field() != rules_failed.verdicts_field()
    assert "evidence_cited:fail" in evidence_failed.verdicts_field()


def test_a_predicate_that_flapped_prints_both_verdicts_rather_than_the_first():
    """A session that flapped and a session that did not are different
    behaviours, and the field has to say so: keeping only one verdict per
    predicate would make the flapping session compare equal to whichever steady
    session shared its first repetition."""
    flapped = report(
        repetition(0, verdicts=[("evidence_cited", True, "yes")]),
        repetition(1, verdicts=[("evidence_cited", False, "no")]),
    )
    steady = report(
        repetition(0, verdicts=[("evidence_cited", True, "yes")]),
        repetition(1, verdicts=[("evidence_cited", True, "yes")]),
    )

    assert flapped.verdicts_field() == "evidence_cited:fail|pass"
    assert flapped.verdicts_field() != steady.verdicts_field()


def test_the_trace_axis_is_not_normalised_to_a_set_the_way_the_evidence_is():
    """`_group` normalises with `sorted(set(...))`, which is right for evidence
    and would answer this axis's own question wrong: `catalog>resolve` and
    `resolve>catalog` are two behaviours, and a repeated call is not one call.
    hy-hk5m's observed flip -- validate called, then not -- would read as
    agreement through that helper."""
    forwards = report(
        repetition(0, verdicts=[("run_completed", True, "a")], trace_shape=("catalog", "resolve"))
    )
    backwards = report(
        repetition(0, verdicts=[("run_completed", True, "a")], trace_shape=("resolve", "catalog"))
    )
    twice = report(
        repetition(
            0,
            verdicts=[("run_completed", True, "a")],
            trace_shape=("resolve", "resolve"),
        )
    )
    once = report(repetition(0, verdicts=[("run_completed", True, "a")], trace_shape=("resolve",)))

    assert forwards.traces_field() != backwards.traces_field()
    assert twice.traces_field() != once.traces_field()


def test_two_repetitions_that_took_different_paths_to_one_answer_are_visible():
    """The gap hy-9dyv was filed on, closed at the report level: identical
    verdicts, identical answer, identical evidence, different tools."""
    subject = report(
        repetition(
            0,
            verdicts=[("run_completed", True, "a")],
            trace_shape=("catalog", "resolve", "validate"),
        ),
        repetition(1, verdicts=[("run_completed", True, "a")], trace_shape=("catalog", "resolve")),
    )

    assert len(subject.distinct_trace_shapes()) == 2
    assert "trace_shapes_distinct=2" in subject.line()
    assert "TRACE 2 distinct shapes" in subject.render()
    assert "catalog>resolve>validate" in subject.render()


def test_a_v1_line_is_refused_by_its_version_and_not_by_a_missing_field():
    """A reader that accepted a v1 line and found `answers_id` absent would be
    one `.get()` from comparing two absences and calling them equal."""
    v1 = (
        report(repetition(0, verdicts=[("run_completed", True, "a")]))
        .line()
        .replace(" v2 ", " v1 ")
    )

    with pytest.raises(UnreadableStabilityLine) as raised:
        parse_stability_line(v1)

    assert "v1" in str(raised.value) and "v2" in str(raised.value)


def test_something_that_is_not_a_stability_line_says_so_differently():
    """The two refusals are not one refusal: a line from an older version is a
    measurement of fewer things, and `cross_session` reports the difference."""
    with pytest.raises(UnreadableStabilityLine) as raised:
        parse_stability_line("HYPERSET-GATE v2 collected=848 passed=844")

    assert "not a stability line" in str(raised.value)
