"""The pure current-vs-proposed definition diff (hy-z6zv).

A reviewer judges a proposal by the EXACT change it makes to the governed current meaning.
This pins that the diff reports added/changed/removed by the same entry identity the proposal
PR merge deduplicates on, is deterministic, and stays in sync with git_pr's merge keys.
"""

from __future__ import annotations

from hyperset.flywheel.git_pr import _MERGE_KEYS as GIT_PR_MERGE_KEYS
from hyperset.review.meaning_diff import MERGE_KEYS, diff_definition, merge_definitions

PROPOSED = {
    "definitions": [{"term": "churn", "statement": "customers lost in a period"}],
    "approved_sources": [{"ref": "table:analytics.churn", "role": "primary"}],
    "fields": [{"name": "churn_rate", "expression": "lost / total"}],
    "grain": "monthly",
}


def test_against_no_governed_meaning_every_section_is_added_and_grain_is_a_before_after():
    diff = diff_definition(None, PROPOSED)
    sections = diff["sections"]
    assert [e["term"] for e in sections["definitions"]["added"]] == ["churn"]
    assert sections["definitions"]["changed"] == [] and sections["definitions"]["removed"] == []
    assert [e["ref"] for e in sections["approved_sources"]["added"]] == ["table:analytics.churn"]
    assert [e["name"] for e in sections["fields"]["added"]] == ["churn_rate"]
    # A scalar key that was unset and is now set is a before/after, not an added entry.
    assert diff["grain"] == {"before": None, "after": "monthly"}


def test_a_same_identity_entry_with_a_new_body_is_changed_not_added_or_removed():
    current = {
        "definitions": [{"term": "churn", "statement": "OLD meaning"}],
        "fields": [{"name": "churn_rate", "expression": "lost / total"}],  # identical -> no change
    }
    diff = diff_definition(current, PROPOSED)
    definitions = diff["sections"]["definitions"]
    assert definitions["added"] == [] and definitions["removed"] == []
    (changed,) = definitions["changed"]
    assert changed["identity"] == "churn"
    assert changed["before"]["statement"] == "OLD meaning"
    assert changed["after"]["statement"] == "customers lost in a period"
    # An entry present and byte-identical on both sides is NOT reported: `fields` did not move.
    assert "fields" not in diff["sections"]
    # approved_sources exists only on the proposal -> added.
    assert [e["ref"] for e in diff["sections"]["approved_sources"]["added"]] == [
        "table:analytics.churn"
    ]


def test_an_entry_only_in_the_current_meaning_is_removed():
    current = {"definitions": [{"term": "legacy", "statement": "dropped"}]}
    diff = diff_definition(current, {"definitions": []})
    definitions = diff["sections"]["definitions"]
    assert [e["term"] for e in definitions["removed"]] == ["legacy"]
    assert definitions["added"] == [] and definitions["changed"] == []


def test_an_unchanged_proposal_reports_no_sections_and_no_scalar_move():
    assert diff_definition(PROPOSED, dict(PROPOSED)) == {"sections": {}}


def test_the_diff_is_deterministic_and_orders_entries_by_identity():
    proposed = {
        "definitions": [
            {"term": "beta", "statement": "b"},
            {"term": "alpha", "statement": "a"},
        ]
    }
    first = diff_definition(None, proposed)
    assert first == diff_definition(None, proposed)  # repeatable
    assert [e["term"] for e in first["sections"]["definitions"]["added"]] == ["alpha", "beta"]


def test_merge_definitions_unions_several_governed_rows_by_identity():
    merged = merge_definitions(
        [
            {"definitions": [{"term": "churn", "statement": "a"}], "grain": "monthly"},
            {"definitions": [{"term": "retention", "statement": "r"}]},
            {"definitions": [{"term": "churn", "statement": "LATER wins"}]},  # same id, later wins
        ]
    )
    by_term = {e["term"]: e["statement"] for e in merged["definitions"]}
    assert by_term == {"churn": "LATER wins", "retention": "r"}
    assert merged["grain"] == "monthly"  # first non-null scalar


def test_the_merge_key_identities_stay_in_sync_with_the_pr_merge():
    """The detail diff claims to mirror what the proposal PR will carry, so the sections and
    the primary-key identity of each entry must match git_pr's add-only merge. Sample entry
    carries every primary-key field; the two identities must agree per section -- otherwise an
    entry the PR treats as already-present could show as added at detail, or vice versa."""
    assert set(MERGE_KEYS) == set(GIT_PR_MERGE_KEYS)
    sample = {"term": "t", "ref": "r", "name": "n", "from": "a", "to": "b"}
    for section in ("definitions", "approved_sources", "prohibited_sources", "fields", "joins"):
        assert MERGE_KEYS[section](sample) == GIT_PR_MERGE_KEYS[section](sample), section
