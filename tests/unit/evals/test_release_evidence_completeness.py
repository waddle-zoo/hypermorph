"""Release-evidence completeness, audited per arm (hy-u772, hy-bwo SS4, #25 scope 4).

Every committed recording is release evidence, and a benchmark number means nothing if the
evidence behind it is not complete and resolvable. This audits, over the committed files
themselves (not a live run), that each recording of EVERY arm persists the full set --
task version, transcript, tool calls + results, git commit, source dependency refs, and the
run pins -- and that the GOVERNED arm additionally pins its exact Git-context snapshot and
observed-version deps per case, that stability is measurable across repeated runs, and that
`hyperset evals score` now SURFACES governed completeness beside the scores.
"""

from __future__ import annotations

import json

import pytest

from hyperset.evals.provenance_completeness import _served, grade_recording_completeness
from hyperset.evals.recording import (
    FRONTIER_ARM,
    GOVERNED_ARM,
    RECOGNIZED_ARMS,
    Recording,
)
from hyperset.evals.report import render, score_recordings
from hyperset.evals.run import RECORDINGS_DIR, recordings_of
from hyperset.evals.scorers import calls, results
from hyperset.evals.source_identity import source_pairs
from hyperset.evals.stability import parse_stability_line, stability_report

# The pins every arm's run must carry to be reproducible: model IDENTITY (name + digest),
# the DETERMINISM inputs (seed, temperature), and the two hashes that pin the served surface.
REQUIRED_PIN_KEYS = ("model", "digest", "seed", "temperature", "tools_hash", "prompt_hash")


def _committed(directory=None):
    """Every committed recording of EVERY RECOGNIZED arm -- including the optional frontier
    arm when one is present. `recordings_of` returns nothing for an absent arm, so iterating
    RECOGNIZED_ARMS keeps the frontier arm optional (no row when uncommitted) while ensuring a
    committed frontier recording IS audited rather than silently skipped. `directory` lets a
    test point the discovery at a synthetic recordings root."""
    from hyperset.evals.cases import load_cases

    out = []
    for arm in RECOGNIZED_ARMS:
        for case in load_cases():
            for path in recordings_of(arm, case.id, directory=directory):
                out.append((arm, case, path))
    return out


def _governed():
    return [(case, path) for arm, case, path in _committed() if arm == GOVERNED_ARM]


def _assert_full_evidence(path):
    """Every field a release quote rests on is present in the recording at `path`."""
    recording = Recording.read(path)
    payload = json.loads(path.read_text())
    trace = payload["trace"]

    assert recording.task_version, "no task version"  # WHICH exam it sat
    assert recording.git_commit, "no git commit"  # the source state it ran against
    assert recording.recorded_at, "no recorded_at"
    assert payload["source_refs"], "no source dependency refs"  # observed deps
    assert trace.get("steps"), "no transcript (trace.steps)"
    assert calls(recording), "no tool calls persisted"
    assert results(recording), "no tool results persisted"

    pins = recording.pins.to_dict()
    # Present (a determinism value like temperature=0 or seed=0 is a real pin, not a gap)...
    missing_pins = [key for key in REQUIRED_PIN_KEYS if pins.get(key) is None]
    assert not missing_pins, f"pins missing {missing_pins}"
    # ...and the string identity + surface-hash pins are non-empty (a blank hash is no pin).
    for key in ("model", "digest", "tools_hash", "prompt_hash"):
        assert pins.get(key), f"empty pin {key!r}"


@pytest.mark.parametrize("arm,case,path", _committed(), ids=lambda v: getattr(v, "id", str(v)))
def test_every_committed_recording_persists_the_full_evidence_set(arm, case, path):
    """Per RECOGNIZED arm (incl a committed frontier), per case: the evidence chain is all
    present."""
    _assert_full_evidence(path)


