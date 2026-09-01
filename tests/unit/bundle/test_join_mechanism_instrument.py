"""Independent-instrument gate for the one join mechanism (hy-gl39, design
Section 8, Gate B).

VERIFIABLE PROVENANCE, not an author label. This file is committed RED, BEFORE
the mechanism refactor, and the refactor commit does NOT touch it (git-checkable:
`git log --follow` shows it predates the refactor, and the refactor commit's diff
over this path is empty under `--no-renames`). So the mechanism cannot have been
co-designed against it: the field pair below was held out and the dispatch was
made to pass it UNCHANGED.

What it proves (ADR-0021 dec 7's admitted gap): "a contradiction is a join, not a
rule" holds for a governed expression field the current rule never reads. The
processor's `approved_expression_drift` compares `fields[].expression` vs
`metrics[].expression`; a `filters[].expression` (or `validations[].expression`)
pair is one it does not read. Fed through the SAME dispatch as an `expression`
value kind, it must be judged by the comparator with NO per-field branch anywhere
in the mechanism -- if it needed a new `if` or a new list entry, it is a rule
table and this fails.
"""

from __future__ import annotations


def _entry(field, declared, observed):
    return dict(
        kind="expression_drift",
        produced_by="processor_finding",
        severity="error",
        finding_id="held-out",
        ref="superset:dataset:orders",
        field=field,
        context_says=declared,
        source_says=observed,
        unresolved_since_commit="abc123",
    )


def test_the_mechanism_catches_a_governed_expression_field_the_old_rule_never_read():
    # Imported inside the test so this file is a clean single-test RED against
    # today's per-dimension code (the API does not exist yet), not a collection
    # crash that would mask the rest of the suite.
    from hyperset.bundle.reconcile import EXPRESSION, JoinPair, reconcile

    # `filters[].expression` -- NOT read by the processor's drift rule. The
    # observed projection differs on a string literal, which `compare_fragments`
    # calls DIFFERENT (literals are never folded).
    drift = JoinPair(
        value_kind=EXPRESSION,
        declared="status = 'completed'",
        observed="status = 'complete'",
        entry=_entry("status_filter", "status = 'completed'", "status = 'complete'"),
    )
    (emitted,) = reconcile([drift])
    assert emitted["field"] == "status_filter"
    assert emitted["kind"] == "expression_drift"

    # The SAME field, reformatted (keyword case + whitespace) is EQUIVALENT and is
    # NOT a conflict -- the comparator decides, not a per-field rule. If the
    # mechanism keyed on the field name it could not tell these two apart.
    reformatted = JoinPair(
        value_kind=EXPRESSION,
        declared="status = 'completed'",
        observed="STATUS   =   'completed'",
        entry=_entry("status_filter", "status = 'completed'", "STATUS   =   'completed'"),
    )
    assert reconcile([reformatted]) == []
