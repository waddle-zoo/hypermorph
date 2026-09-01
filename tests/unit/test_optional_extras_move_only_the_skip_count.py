"""An absent optional extra must move the skip count and not the denominator.

WHY THIS IS A TEST AND NOT A CONVENTION. A MODULE-level `pytest.importorskip`
does not skip its tests -- it removes the module from collection and leaves ONE
skip entry standing in for all of them. Three agents measured this suite at
f02cd60 and recorded 472, 463 and 439; nine of that spread was two such guards,
and every number was honest (hy-y91y, hy-ijkb, hy-a2ou). `scripts/gate.py` made
the movement visible and refuses to run without the extras. This removes the
movement, which is what makes an ad-hoc run comparable too -- and ad-hoc runs
are most of them.

THE INSTRUMENT IS A `sys.meta_path` FINDER RAISING `ModuleNotFoundError`, which
is the one thing that models an extra that was never installed. A stub raising
`ImportError` models a BROKEN install instead: `importorskip` re-raises that
deliberately rather than skipping, and the session dies with "Interrupted: N
errors during collection". Critic lost a durable record to exactly that
substitution, so the finder below is the subject of its own arm.

Subprocess rather than in-process, because the claim is about COLLECTION, and a
finder installed inside a running session arrives after these modules are
already imported.

EVERY ARM THAT BLOCKS A PACKAGE FIRST REQUIRES IT, via `_require` below. The
finder makes a package absent for the CHILD pytest; it cannot make one absent
that the PARENT does not have. In a seat where an extra is genuinely not
installed, blocking it changes nothing, and each arm then compares a reading to
itself: the absence arms failed there while naming the wrong cause, and -- worse
-- the denominator arms PASSED there against the very module-level guards this
file exists to forbid, measured both ways at `1ad0dd2` (hy-a2ou, slit's grade of
PR #224). A seat without the extra cannot answer the question, so it says so
with a skip rather than answering it wrongly in either direction. That is this
file's own thesis applied to itself, and it keeps the denominator at seven.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

GUARDED = {
    "inspect_ai": "tests/unit/evals/test_inspect_task.py",
    "claude_agent_sdk": "tests/unit/planner/test_claude_runtime.py",
    "presidio_analyzer": "tests/unit/security/test_pii_guard.py",
    # The `mcp` extra: test_mcp.py's every test drives a real MCP client through `_drive`, which
    # function-level `importorskip("mcp")`s -- so blocking mcp costs its skips WITHOUT uncollecting
    # the module (hy-fabp). The guard is a checklist, not auto-discovery, so a new extra is only
    # covered once ADDED here (mayor, hy-fabp).
    "mcp": "tests/unit/transport/test_mcp.py",
}
SUBJECTS = [*GUARDED.values(), "tests/unit/planner/test_runtime_conformance.py"]

BLOCKER = '''
"""Make named packages absent the way an uninstalled extra is absent."""
import os
import sys

BLOCKED = tuple(name for name in os.environ.get("BLOCK_PACKAGES", "").split(",") if name)


class Absent:
    def find_spec(self, name, path=None, target=None):
        if name in BLOCKED or any(name.startswith(b + ".") for b in BLOCKED):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None


sys.meta_path.insert(0, Absent())
'''


def _require(*packages: str) -> None:
    """Skip unless this seat can host the absence the caller is about to stage.

    `pytest.importorskip` is checked in THIS process, which is the one that
    decides what the child can be made to lack: the finder blocks a package the
    child would otherwise import, and there is no such package here if the
    parent's venv does not have it either.
    """
    for package in packages:
        pytest.importorskip(package)


def _plugin(tmp_path: Path) -> Path:
    plugin = tmp_path / "absent_extras.py"
    plugin.write_text(BLOCKER)
    return plugin


def _pytest(
    tmp_path: Path, *blocked: str, collect_only: bool = False
) -> subprocess.CompletedProcess:
    _plugin(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{tmp_path}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["BLOCK_PACKAGES"] = ",".join(blocked)
    argv = [sys.executable, "-m", "pytest", *SUBJECTS, "-q", "-p", "no:cacheprovider"]
    if collect_only:
        argv.append("--collect-only")
    return subprocess.run(
        [*argv, "-p", "absent_extras"], cwd=REPO_ROOT, env=env, capture_output=True, text=True
    )


def _collected(result: subprocess.CompletedProcess) -> int:
    assert result.returncode == 0, result.stdout + result.stderr
    return len([line for line in result.stdout.splitlines() if "::" in line])


def _counts(result: subprocess.CompletedProcess) -> dict[str, int]:
    """The summary line as numbers, so a row is comparable to another row."""
    assert result.returncode == 0, result.stdout + result.stderr
    summary = [line for line in result.stdout.splitlines() if " in " in line and "=" not in line][
        -1
    ]
    parts = summary.split(" in ")[0].replace(",", "").split()
    return {name: int(value) for value, name in zip(parts[::2], parts[1::2], strict=False)}


@pytest.mark.parametrize(
    "blocked",
    [(), ("inspect_ai",), ("claude_agent_sdk",), ("inspect_ai", "claude_agent_sdk")],
    ids=["nothing-absent", "inspect_ai-absent", "claude_agent_sdk-absent", "both-absent"],
)
def test_the_denominator_holds_still_whatever_is_installed(tmp_path, blocked):
    """Critic's four-row measurement, as an assertion instead of a record.

    The rows used to read 448 / 445 / 442 / 439 collected for these modules and
    their siblings; the whole point is that they now collapse onto one number,
    so the count is taken against the same command with nothing blocked rather
    than against a constant this file would have to keep up to date.

    The nothing-absent row blocks nothing, so it needs nothing and runs
    everywhere; it compares the command to itself and can only fail on
    nondeterminism.
    """
    _require(*blocked)
    present = _collected(_pytest(tmp_path, collect_only=True))
    absent = _collected(_pytest(tmp_path, *blocked, collect_only=True))

    assert absent == present, (
        f"blocking {blocked or '()'} moved collection from {present} to {absent}: a module-level "
        "guard has uncollected its tests, so two agents with different extras cannot compare "
        "suite sizes"
    )


@pytest.mark.parametrize(
    "package, module",
    sorted(GUARDED.items()),
    ids=sorted(GUARDED),
)
def test_an_absent_extra_still_costs_its_tests(tmp_path, package, module):
    """The other half, and the arm that stops this from passing vacuously.

    Constant collection is also what a guard that never fires looks like. So
    each package is required to actually cost skips when it is absent, and to
    cost them in its OWN module -- a count alone would be satisfied by any
    module skipping.
    """
    _require(package)
    present = _counts(_pytest(tmp_path))
    absent = _counts(_pytest(tmp_path, package))

    assert absent["skipped"] > present.get("skipped", 0), (
        f"{package} absent changed no skip count, so its guard did not fire: {absent}"
    )
    named = _pytest(tmp_path, package, collect_only=True)
    assert module in named.stdout, f"{module} left collection entirely with {package} absent"


def test_the_finder_makes_a_package_absent_rather_than_broken(tmp_path):
    """The instrument's own arm.

    `importorskip` skips a `ModuleNotFoundError` and RE-RAISES an
    `ImportError`, on purpose: a package that is installed and unimportable is
    a broken environment, not an absent extra. A stub that raised the wrong one
    would abort collection, and the four rows above would then agree at zero --
    the shape of a passing test that measured nothing.

    `inspect_ai` has to be here for the probe to mean anything: an uninstalled
    package raises `ModuleNotFoundError` on its own, so this arm would pass
    without the finder doing any of the work.
    """
    _require("inspect_ai")
    _plugin(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{tmp_path}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["BLOCK_PACKAGES"] = "inspect_ai"
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import absent_extras, inspect_ai",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert probe.returncode != 0
    assert "ModuleNotFoundError" in probe.stderr, probe.stderr
