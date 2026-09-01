"""Ephemeral proposed-context PREVIEW render (hy-nauw, V1 gap Reviewer/4).

Before the mutation handoff a reviewer runs a read-only preview of a task's UNAPPROVED
proposed context: the current-vs-proposed meaning, the representative questions the proposed
context is FOR, and deterministic regression checks. It is NOT SERVING -- it writes no
governed row, creates no approval, and runs no warehouse SQL (ADR 0012); it is computed
in-memory from the task's own draft plus the governed snapshot the reviewer already reads.
`not_serving: True` rides on the payload so nothing downstream can mistake a preview for a
served governed answer.

The current-vs-proposed diff is the SAME `diff_definition` the task detail shows; the
regression check reuses `validate_definition_draft`, the exact structural rule a human's Git
commit faces at sync (a field reading a non-approved source, a ref both approved and
prohibited), so the preview flags what the real proposal would.
"""

from __future__ import annotations

from hyperset.context.schema import ContextValidationError, validate_definition_draft
from hyperset.review.meaning_diff import MERGE_KEYS, diff_definition


def _entry_label(section: str, entry: object) -> str:
    """A short, stable identity for one definition entry, for a regression line."""
    if isinstance(entry, dict):
        for key in ("term", "name", "ref"):
            if entry.get(key):
                return str(entry[key])
        if entry.get("from") or entry.get("to"):
            return f"{entry.get('from')} → {entry.get('to')}"
    return str(entry)


def representative_questions(payload: dict) -> list[str]:
    """The questions the proposed context is representative FOR, deterministically from the
    task itself: the originating miss question, then one per newly-defined term. Deduplicated,
    order-stable -- no inference, no external corpus."""
    payload = payload or {}
    miss = payload.get("miss") or {}
    questions: list[str] = []
    question = (miss.get("question") or "").strip()
    if question:
        questions.append(question)
    for entry in (payload.get("definition") or {}).get("definitions") or []:
        term = (entry.get("term") or "").strip() if isinstance(entry, dict) else ""
        if term:
            questions.append(f"What does '{term}' mean?")
    seen: set[str] = set()
    ordered: list[str] = []
    for question in questions:
        if question not in seen:
            seen.add(question)
            ordered.append(question)
    return ordered


def regression_checks(
    current: dict | None, proposed: dict | None, *, domain: str | None
) -> list[dict]:
    """Deterministic checks a reviewer runs BEFORE proposing -- no SQL, no overlay serving.

    1. `proposed_definition_validates`: the proposed draft faces the SAME structural rules a
       Git commit faces (`validate_definition_draft`), so a field reading a non-approved
       source or a ref both approved and prohibited fails here, before any PR.
    2. `preserves_existing_governed_meaning`: a proposal that CHANGES or REMOVES an entry the
       governed context already declares is a regression risk to existing answers; those
       entries are listed. An add-only proposal passes.
    """
    checks: list[dict] = []
    try:
        validate_definition_draft(proposed or {}, domain=domain or "")
        checks.append({"check": "proposed_definition_validates", "status": "pass", "detail": []})
    except ContextValidationError as exc:
        checks.append(
            {
                "check": "proposed_definition_validates",
                "status": "fail",
                "detail": list(exc.reasons),
            }
        )

    diff = diff_definition(current, proposed)
    altered: list[str] = []
    for section in MERGE_KEYS:
        delta = diff.get("sections", {}).get(section)
        if not delta:
            continue
        for changed in delta.get("changed", []):
            altered.append(f"{section}: changed {changed['identity']}")
        for removed in delta.get("removed", []):
            altered.append(f"{section}: removed {_entry_label(section, removed)}")
    checks.append(
        {
            "check": "preserves_existing_governed_meaning",
            "status": "warn" if altered else "pass",
            "detail": altered,
        }
    )
    return checks


def build_preview(task, *, current_meaning: dict | None) -> dict:
    """The ephemeral preview for one review task. `current_meaning` is the governed current
    definition the caller looked up (or None); the proposed draft is the task's own
    UNAPPROVED `proposal_payload.definition`. Read-only and NOT SERVING."""
    payload = task.proposal_payload or {}
    proposed = payload.get("definition")
    proposed = proposed if isinstance(proposed, dict) else None
    domain = payload.get("domain") or (payload.get("miss") or {}).get("domain")
    return {
        "not_serving": True,
        "task_id": task.id,
        "domain": domain,
        "current_meaning": current_meaning,
        "proposed_meaning": proposed,
        "diff": diff_definition(current_meaning, proposed),
        "representative_questions": representative_questions(payload),
        "regression_checks": regression_checks(current_meaning, proposed, domain=domain),
    }
