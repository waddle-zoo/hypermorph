"""A guard script in no completion checklist is the same green as no guard
(hy-4k9u). check_docs enforces that every enumerated guard is named in the
completion checklist, so removing one from CLAUDE.md reddens rather than silently
retiring the guard.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "check_docs", Path(__file__).resolve().parents[2] / "scripts" / "check_docs.py"
)
cd = importlib.util.module_from_spec(_SPEC)
sys.modules["check_docs"] = cd
_SPEC.loader.exec_module(cd)

ALL_EXIST = lambda script: True  # noqa: E731 - tiny stub for the exists() callback


def test_a_guard_named_in_the_checklist_passes():
    checklist = "\n".join(cd.GUARD_SCRIPTS)
    assert cd._completion_guard_violations(checklist, ALL_EXIST) == []


def test_a_guard_missing_from_the_checklist_reddens():
    # Drop check_expected_failure_owners.py from the list -- the exact hy-4k9u gap.
    missing = "scripts/check_expected_failure_owners.py"
    checklist = "\n".join(s for s in cd.GUARD_SCRIPTS if s != missing)
    violations = cd._completion_guard_violations(checklist, ALL_EXIST)
    assert any(missing in v and "no checklist is the same green" in v for v in violations)


def test_a_guard_enumerated_but_absent_from_disk_reddens():
    checklist = "\n".join(cd.GUARD_SCRIPTS)
    gone = "scripts/gate.py"
    violations = cd._completion_guard_violations(checklist, lambda s: s != gone)
    assert any(gone in v and "does not exist" in v for v in violations)


def test_a_guard_named_only_in_prose_does_not_count():
    # The false-green this check exists to stop: the script appears in a sentence
    # but not in the ```bash``` run block, so it is not actually run.
    guard = "scripts/check_expected_failure_owners.py"
    text = (
        f"We run {guard} at completion.\n\n"
        "```bash\nuv run python scripts/gate.py\npython3 scripts/check_docs.py\n```\n"
    )
    run = cd._run_block(text)
    assert guard not in run  # excluded: it was only in prose
    assert any(guard in v for v in cd._completion_guard_violations(run, ALL_EXIST))


def test_the_real_repo_passes_the_guard_check():
    # After adding check_expected_failure_owners.py to CLAUDE.md's run block.
    assert cd.check_completion_guards() == []
