"""Two sessions compared, on recordings that really disagreed (hy-hk5m, hy-wwk3).

THE FIXTURES ARE REAL RUNS AND NOT SUBSTRATE EVIDENCE. Every file under
`fixtures/cross_session/` is a byte-for-byte copy of a recording this repository
committed, taken from git and unedited:

    diverged_session_a  blob ed56b042  recorded at commit b10d7527
    diverged_session_b  blob 7a991b6a  recorded at commit 9be7c977
    diverged_session_c  blob f3570da4  recorded at commit 3449ea4d
    steady_session_a    blob b780f6f0  recorded at commit b10d7527
    steady_session_b    blob 7e430031  recorded at commit 9be7c977
    steady_session_c    blob 547fcb2e  recorded at commit 3449ea4d

The diverged three are `raw_baseline/supply_chain_lead_time`, the steady three
are `raw_baseline/revenue_by_region`, and all six ran at one prompt hash, one
tool-schema hash, one model, one window, one seed, one temperature and one
`task_version`. The diverged three produced THREE different answers and one of
them made a different number of tool calls; the steady three agree on every
axis. Nothing about that is arranged: it is what a sweep of every recording in
this repository's history found.

WHAT THEY ARE NOT. They are three sessions at three COMMITS, not three sessions
at one, so they are not evidence about the substrate and must not reach #25's
release sheet as a measurement -- a code change between two commits is a
standing alternative explanation for any disagreement between them. They are
evidence about THIS COMPARATOR: that it reports DISAGREE when two sessions
really diverged, and AGREE when they really did not, rather than passing a suite
made entirely of refusals.

WHY `git_commit` IS EQUALISED HERE RATHER THAN IN THE COMMITTED FILE. hy-wwk3
asked for the pair frozen with the commit equalised in the fixture. It is
equalised in `at_one_tree` instead, one named function whose docstring says what
it fakes, and the files keep the bytes the repository actually wrote. Two
reasons: a doctored recording on disk is exactly the artifact the recording
checks exist to refuse, and committing both an equalised and an un-equalised
copy would be the same six runs twice. The un-equalised form is the same file
read without that call, which is what the CANNOT-COMPARE arm below uses.

WHY A CROSS-SESSION FIXTURE STILL HAS TO BE ASSEMBLED, and storage is no longer
the reason. `run.recording_path` is `recordings/<arm>/<case>/<run_id>.json` and
the store is keyed on the run, so two sessions of one case at one tree do coexist
(hy-qc4u); until that bead they could not, and this fixture predates it. What has
not changed is that a DISAGREEMENT cannot be commissioned. These six were found
by sweeping every recording in this repository's history, which is where the
divergence already was, and asking a live pair at one tree to disagree is asking
the model to flap on demand. The diverged three sitting at three commits is a
consequence of where they were found rather than a choice, and it is what
`at_one_tree` equalises. A live disagreeing pair at one tree is now storable, and
if one is ever observed it replaces these files.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from hyperset.evals.cases import load_cases
from hyperset.evals.cross_session import (
    AGREE,
    AXES,
    CANNOT_COMPARE,
    COMPARABILITY_KEY,
    DISAGREE,
    Agreed,
    CannotCompare,
    Disagreed,
    compare_sessions,
)
from hyperset.evals.recording import Recording
from hyperset.evals.stability import (
    STABILITY_LINE_VERSION,
    StabilityReport,
    stability_report,
)

FIXTURES = Path(__file__).parent / "fixtures" / "cross_session"

ONE_TREE = "e" * 40
"""The commit the equalised sessions are pretended to share. Not any real
commit: a value that resolves would invite someone to read the fixture as a
measurement taken there."""

CASES = {case.id: case for case in load_cases()}


def session(name: str) -> StabilityReport:
    """One committed recording, scored, as a one-repetition session."""
    recording = Recording.read(FIXTURES / f"{name}.json")
    return stability_report([recording], CASES[recording.case_id])


def on_another_ollama(name: str, version: str) -> StabilityReport:
    """The same recording as if it had run against a different Ollama build.

    The one host pin the digest does not determine, moved on the recording
    rather than on the rendered line, so the arm below exercises the path a
    real second session takes: pins in, line out, comparator reading the line.
    """
    recording = Recording.read(FIXTURES / f"{name}.json")
    drifted = dataclasses.replace(
        recording, pins=dataclasses.replace(recording.pins, ollama_version=version)
    )
    return stability_report([drifted], CASES[recording.case_id])


def at_one_tree(report: StabilityReport) -> StabilityReport:
    """The same session with its commit replaced by `ONE_TREE`.

    THIS IS THE ONE FAKE IN THIS FILE and it is here so it is legible. The
    recordings ran at three different commits; the comparator refuses that, and
    rightly, so the only way to exercise the AGREE and DISAGREE paths on real
    runs is to tell it they shared a tree. Everything else on the line --
    verdicts, trace shapes, answers, evidence, every pin -- is what the runs
    actually produced.
    """
    return dataclasses.replace(report, git_commit=ONE_TREE)


def test_sessions_that_really_diverged_are_reported_as_DISAGREE():
    """The arm the whole change exists to make possible.

    Without it every arm here asserts a refusal, and a comparator that can never
    fire passes its own suite (hy-wwk3). These three runs answered the same
    question three different ways and one of them made a different number of
    tool calls, at pins that did not move.
    """
    result = compare_sessions(
        [at_one_tree(session(name)).line() for name in ("diverged_session_a", "diverged_session_b")]
    )

    assert isinstance(result, Disagreed)
    assert result.outcome == DISAGREE
    assert "answers_id" in result.moved


def test_the_diverged_trio_moves_on_the_trace_axis_that_v1_could_not_see():
    """`diverged_session_c` made one fewer `get_raw_asset` call. On a v1 line
    that is invisible: each session was one repetition, so all three printed
    `answers_distinct=1` and no trace field at all."""
    result = compare_sessions(
        [at_one_tree(session(name)).line() for name in ("diverged_session_a", "diverged_session_c")]
    )

    assert isinstance(result, Disagreed)
    assert "traces" in result.moved
    assert "list_raw_assets>list_raw_assets>get_raw_asset>get_raw_asset" in result.render()
    assert "list_raw_assets>list_raw_assets>get_raw_asset" in result.render()


def test_a_disagreement_the_verdicts_alone_would_have_missed():
    """All three diverged sessions score identically. A comparator over
    predicate verdicts -- the thing the benchmark publishes -- would have called
    them one result, which is why there are four axes and not one."""
    lines = [
        at_one_tree(session(name)).line()
        for name in ("diverged_session_a", "diverged_session_b", "diverged_session_c")
    ]
    result = compare_sessions(lines)

    assert isinstance(result, Disagreed)
    assert "verdicts" not in result.moved
    assert set(result.moved) >= {"traces", "answers_id"}


def test_sessions_that_really_agreed_are_reported_as_AGREE():
    """The other half of a two-sided instrument. These three ran the same case
    at three commits and produced the same answer, the same shape, the same
    evidence and the same verdicts."""
    result = compare_sessions(
        [
            at_one_tree(session(name)).line()
            for name in ("steady_session_a", "steady_session_b", "steady_session_c")
        ]
    )

    assert isinstance(result, Agreed)
    assert result.outcome == AGREE
    assert all(axis in result.render() for axis in AXES)


def test_the_same_pair_at_its_own_two_commits_is_CANNOT_COMPARE():
    """What proves the key was not widened by accident (hy-wwk3).

    These are the same two runs the DISAGREE arm uses. Read as recorded -- two
    commits -- the comparator refuses them, because a code change between two
    trees is an alternative explanation for any difference, and `SCHEMA_VERSION`
    is not even a repository pin (hy-5e19). The refusal is the honest answer and
    it is a first-class one.
    """
    result = compare_sessions(
        [session(name).line() for name in ("diverged_session_a", "diverged_session_b")]
    )

    assert isinstance(result, CannotCompare)
    assert result.outcome == CANNOT_COMPARE
    assert "sha" in result.render()


def test_a_v1_line_is_refused_by_its_version_rather_than_read_as_agreement():
    """The most important refusal. A v1 line carries no identity per axis, so a
    reader that accepted it would compare two absent fields and find them equal
    -- silence read as assent.

    ASSERTED ON THE REASON, not on the rendering, because the rendering was a
    check that could not fail. This arm first said `"v1" in result.render()`,
    and a mutant that removed the version check from `parse_stability_line`
    PASSED it: every comparison line begins `HYPERSET-CROSS-SESSION v1`, so the
    substring was the comparator's own version. The refusal a v1 line must get
    is the version one, arriving before the missing fields are noticed.
    """
    v1 = (
        "HYPERSET-STABILITY v1 sha=" + "a" * 40 + " arm=governed case=revenue_by_region n=3 "
        "model=qwen2.5:7b digest=sha256:1 context_window=32768 seed=20260728 temperature=0.0 "
        "prompt_hash=sha256:2 tools_hash=sha256:3 predicates=7 unanimous=7 flapping=0 "
        "answers_distinct=1 source_refs_distinct=1 result=REPORT-ONLY"
    )
    result = compare_sessions([v1, at_one_tree(session("steady_session_a")).line()])

    assert isinstance(result, CannotCompare)
    assert result.detail == (
        f"stability line version v1 is not {STABILITY_LINE_VERSION}: a v1 line carries no "
        f"identity per axis, and its missing fields must not read as agreement -- re-run the "
        f"report at this commit to get a {STABILITY_LINE_VERSION} line",
    )
    assert "unreadable-line" in result.line()


def test_a_line_missing_one_axis_is_refused_rather_than_compared_on_the_rest():
    """A hand-truncated paste is the realistic shape of this: someone quotes the
    line up to the field they cared about. Comparing what survived and calling
    it AGREE is the defect one paste-width away."""
    full = at_one_tree(session("steady_session_a")).line()
    truncated = full.replace(
        f" answers_id={at_one_tree(session('steady_session_a')).answers_identity()}", ""
    )
    result = compare_sessions([full, truncated])

    assert isinstance(result, CannotCompare)
    assert "answers_id" in result.render()


def test_one_line_is_not_a_comparison():
    result = compare_sessions([at_one_tree(session("steady_session_a")).line()])

    assert isinstance(result, CannotCompare)


def test_two_sessions_whose_pins_moved_are_refused_on_the_pin_that_moved():
    steady = at_one_tree(session("steady_session_a")).line()
    rerolled = steady.replace("prompt_hash=", "prompt_hash=sha256:deadbeef-")
    result = compare_sessions([steady, rerolled])

    assert isinstance(result, CannotCompare)
    assert "prompt_hash" in result.render()


def test_two_sessions_on_two_ollama_builds_are_not_comparable():
    """The host pin the digest does not cover, and the one place it matters
    (hy-a1i0).

    Measured before the fix: these two lines were BYTE-IDENTICAL and the pair
    was reported AGREE -- "these sessions reproduce" -- while the same drift
    between two repetitions of ONE session is refused by name as `PinsDrifted`.
    Cross-session is where host pins actually diverge; inside one process they
    cannot. So the comparator was strictly weaker than the check it calls its
    own cross-session form, on the pin it most needed.

    The first assertion is the one that was false: it holds that the drift
    reaches the LINE at all. Without it the refusal below could be satisfied by
    a key that names a field nothing prints.
    """
    steady = at_one_tree(session("steady_session_a")).line()
    other = at_one_tree(on_another_ollama("steady_session_a", "0.0.0-other-build")).line()

    assert steady != other
    result = compare_sessions([steady, other])

    assert isinstance(result, CannotCompare)
    assert "ollama_version" in result.render()


def test_a_refusal_never_renders_like_an_agreement():
    """hy-wwk3's first required addition, asserted rather than described. Three
    outcomes, three types, three renderings: a two-valued instrument answers
    with the safe word when it could not look, and here the safe word would be
    "stable"."""
    steady = at_one_tree(session("steady_session_a")).line()
    agreed = compare_sessions([steady, steady])
    refused = compare_sessions([steady])

    assert type(agreed) is not type(refused)
    assert AGREE not in refused.render()
    assert "not agreement" in refused.render()
    assert CANNOT_COMPARE not in agreed.render()


# The stability LINE's own non-pin `k=v` fields -- the counts, the four identity fields, and the
# result -- named here so the guard can subtract them and treat EVERY OTHER `k=v` key as a pin the
# comparator will read. These are the line's structure, NOT the pin names: a new PIN added to the
# renderer is not in this set, so it survives the subtraction and is checked against the key. A new
# NON-PIN count added to the line is not here either and reds LOUDLY until it is classified -- the
# safe direction (hy-nosl). Read against stability.StabilityReport.line().
_STABILITY_LINE_NON_PIN_FIELDS = frozenset(
    {
        "n",
        "predicates",
        "unanimous",
        "flapping",
        "answers_distinct",
        "source_refs_distinct",
        "source_versions_distinct",
        "unversioned",
        "trace_shapes_distinct",
        "verdicts",
        "traces",
        "answers_id",
        "evidence_id",
        "result",
    }
)


def _rendered_pin_names(line: str, non_pin_fields: frozenset[str]) -> set[str]:
    """The PIN names an ACTUAL rendered stability line carries: every `k=v` token whose key is not
    one of the line's known non-pin fields. Derived from the line TEXT, so a renderer that emits a
    new pin is SEEN here -- the direction the old guard dropped by filtering the line's tokens
    through a hardcoded set of the same pin names before the comparison (hy-nosl)."""
    keys = {token.split("=", 1)[0] for token in line.split() if "=" in token}
    return keys - non_pin_fields


def test_every_pin_the_rendered_line_carries_is_the_comparability_key():
    """Every pin the stability line RENDERS is the comparability key, and vice versa.

    DERIVED FROM THE ACTUAL LINE (hy-nosl), not from a static mirror of the pin names. The old
    guard filtered the line's tokens through a hardcoded set of the same ten names and asserted the
    result equalled the key, so a pin the renderer emitted but the key omitted was removed before
    the comparison -- invisible. This parses the real `line()`, subtracts only the line's own
    non-pin fields, and asserts EQUALITY with the key, which catches BOTH directions off the line
    itself: a renderer that emits an unkeyed pin (rendered superset) AND a key member the line
    still renders after it was dropped from the key (rendered != the smaller key)."""
    line = at_one_tree(session("steady_session_a")).line()
    rendered = _rendered_pin_names(line, _STABILITY_LINE_NON_PIN_FIELDS)
    assert rendered == set(COMPARABILITY_KEY), (
        "the pins the stability line renders and the comparability key have diverged: "
        f"rendered_only={rendered - set(COMPARABILITY_KEY)}, "
        f"key_only={set(COMPARABILITY_KEY) - rendered}"
    )


def test_a_pin_the_renderer_emits_but_the_key_omits_reds():
    """The direction the old guard could not see (hy-nosl): the RENDERER emits a pin absent from
    the key. Simulated by appending it to the real rendered line -- the extraction sees it and the
    equality above would fail, where the literal filter dropped it and reported nothing. Feeding
    the rendered LINE (not the pin tuples) is the point."""
    line = at_one_tree(session("steady_session_a")).line() + " gpu_arch=deadbeef"
    rendered = _rendered_pin_names(line, _STABILITY_LINE_NON_PIN_FIELDS)
    assert "gpu_arch" in rendered and "gpu_arch" not in set(COMPARABILITY_KEY)
    assert rendered != set(COMPARABILITY_KEY)  # the guard's own assertion would fail


def test_a_pin_the_renderer_stops_emitting_reds():
    """The key-only direction, retained and read off the line: a pin in the key the renderer no
    longer emits (equivalently, a key member kept while the line dropped it) breaks the equality."""
    line = at_one_tree(session("steady_session_a")).line()
    without_tools_hash = " ".join(t for t in line.split() if not t.startswith("tools_hash="))
    rendered = _rendered_pin_names(without_tools_hash, _STABILITY_LINE_NON_PIN_FIELDS)
    assert rendered != set(COMPARABILITY_KEY)
    assert "tools_hash" in set(COMPARABILITY_KEY) - rendered
