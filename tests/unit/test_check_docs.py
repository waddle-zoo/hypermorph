"""Every section of scripts/check_docs.py is exercised against a TEMPORARY ROOT (hy-p6jp).

The script had no test file, so each section was validated only by the run that would report it
green -- a section that can never report a finding passed exactly like one that looked and found
nothing (the bead's `check_compatibility_links` risk: it would report zero violations if its link
regex stopped matching). Each section here gets a CLEAN control (0 violations, non-vacuous) AND at
least one synthetic POSITIVE, so a section that stopped firing goes red. The merge-check probe is
PORTED from the six arms hy-ytxq ran against a temporary ROOT before shipping, not reconstructed.

The pattern: load a fresh module, set its `ROOT` to a tmp_path holding copies of ONLY the files
that section reads, call the section, assert. Trees are built from the module's OWN constants
where possible, so a constant change moves the fixture with it rather than silently un-covering.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

_CHECK_DOCS = Path(__file__).resolve().parents[2] / "scripts" / "check_docs.py"


def _load(root: Path):
    """A fresh check_docs module whose ROOT is `root`."""
    spec = importlib.util.spec_from_file_location("check_docs_under_test", _CHECK_DOCS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    return module


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- positioning ----------------------------------------------------------------------------


def test_positioning_control_is_clean(tmp_path):
    module = _load(tmp_path)
    _write(tmp_path, "README.md", "# Hyperset\n\nA governed analytics context service.\n")
    assert module.check_positioning() == []


def test_positioning_reports_an_unqualified_obsolete_phrase(tmp_path):
    module = _load(tmp_path)
    phrase = module.OBSOLETE_PHRASES[0]
    _write(tmp_path, "README.md", f"# Hyperset\n\nHyperset is a {phrase} today.\n")
    violations = module.check_positioning()
    assert any(phrase in v for v in violations), violations


def test_positioning_exempts_a_phrase_in_a_historical_window(tmp_path):
    module = _load(tmp_path)
    phrase = module.OBSOLETE_PHRASES[0]
    # A HISTORICAL_MARKER within the window qualifies it, so it must NOT be a violation -- the
    # arm that keeps this from being a blunt substring grep.
    _write(
        tmp_path, "README.md", f"# Hyperset\n\nThis is no longer true: it was once a {phrase}.\n"
    )
    assert module.check_positioning() == []


# --- research-status ------------------------------------------------------------------------


def test_research_status_control_is_clean(tmp_path):
    module = _load(tmp_path)
    for rel in module.REQUIRED_STATUS_FILES:
        _write(tmp_path, rel, "[!NOTE] status: current\n\nresearch body\n")
    assert module.check_research_status() == []


def test_research_status_reports_a_missing_status_block(tmp_path):
    module = _load(tmp_path)
    for rel in module.REQUIRED_STATUS_FILES:
        _write(tmp_path, rel, "[!NOTE] status: current\n\nbody\n")
    broken = module.REQUIRED_STATUS_FILES[0]
    _write(tmp_path, broken, "# Title\n\nno status block here\n")
    violations = module.check_research_status()
    assert any(broken in v and "missing" in v for v in violations), violations


def test_research_status_reports_historical_without_a_warning_marker(tmp_path):
    module = _load(tmp_path)
    for rel in module.REQUIRED_STATUS_FILES:
        _write(tmp_path, rel, "[!NOTE] status: current\n\nbody\n")
    rel = module.REQUIRED_STATUS_FILES[0]
    # Historical status under [!NOTE] instead of [!WARNING] must fire.
    _write(tmp_path, rel, "[!NOTE] status: historical\n\nbody\n")
    violations = module.check_research_status()
    assert any(rel in v and "[!WARNING]" in v for v in violations), violations


# --- foundation-contract --------------------------------------------------------------------


def _clean_foundation(module, root: Path) -> None:
    for rel, requirements in module.FOUNDATION_REQUIREMENTS.items():
        _write(root, rel, "\n".join(requirements) + "\n")


def test_foundation_contract_control_is_clean(tmp_path):
    module = _load(tmp_path)
    _clean_foundation(module, tmp_path)
    assert module.check_foundation_contract() == []


def test_foundation_contract_reports_a_missing_commitment(tmp_path):
    module = _load(tmp_path)
    _clean_foundation(module, tmp_path)
    rel, requirements = next(iter(module.FOUNDATION_REQUIREMENTS.items()))
    dropped = requirements[0]
    _write(tmp_path, rel, "\n".join(requirements[1:]) + "\n")  # one phrase removed
    violations = module.check_foundation_contract()
    assert any(rel in v and dropped in v for v in violations), violations


def test_foundation_contract_reports_a_missing_document(tmp_path):
    module = _load(tmp_path)
    _clean_foundation(module, tmp_path)
    rel = next(iter(module.FOUNDATION_REQUIREMENTS))
    (tmp_path / rel).unlink()
    violations = module.check_foundation_contract()
    assert any(rel in v and "missing" in v for v in violations), violations


# --- compatibility-links --------------------------------------------------------------------


def test_compatibility_links_control_is_clean(tmp_path):
    module = _load(tmp_path)
    _write(tmp_path, "docs/target.md", "# Target\n")
    _write(tmp_path, "README.md", "# Hyperset\n\nSee [the target](docs/target.md).\n")
    assert module.check_compatibility_links() == []


def test_compatibility_links_reports_a_broken_markdown_link(tmp_path):
    module = _load(tmp_path)
    # THE bead's named risk: a link the section must resolve to a real file.
    _write(tmp_path, "README.md", "# Hyperset\n\nSee [gone](docs/does-not-exist.md).\n")
    violations = module.check_compatibility_links()
    assert any("does-not-exist.md" in v for v in violations), violations


def test_compatibility_links_reports_an_unqualified_broad_compat_claim(tmp_path):
    module = _load(tmp_path)
    _write(
        tmp_path, "README.md", "# Hyperset\n\nSupports Superset 4.x through 6.1 out of the box.\n"
    )
    violations = module.check_compatibility_links()
    assert any("4.x" in v for v in violations), violations


# --- repo-shape -----------------------------------------------------------------------------


def _git_init(root: Path) -> None:
    for args in (["init"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _clean_repo_shape(module, root: Path) -> None:
    _git_init(root)
    for rel in (
        ".agents/skills/caveman/SKILL.md",
        ".agents/skills/ponytail/SKILL.md",
        "tests/fixtures/superset/6.1.0/revenue/manifest.json",
        "tests/fixtures/superset/6.1.0/usage/manifest.json",
    ):
        _write(root, rel, "{}\n")
    _write(root, ".gitignore", ".beads/\n.claude/\n")


def test_repo_shape_control_is_clean(tmp_path):
    module = _load(tmp_path)
    _clean_repo_shape(module, tmp_path)
    assert module.check_repo_shape() == []


def test_repo_shape_reports_a_removed_package_that_exists(tmp_path):
    module = _load(tmp_path)
    _clean_repo_shape(module, tmp_path)
    _write(tmp_path, "hyperset/agent/__init__.py", "")  # a package this project deleted by name
    violations = module.check_repo_shape()
    assert any("hyperset/agent" in v and "must not" in v for v in violations), violations


def test_repo_shape_reports_a_missing_required_asset(tmp_path):
    module = _load(tmp_path)
    _clean_repo_shape(module, tmp_path)
    (tmp_path / ".agents/skills/caveman/SKILL.md").unlink()
    violations = module.check_repo_shape()
    assert any("caveman/SKILL.md" in v and "missing" in v for v in violations), violations


def test_repo_shape_reports_agent_local_state_not_ignored(tmp_path):
    module = _load(tmp_path)
    _clean_repo_shape(module, tmp_path)
    _write(tmp_path, ".gitignore", ".claude/\n")  # .beads/ line removed
    violations = module.check_repo_shape()
    assert any(".beads/" in v and "not ignored" in v for v in violations), violations


# --- merge-check-exit-codes (ported from hy-ytxq's six-arm probe) ---------------------------

_REFINERY = "docs/directives/refinery.md"
_CLAUSE3 = "scripts/clause3-intersection.sh"
_REFCHECK = "scripts/refcheck.sh"


def _clean_merge_check(root: Path) -> None:
    """A refinery.md whose exit-code table agrees exactly with two synthetic scripts."""
    _write(root, _CLAUSE3, "#!/bin/bash\nexit 0\nexit 4\n")
    _write(root, _REFCHECK, "#!/bin/bash\nrc=0\nexit $rc\n")  # rc assigned 0, exits it
    _write(
        root,
        _REFINERY,
        "# Refinery\n\n"
        "```bash\n"
        f"bash {_CLAUSE3}\n"
        "# 0 clean, proceed\n"
        "# 4 REFUSE\n"
        f"bash {_REFCHECK}\n"
        "# 0 clean, proceed\n"
        "```\n",
    )


def test_merge_check_control_is_clean(tmp_path):
    module = _load(tmp_path)
    _clean_merge_check(tmp_path)
    assert module.check_merge_check_exit_codes() == []


def test_merge_check_reports_a_documented_code_the_script_no_longer_produces(tmp_path):
    module = _load(tmp_path)
    _clean_merge_check(tmp_path)
    # Document a code 9 nothing produces.
    text = (
        (tmp_path / _REFINERY).read_text().replace("# 4 REFUSE\n", "# 4 REFUSE\n# 9 impossible\n")
    )
    _write(tmp_path, _REFINERY, text)
    violations = module.check_merge_check_exit_codes()
    assert any("9" in v and "no longer produces it" in v for v in violations), violations


def test_merge_check_reports_a_code_the_script_produces_but_the_directive_omits(tmp_path):
    module = _load(tmp_path)
    _clean_merge_check(tmp_path)
    # Append exit 7 to the script; the directive does not mention it.
    _write(tmp_path, _CLAUSE3, "#!/bin/bash\nexit 0\nexit 4\nexit 7\n")
    violations = module.check_merge_check_exit_codes()
    assert any("can exit 7" in v for v in violations), violations


def test_merge_check_reports_a_dropped_refuse_annotation(tmp_path):
    module = _load(tmp_path)
    _clean_merge_check(tmp_path)
    # Drop the `4 REFUSE` row while the script still exits 4.
    text = (tmp_path / _REFINERY).read_text().replace("# 4 REFUSE\n", "")
    _write(tmp_path, _REFINERY, text)
    violations = module.check_merge_check_exit_codes()
    assert any("can exit 4" in v for v in violations), violations


def test_merge_check_reports_a_renamed_invocation_line(tmp_path):
    module = _load(tmp_path)
    _clean_merge_check(tmp_path)
    # Rename the `bash <script>` line so no annotation attaches to it.
    text = (
        (tmp_path / _REFINERY).read_text().replace(f"bash {_CLAUSE3}\n", "run the intersection\n")
    )
    _write(tmp_path, _REFINERY, text)
    violations = module.check_merge_check_exit_codes()
    assert any("no exit-code annotation" in v and _CLAUSE3 in v for v in violations), violations


def test_merge_check_reports_a_documented_script_that_does_not_exist(tmp_path):
    module = _load(tmp_path)
    _clean_merge_check(tmp_path)
    (tmp_path / _CLAUSE3).unlink()
    violations = module.check_merge_check_exit_codes()
    assert any("which does not exist" in v and _CLAUSE3 in v for v in violations), violations


# --- completion-guards ----------------------------------------------------------------------


def _clean_completion(module, root: Path) -> None:
    for script in module.GUARD_SCRIPTS:
        _write(root, script, "# a guard\n")
    named = "\n".join(f"python3 {script}" for script in module.GUARD_SCRIPTS)
    _write(root, module.COMPLETION_CHECKLIST, f"# Completion\n\n```bash\n{named}\n```\n")


def test_completion_guards_control_is_clean(tmp_path):
    module = _load(tmp_path)
    _clean_completion(module, tmp_path)
    assert module.check_completion_guards() == []


def test_completion_guards_reports_a_guard_run_by_no_command(tmp_path):
    module = _load(tmp_path)
    _clean_completion(module, tmp_path)
    dropped = module.GUARD_SCRIPTS[0]
    named = "\n".join(f"python3 {s}" for s in module.GUARD_SCRIPTS if s != dropped)
    _write(tmp_path, module.COMPLETION_CHECKLIST, f"# Completion\n\n```bash\n{named}\n```\n")
    violations = module.check_completion_guards()
    assert any(dropped in v and "no completion command" in v for v in violations), violations


def test_completion_guards_reports_an_enumerated_guard_that_does_not_exist(tmp_path):
    module = _load(tmp_path)
    _clean_completion(module, tmp_path)
    missing = module.GUARD_SCRIPTS[0]
    (tmp_path / missing).unlink()
    violations = module.check_completion_guards()
    assert any(missing in v and "does not exist" in v for v in violations), violations