def test_committed_discovery_covers_a_committed_frontier_recording(tmp_path):
    """Guards the DISCOVERY iteration itself, not just `_assert_full_evidence`: a committed
    frontier recording in the recordings root must be DISCOVERED by `_committed()` and then
    audited. Reverting `_committed()` from RECOGNIZED_ARMS back to ARMS (the actual bug) reds
    the discovery assertion below, because the frontier tuple stops being returned.

    Build a synthetic recordings root (the committed governed + raw arms, plus a frontier
    recording relabelled from a governed one), drive `_committed()` at it, and assert the
    frontier arm+path is discovered; then drop a discovered field and assert the audit reds
    on the DISCOVERED path."""
    import shutil

    root = tmp_path / "recordings"
    shutil.copytree(RECORDINGS_DIR, root)  # the required governed + raw arms
    governed = root / GOVERNED_ARM / "revenue_by_region" / "unidentified.json"
    payload = json.loads(governed.read_text())
    payload["arm"] = FRONTIER_ARM
    fdir = root / FRONTIER_ARM / "revenue_by_region"
    fdir.mkdir(parents=True)
    fpath = fdir / "unidentified.json"
    fpath.write_text(json.dumps(payload))

    discovered = _committed(directory=root)
    frontier = [(arm, case, path) for arm, case, path in discovered if arm == FRONTIER_ARM]
    assert frontier, "the committed frontier recording was NOT discovered by _committed()"
    ((_, _, discovered_path),) = frontier  # exactly one frontier recording, and it is ours
    assert discovered_path == fpath

    _assert_full_evidence(discovered_path)  # the DISCOVERED frontier recording passes

    payload["source_refs"] = []  # drop a required field on the discovered recording
    fpath.write_text(json.dumps(payload))
    with pytest.raises(AssertionError):
        _assert_full_evidence(discovered_path)


def test_every_scored_run_persists_non_empty_score_records():
    """Scores are release evidence too: every scored run (each arm, each case) carries the
    predicate results, each a real record, not an empty list. Emptying a run's scores reds
    this even though provenance-completeness and the rendered text stay green."""
    report = score_recordings()
    assert report["runs"], "no runs were scored"
    for run in report["runs"]:
        scores = run["scores"]
        assert scores, f"run {run['case_id']}/{run['arm']} persists no score records"
        for entry in scores:
            assert entry.get("predicate"), f"a score record on {run['case_id']} names no predicate"
            assert entry.get("code"), f"a score record on {run['case_id']} carries no code"


@pytest.mark.parametrize("case,path", _governed(), ids=lambda v: getattr(v, "id", str(v)))
def test_the_governed_arm_pins_its_git_context_snapshot_and_observed_deps_per_case(case, path):
    """The governed arm's exact Git-context snapshot commit and its observed-version deps are
    pinned in the served bundle, and the whole chain grades COMPLETE."""
    payload = json.loads(path.read_text())
    trace = payload["trace"]

    grade = grade_recording_completeness({"trace": trace})
    assert grade.complete, f"governed recording is provenance-incomplete: missing {grade.missing}"

    bundle = _served(trace, "resolve_analytics_context")
    refs = (bundle or {}).get("provenance_refs") or []
    git_context = [r for r in refs if r.startswith("git_context:") and "@" in r]
    observed = [r for r in refs if r.startswith("observed_version:")]
    assert len(git_context) == 1, f"expected one pinned Git-context snapshot, got {git_context}"
    assert git_context[0].split("@", 1)[1], "the Git-context snapshot names no commit"
    assert observed, "no observed-version deps pinned"
    # The recorded observed source deps are carried alongside for the same case.
    assert source_pairs(trace), "no observed source pairs recorded"


def test_stability_is_measurable_and_the_governed_recording_carries_versions():
    """Stability across repeated runs is measurable, and the committed governed recording
    carries the source versions a stability read needs (0 unversioned, 1 distinct version set
    when the same run is read twice)."""
    from hyperset.evals.cases import load_cases

    case, path = _governed()[0]
    recording = Recording.read(path)
    case = next(c for c in load_cases() if c.id == recording.case_id)

    subject = stability_report([recording, recording], case)
    fields = parse_stability_line(subject.line())

    assert fields["case"] == recording.case_id
    assert fields["unversioned"] == "0", "the committed governed recording must carry versions"
    assert int(fields["source_versions_distinct"]) == 1, "one run read twice is one version set"


def test_the_score_report_and_render_surface_governed_provenance_completeness():
    """SS4 wiring: `hyperset evals score` now surfaces per-case governed completeness beside
    the scores, so the benchmark job shows the evidence is complete, not only the numbers."""
    report = score_recordings()

    entries = report["provenance_completeness"]
    governed_cases = {case.id for case, _ in _governed()}
    assert {e["case_id"] for e in entries} == governed_cases
    assert all(e["arm"] == GOVERNED_ARM for e in entries)
    assert all(e["complete"] is True and e["missing"] == [] for e in entries)

    text = render(report)
    assert "Provenance completeness" in text
    for case_id in governed_cases:
        assert f"{case_id}: complete" in text
