#!/usr/bin/env python3
"""Gate a change on NEW mypy errors only (hy-djml step 2).

The type checker was adopted report-only with a large baseline (hy-djml step 1);
paying that baseline down before the check earns its keep would be a PR nobody has
time for. So this gates on the DELTA: run mypy at the merge base and at the head,
and fail only on an error the head introduced.

RECOMPUTED, never stored. A stored baseline is a file that goes stale silently and
whose failure mode is a false negative -- a new error waved through because the
baseline was not updated. This recomputes both error sets from the git graph on
every run; there is nothing to keep in sync (same argument as hy-kiwk).

TWO HONEST COSTS, documented here rather than discovered later:

1. Line numbers are STRIPPED from the key ((file, code, message)), so an error is
   matched to the base by its identity, not its position -- otherwise inserting a
   line above an existing error would renumber it and read as new. The cost: N
   identical errors in one file collapse by key, so the comparison is a MULTISET
   (Counter), not a set. Without that, adding a SECOND copy of an error the base
   already has would not be counted, and a regression would slip.

2. Moving a block of already-failing code to another file reads as N new errors
   (the file component of the key changed) and N disappeared. Arguably correct --
   the errors are at a new location -- but it is a real false positive for a pure
   move, and a reviewer seeing it should recognise the shape.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The gate runs mypy via `uvx` at a PINNED version with EXPLICIT flags, not via
# `uv run mypy`, so both the base tree and the head tree are checked identically
# regardless of what each tree's dependencies declare -- the base tree can predate
# the checker's adoption (hy-djml step 1) and have no mypy at all. The flags mirror
# the loosest `[tool.mypy]` config. The version is pinned because a different mypy
# reports a different set, which would read as spurious new/vanished errors.
#
# This ISOLATED run reports 140 errors at adoption, not the 131 the local
# `uv run mypy hyperset` reports: without the project's installed dependencies mypy
# resolves fewer imports and infers a few more errors. That is fine -- the gate
# only ever compares this same invocation at base against itself at head, so it is
# internally consistent; the absolute count is not what it gates on.
MYPY_VERSION = "2.3.1"
MYPY_FLAGS = ["--ignore-missing-imports", "--python-version", "3.11"]

# mypy's own exit codes: 0 = no type errors, 1 = type errors found. Both are a
# genuine run whose output is the finding set. ANYTHING ELSE -- 2 for a fatal or
# usage error, 127 for uvx failing to fetch mypy, a crash -- is an invocation that
# never measured, and its output is empty or partial. Reading that as "no errors"
# is how the gate silently disables itself, so only these two are trusted.
VALID_MYPY_RETURNCODES = frozenset({0, 1})


class MypyInvocationError(RuntimeError):
    """A mypy run that did not complete as a type-error check, so its output is not
    a trustworthy error set. Raised to FAIL CLOSED rather than let a failed
    invocation read as zero new errors."""


# `path:line: error: message  [code]` or `path:line:col: error: message [code]`.
# Only `error:` lines are gated; `note:` lines are context, not findings. The line
# (and optional column) is consumed but NOT captured -- that is the strip.
_ERROR = re.compile(
    r"^(?P<file>[^:]+):\d+(?::\d+)?: error: (?P<message>.*?)(?:  \[(?P<code>[^\]]+)\])?$"
)


def parse_errors(output: str) -> Counter[tuple[str, str, str]]:
    """A multiset of (file, code, message) over the `error:` lines, line numbers
    stripped so an error is identified by what it is, not where it sits."""
    errors: Counter[tuple[str, str, str]] = Counter()
    for line in output.splitlines():
        match = _ERROR.match(line.rstrip())
        if match is None:
            continue
        errors[(match["file"], match["code"] or "", match["message"])] += 1
    return errors


def new_errors(
    base: Counter[tuple[str, str, str]], head: Counter[tuple[str, str, str]]
) -> Counter[tuple[str, str, str]]:
    """The errors the head introduced: the MULTISET difference head - base. A key
    the head has more of than the base contributes its surplus count."""
    return head - base


def checked_errors(output: str, returncode: int) -> Counter[tuple[str, str, str]]:
    """Parse mypy output, but FAIL CLOSED on a return code that is not a genuine
    type-error run (0 clean / 1 errors found).

    A discarded nonzero -- uvx could not fetch mypy, a bad flag, an internal crash
    -- yields empty or partial output, which `parse_errors` reads as zero errors
    and the gate as 'nothing new', silently disabling the check on exactly the runs
    that most needed it. So the return code is validated BEFORE the output is
    trusted. A clean run with empty output (returncode 0, `no issues found`) is a
    real zero and is allowed through."""
    if returncode not in VALID_MYPY_RETURNCODES:
        raise MypyInvocationError(
            f"mypy exited {returncode}, which is not a type-error run (expected 0 or 1); "
            f"refusing to read a failed invocation as 'no errors'. Output:\n{output.strip()}"
        )
    return parse_errors(output)


def _run_mypy(paths: list[str], cwd: Path) -> tuple[str, int]:
    """Run the pinned mypy over `paths` in `cwd`, returning its combined output AND
    its return code. The code is PRESERVED, not discarded: `checked_errors` gates on
    it so a fatal invocation fails closed. Invoked via `uvx` at a pinned version so a
    tree without mypy in its own deps (a base predating adoption) is still checked
    identically to the head."""
    completed = subprocess.run(
        ["uvx", f"mypy@{MYPY_VERSION}", *MYPY_FLAGS, *paths],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return completed.stdout + completed.stderr, completed.returncode


def _errors_at(ref: str, paths: list[str]) -> Counter[tuple[str, str, str]]:
    """mypy errors at a git ref, checked out into a throwaway worktree so the
    working tree is never disturbed."""
    with tempfile.TemporaryDirectory(prefix="mypy-base-") as tmp:
        subprocess.run(
            ["git", "worktree", "add", "--detach", tmp, ref],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        try:
            return checked_errors(*_run_mypy(paths, Path(tmp)))
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", tmp],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base",
        default="origin/main",
        help="the ref to diff against; the merge base with HEAD is used (default origin/main)",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["hyperset"],
        help="paths to type-check (default: hyperset)",
    )
    args = parser.parse_args(argv)
    paths = args.paths or ["hyperset"]

    merge_base = subprocess.run(
        ["git", "merge-base", "HEAD", args.base],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    try:
        base = _errors_at(merge_base, paths)
        head = checked_errors(*_run_mypy(paths, REPO_ROOT))
    except MypyInvocationError as error:
        # Fail closed: a run that could not measure must not read as a pass.
        print(f"mypy new-error gate could not run:\n{error}", file=sys.stderr)
        return 2
    introduced = new_errors(base, head)

    if not introduced:
        total = sum(head.values())
        print(
            f"No NEW mypy errors vs {args.base} ({merge_base[:12]}). "
            f"Head has {total} error(s), all present at base."
        )
        return 0

    count = sum(introduced.values())
    print(
        f"{count} NEW mypy error(s) introduced vs {args.base} ({merge_base[:12]}):", file=sys.stderr
    )
    for (file, code, message), number in sorted(introduced.items()):
        tag = f" [{code}]" if code else ""
        suffix = f" (x{number})" if number > 1 else ""
        print(f"  {file}: {message}{tag}{suffix}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
