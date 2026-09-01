"""An unreviewed adapter-derived field degrades governed->mixed (283-6, hy-v5iy).

Brandon's fork-3 ruling: REUSE the existing `mixed` status, no new value. The
degrade reads `resolution.projection.fields_derived` (283-5): a field the ADAPTER
authored with no human `reviewed_by` is not itself governed. A governed Git-owned
field is never in that list, so an adapter overlay never downgrades governed.
"""

from __future__ import annotations

from hyperset.bundle.resolver import _has_unreviewed_derived


def _projection(fields_derived, **extra):
    base = {
        "adapter": "acme",
        "adapter_version": 1,
        "fields_unmapped": [],
        "fields_lossy": [],
        "fields_derived": fields_derived,
    }
    base.update(extra)
    return base


def test_a_derived_field_with_no_reviewer_degrades():
    assert _has_unreviewed_derived(_projection([{"field": "region"}])) is True


def test_a_derived_field_with_a_blank_reviewer_degrades():
    assert _has_unreviewed_derived(_projection([{"field": "region", "reviewed_by": "  "}])) is True


def test_a_derived_field_with_a_reviewer_does_not_degrade():
    assert (
        _has_unreviewed_derived(_projection([{"field": "region", "reviewed_by": "alice@acme"}]))
        is False
    )


def test_one_unreviewed_among_reviewed_still_degrades():
    fields = [
        {"field": "a", "reviewed_by": "alice"},
        {"field": "b"},  # unreviewed
    ]
    assert _has_unreviewed_derived(_projection(fields)) is True


def test_an_empty_fields_derived_does_not_degrade():
    # The state EVERY adapter serves today (empty by construction): no degrade, so
    # every domain served now keeps the status it had.
    assert _has_unreviewed_derived(_projection([])) is False


def test_a_non_adapter_snapshot_does_not_degrade():
    assert _has_unreviewed_derived(None) is False


def test_a_lossy_or_unmapped_overlay_alone_does_not_degrade():
    # Only fields_derived-without-reviewer degrades. A governed field carried
    # through a lossy transform, or an unmapped disclosure, does NOT downgrade the
    # governed status -- governed stays authoritative.
    proj = _projection([], fields_lossy=[{"field": "title"}], fields_unmapped=[{"field": "x"}])
    assert _has_unreviewed_derived(proj) is False


def test_a_malformed_derived_entry_is_treated_as_unreviewed():
    # Fail toward degrading: an entry this cannot read as reviewed must not read as
    # governed.
    assert _has_unreviewed_derived(_projection(["region"])) is True
    assert _has_unreviewed_derived(_projection([{"field": "region", "reviewed_by": None}])) is True
