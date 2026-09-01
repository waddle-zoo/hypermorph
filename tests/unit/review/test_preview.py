"""The ephemeral proposed-context preview render (hy-nauw, V1 gap Reviewer/4).

A reviewer runs a read-only preview of a task's UNAPPROVED draft before proposing: the
current-vs-proposed meaning, representative questions, and deterministic regression checks.
Pinned here as pure dict-in/dict-out -- NOT SERVING, no SQL, no governed write.
"""

from __future__ import annotations

from types import SimpleNamespace

from hyperset.review.preview import build_preview, regression_checks, representative_questions

DEFINITION = {
    "definitions": [{"term": "churn", "statement": "customers lost in a period"}],
    "approved_sources": [{"ref": "table:postgres:analytics.public.churn", "role": "primary"}],
    "fields": [
        {
            "name": "churn_rate",
            "source_ref": "table:postgres:analytics.public.churn",
            "expression": "lost / total",
        }
    ],
}
PAYLOAD = {
    "domain": "revenue",
    "miss": {"question": "What is churn?", "domain": "revenue"},
    "definition": DEFINITION,
}


def _task(payload=PAYLOAD, task_id="rt-abc"):
    return SimpleNamespace(id=task_id, proposal_payload=payload)


def test_representative_questions_are_the_miss_then_one_per_new_term_deduplicated():
    payload = {
        "miss": {"question": "What is churn?"},
        "definition": {
            "definitions": [{"term": "churn"}, {"term": "retention"}, {"term": "churn"}]
        },
    }
    assert representative_questions(payload) == [
        "What is churn?",
        "What does 'churn' mean?",
        "What does 'retention' mean?",
    ]


def test_representative_questions_are_empty_when_there_is_no_miss_or_term():
    assert representative_questions({"definition": {"definitions": []}}) == []


def test_regression_flags_a_field_that_reads_a_non_approved_source():
    """The proposed draft faces the SAME structural rule a Git commit faces: a field reading a
    source that is not approved fails the validate check, so the preview flags it before a PR."""
    draft = {
        "definitions": [{"term": "x", "statement": "y"}],
        "approved_sources": [{"ref": "table:postgres:analytics.public.a", "role": "primary"}],
        "fields": [
            {
                "name": "f",
                "source_ref": "table:postgres:analytics.public.UNAPPROVED",
                "expression": "e",
            }
        ],
    }
    (validates, preserves) = regression_checks(None, draft, domain="revenue")
    assert validates["check"] == "proposed_definition_validates"
    assert validates["status"] == "fail"
    assert any("not an approved source" in reason for reason in validates["detail"])
    # Nothing governed to preserve -> the second check still passes.
    assert preserves == {
        "check": "preserves_existing_governed_meaning",
        "status": "pass",
        "detail": [],
    }


def test_regression_warns_when_the_proposal_changes_an_existing_governed_entry():
    current = {"definitions": [{"term": "churn", "statement": "old governed meaning"}]}
    (validates, preserves) = regression_checks(current, DEFINITION, domain="revenue")
    assert validates["status"] == "pass"
    assert preserves["status"] == "warn"
    assert preserves["detail"] == ["definitions: changed churn"]


def test_regression_warns_when_the_proposal_removes_an_existing_governed_entry():
    current = {"definitions": [{"term": "legacy", "statement": "dropped"}]}
    proposed = {"definitions": []}
    (_validates, preserves) = regression_checks(current, proposed, domain="revenue")
    assert preserves["status"] == "warn"
    assert preserves["detail"] == ["definitions: removed legacy"]


def test_an_add_only_proposal_passes_both_regression_checks():
    (validates, preserves) = regression_checks(None, DEFINITION, domain="revenue")
    assert validates["status"] == "pass"
    assert preserves["status"] == "pass" and preserves["detail"] == []


def test_build_preview_is_not_serving_and_carries_current_proposed_diff_questions_and_checks():
    preview = build_preview(_task(), current_meaning=None)
    assert preview["not_serving"] is True  # a preview is never a served governed answer
    assert preview["task_id"] == "rt-abc"
    assert preview["domain"] == "revenue"
    assert preview["current_meaning"] is None
    assert preview["proposed_meaning"] == DEFINITION
    assert [e["term"] for e in preview["diff"]["sections"]["definitions"]["added"]] == ["churn"]
    assert preview["representative_questions"][0] == "What is churn?"
    assert [c["check"] for c in preview["regression_checks"]] == [
        "proposed_definition_validates",
        "preserves_existing_governed_meaning",
    ]
