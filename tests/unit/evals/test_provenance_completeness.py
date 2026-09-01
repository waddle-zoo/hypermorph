"""The provenance-completeness grader and its release gate (hy-bwo, #25 scope 3).

The gate: every governed answer the committed benchmark serves carries a COMPLETE,
resolvable evidence chain -- 100% of #30's minimum reference contract -- so a
reviewer can audit what source was observed and what version became active without
a psql prompt. Completeness is not correctness (the scorers judge correctness);
this proves the chain is auditable at all.

The per-requirement tests each strip ONE reference from an otherwise-complete
bundle and assert the grader names exactly that gap -- so every requirement is
load-bearing, not decoration, and a future refactor that stops checking one reds.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hyperset.evals.provenance_completeness import (
    _CONTEXT_AUTHORITY_FIELDS,
    grade_bundle_completeness,
    grade_recording_completeness,
    resolution_status,
)

ROOT = Path(__file__).resolve().parents[3]
RECORDINGS = ROOT / "hyperset" / "evals" / "recordings"


def _governed_recordings() -> list[Path]:
    return sorted((RECORDINGS / "governed").rglob("*.json"))


def test_every_served_governed_answer_is_100_percent_complete():
    """THE GATE. Any committed recording whose served bundle is `governed` must
    carry a complete evidence chain. This is the release-evidence completeness gate
    (#25 scope 3), scored over committed recordings -- no model, no credential."""
    graded = []
    for path in _governed_recordings():
        payload = json.loads(path.read_text())
        if resolution_status(payload) != "governed":
            continue
        result = grade_recording_completeness(payload)
        graded.append((path.parent.name, result))
        assert result.complete, (
            f"{path.parent.name}: served governed answer has an INCOMPLETE evidence "
            f"chain, missing {list(result.missing)}"
        )
    # Non-vacuous: the canonical-metric governed_fetch case must be one of them.
    assert any(case == "revenue_by_region" for case, _ in graded), (
        "the canonical revenue_by_region governed answer was not graded; the gate is measuring "
        "nothing"
    )


def _complete_bundle() -> dict:
    authority = {field: "x" for field in _CONTEXT_AUTHORITY_FIELDS}
    authority["context_snapshot_id"] = "snap-1"
    authority["commit_sha"] = "c1"
    return {
        "bundle_id": "cb-1",
        "context_authority": authority,
        "provenance_refs": ["git_context:snap-1@c1", "observed_version:oav-1"],
        "linked_evidence": {
            "observed_assets": [
                {
                    "asset_id": "a1",
                    "asset_type": "dataset",
                    "observed_version_id": "oav-1",
                    "content_sha256": "h1",
                    "governance": "git_linked",
                    "ref": "superset:dataset:x",
                    "connector": "superset",
                }
            ]
        },
        "execution": {"performed_by_hyperset": False, "result_validated_by_hyperset": False},
        "resolution": {"status": "governed", "summary": "s", "warnings": []},
    }


def _complete_plan() -> dict:
    return {
        "status": "warnings",
        "checked_against": {"planned_bundle_id": "cb-1", "bundle_id": "cb-1"},
        "execution": {"performed_by_hyperset": False, "result_validated_by_hyperset": False},
    }


def test_a_fully_referenced_bundle_grades_complete():
    result = grade_bundle_completeness(_complete_bundle(), _complete_plan())
    assert result.complete, result.missing
    assert not result.missing


# Each mutation of the complete fixture trips EXACTLY ONE requirement, and the
# test asserts `missing == (that_one,)`. So deleting a single grader check reds
# ONLY that check's test (the fixture stops tripping it and the exact-equality
# assertion fails), and no other -- each requirement is individually load-bearing,
# not merely present-among-many (hy-bwo #405 anti-rot fix).
def _drop_a_context_field(bundle):
    # content_sha256 is NOT used by git_context_binds (which reads
    # context_snapshot_id + commit_sha), so dropping it fails only the authority
    # completeness check.
    bundle["context_authority"].pop("content_sha256")


def _one_malformed_ref(bundle):
    # A complete set of refs plus one that does not parse: the git and observed
    # refs still bind and resolve, so only well_formed fails.
    bundle["provenance_refs"] = ["git_context:snap-1@c1", "observed_version:oav-1", "not a ref"]


def _one_dangling_observed_ref(bundle):
    # git ref still binds; the observed ref names a version the evidence lacks.
    bundle["provenance_refs"] = ["git_context:snap-1@c1", "observed_version:oav-UNKNOWN"]


def _git_ref_names_the_wrong_commit(bundle):
    # observed ref still resolves; the git ref's commit does not match authority.
    bundle["provenance_refs"] = ["git_context:snap-1@WRONGCOMMIT", "observed_version:oav-1"]


def _incomplete_observed_asset(bundle):
    # observed_version_id stays (so the ref resolves); only completeness fails.
    bundle["linked_evidence"]["observed_assets"][0].pop("content_sha256")


def _missing_execution_flag(bundle):
    bundle["execution"].pop("result_validated_by_hyperset")


def _missing_resolution_warnings(bundle):
    bundle["resolution"].pop("warnings")


@pytest.mark.parametrize(
    "mutate, expected_missing",
    [
        (_drop_a_context_field, "context_authority.content_sha256"),
        (_one_malformed_ref, "provenance_refs.well_formed"),
        (_one_dangling_observed_ref, "provenance_refs.observed_resolve"),
        (_git_ref_names_the_wrong_commit, "provenance_refs.git_context_binds"),
        (_incomplete_observed_asset, "linked_evidence.observed_assets.complete"),
        (_missing_execution_flag, "execution.flags"),
        (_missing_resolution_warnings, "resolution.complete"),
    ],
)
def test_each_dropped_reference_is_the_sole_named_gap(mutate, expected_missing):
    bundle = _complete_bundle()
    mutate(bundle)
    result = grade_bundle_completeness(bundle, _complete_plan())
    # Exactly one requirement failed -- so deleting that grader check reds only
    # this test, and deleting any other leaves this test green.
    assert result.missing == (expected_missing,), result.missing
    assert not result.complete


def test_a_missing_bundle_id_is_the_sole_gap():
    # bundle_id feeds plan.binds_bundle too, so null the plan's binding target as
    # well: the only remaining gap is the bundle_id itself.
    bundle = _complete_bundle()
    bundle.pop("bundle_id")
    plan = _complete_plan()
    plan["checked_against"] = {"planned_bundle_id": None, "bundle_id": None}
    result = grade_bundle_completeness(bundle, plan)
    assert result.missing == ("bundle_id.present",), result.missing


def test_an_empty_plan_status_is_the_sole_gap():
    plan = _complete_plan()
    plan["status"] = ""
    result = grade_bundle_completeness(_complete_bundle(), plan)
    assert result.missing == ("plan.present",), result.missing


def test_a_plan_that_validated_a_different_bundle_is_the_sole_gap():
    plan = _complete_plan()
    plan["checked_against"] = {"planned_bundle_id": "cb-OTHER", "bundle_id": "cb-OTHER"}
    result = grade_bundle_completeness(_complete_bundle(), plan)
    assert result.missing == ("plan.binds_bundle",), result.missing


def test_a_plan_missing_an_execution_flag_is_the_sole_gap():
    plan = _complete_plan()
    plan["execution"].pop("result_validated_by_hyperset")
    result = grade_bundle_completeness(_complete_bundle(), plan)
    assert result.missing == ("plan.execution.flags",), result.missing


def test_empty_provenance_refs_is_caught_even_though_present_is_subsumed():
    # `provenance_refs.present` is the ONE check that cannot be isolated: an empty
    # ref list also fails observed_resolve (no observed ref to resolve) and
    # git_context_binds (no git ref), so no fixture trips present ALONE and
    # deleting present alone reds nothing. It is redundant with those two. Kept and
    # flagged to the mayor (a grader change would be needed to drop it); the empty
    # case is still caught, which this asserts exactly.
    bundle = _complete_bundle()
    bundle["provenance_refs"] = []
    result = grade_bundle_completeness(bundle, _complete_plan())
    assert set(result.missing) == {
        "provenance_refs.present",
        "provenance_refs.well_formed",
        "provenance_refs.observed_resolve",
        "provenance_refs.git_context_binds",
    }


def test_empty_observed_assets_is_caught_though_its_present_check_cannot_isolate():
    # `linked_evidence.observed_assets.present` is the SECOND check that cannot be
    # isolated to a single gap (hy-bwo #405 round 3): emptying the observed assets
    # also fails `.complete` (which requires a non-empty list) and
    # `provenance_refs.observed_resolve` (its observed ref no longer resolves). So
    # no fixture trips present ALONE; without this test, deleting the present check
    # stayed green. This asserts the exact set, so deleting present reds it.
    bundle = _complete_bundle()
    bundle["linked_evidence"]["observed_assets"] = []
    result = grade_bundle_completeness(bundle, _complete_plan())
    assert set(result.missing) == {
        "linked_evidence.observed_assets.present",
        "linked_evidence.observed_assets.complete",
        "provenance_refs.observed_resolve",
    }


def test_an_unverifiable_plan_still_binds_via_planned_bundle_id():
    # The real revenue recording: status `unverifiable` because the re-checked
    # bundle moved, but the arm planned over the served bundle. Still complete.
    plan = _complete_plan()
    plan["status"] = "unverifiable"
    plan["checked_against"] = {"planned_bundle_id": "cb-1", "bundle_id": "cb-MOVED"}
    result = grade_bundle_completeness(_complete_bundle(), plan)
    assert result.complete, result.missing


def test_a_missing_plan_fails_every_plan_requirement():
    # No plan at all: every plan requirement fails, and nothing else -- the bundle
    # half is complete. Exact, so this stays honest if a plan check is added.
    result = grade_bundle_completeness(_complete_bundle(), None)
    assert set(result.missing) == {
        "plan.present",
        "plan.binds_bundle",
        "plan.execution.flags",
    }


def test_a_recording_that_served_no_bundle_is_incomplete():
    result = grade_recording_completeness({"trace": {"steps": []}})
    assert not result.complete
    assert "resolve_analytics_context.served" in result.missing


def test_grading_does_not_mutate_the_recording():
    payload = json.loads((_governed_recordings()[0]).read_text())
    before = copy.deepcopy(payload)
    grade_recording_completeness(payload)
    assert payload == before
