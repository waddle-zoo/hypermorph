#!/usr/bin/env python3
"""Documentation authority and v0-foundation checks.

WHAT THIS CANNOT DO, stated here because a green run from it reads as "the
docs are fine" and that reading is what let `docs/v0-foundation.md` section 7
contradict the shipped wire contract through four merged PRs. Everything below
compares phrases to phrases: required strings are present, status blocks are
positioned, links resolve, the repository has the shape it claims. Nothing
here imports `hyperset`, so nothing here can detect a document that
CONTRADICTS the code -- a documented parameter the server refuses, a response
field that changed shape, a disclosure code the contract added and the
document never learned.

That comparison lives in `tests/unit/test_section_7_matches_the_served_contract.py`,
which imports the served contract and asserts section 7 against it. It is a
test rather than a section here on purpose: comparing a document to a contract
means importing the contract, and this script is deliberately stdlib-only.
A green run from this script is not evidence that a document agrees with the
code (hy-698).


Run all checks:
    python3 scripts/check_docs.py

Run one category:
    python3 scripts/check_docs.py --section positioning
    python3 scripts/check_docs.py --section research-status
    python3 scripts/check_docs.py --section foundation-contract
    python3 scripts/check_docs.py --section compatibility-links
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PRIMARY_DOCS = [
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "docs/architecture.md",
    "docs/v0-foundation.md",
    "docs/adr/README.md",
    "docs/adr/0006-model-agnostic-retrieval.md",
    "docs/adr/0009-vertical-slice-first.md",
    "hyperset/README.md",
    # Phrase checks only, which is all this script can do. What binds this
    # document's NUMBERS to the report that produces them is
    # tests/unit/evals/test_benchmark_md_matches_the_scored_recordings.py
    # (hy-rd7e); listing it here is so a pre-pivot phrase in it is caught too.
    "docs/development/benchmark.md",
]

OBSOLETE_PHRASES = [
    "superset of apache superset",
    "drop-in compatible dashboards",
    "semantic-layer-first",
    "hyperset's native semantic-layer store is the source of truth",
    "implementation has not begun",
]

FOUNDATION_DRIFT_PHRASES = [
    "tool contract shape is still the right shape",
    "parallel outputs of the same raw payload",
    "promoting an observation into governed context is always an explicit adapter call",
]

HISTORICAL_MARKERS = (
    "historical",
    "rejected",
    "supersed",
    "predates",
    "earlier",
    "former",
    "pre-pivot",
    "pre-connector-pivot",
    "not the current",
    "no longer",
    "old",
)

STATUS_MARKERS = ("[!WARNING]", "[!NOTE]")
CURRENT_STATUS_RE = re.compile(r"\bstatus\s*:\s*current\b", re.IGNORECASE)
HISTORICAL_STATUS_RE = re.compile(
    r"\bstatus\s*:\s*(?:historical|superseded|rejected|deprecated)", re.IGNORECASE
)

REQUIRED_STATUS_FILES = [
    "docs/research/context-model.md",
    "docs/research/from-scratch-bootstrap.md",
    "docs/research/superset-metadata-models.md",
    "docs/research/superset-version-compat.md",
    "docs/research/superset-annotation-gap.md",
    "docs/research/semantic-layer-landscape.md",
    "docs/research/agent-eval-framework.md",
]

FOUNDATION_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "docs/v0-foundation.md": (
        "status: **current and binding for v0 implementation**",
        "mandatory walking skeleton",
        "contextbundle",
        "resolve_analytics_context",
        "reviewdecision",
        "exact pinned apache superset 6.1.0",
        "performed_by_hyperset: false",
        "breadth follows proof",
        "removed boundaries",
    ),
    "AGENTS.md": (
        "docs/v0-foundation.md",
        "vertical-slice-first",
        "contextbundle",
        "resolve_analytics_context",
        "configured git context authority",
        "supported lightweight-planner path",
        "there is no supported direct `observedasset -> authoritative context`",
    ),
    "docs/architecture.md": (
        "v0-foundation.md",
        "contextbundle",
        "resolve_analytics_context",
        "`reviewrepository.approve`",
        "active packages",
        "deliberate deferrals",
    ),
    "docs/adr/0006-model-agnostic-retrieval.md": (
        "superseded by adr 0009",
        "resolve_analytics_context",
        "contextbundle",
    ),
    "docs/adr/0009-vertical-slice-first.md": (
        "status: accepted",
        "one real vertical slice",
        "resolve_analytics_context",
        "contextbundle",
        "unlock gates",
    ),
    "docs/adr/README.md": (
        "0009-vertical-slice-first.md",
        "0022-natural-language-selection-before-exact-resolution.md",
    ),
}

BROAD_COMPAT_RE = re.compile(
    r"4\.x[\s\-–]*(?:through|to|[-–])[\s]*6\.1|any 4\.x[\s\-–]*6\.x",
    re.IGNORECASE,
)
COMPAT_QUALIFIER_WINDOW = 200
COMPAT_QUALIFIERS = ("real-source", "real source", "contract test", "pinned")
MD_LINK_RE = re.compile(r"\]\((?!https?://|#)([^)]+)\)")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _is_historical_window(text: str, index: int, phrase_length: int) -> bool:
    window = text[max(0, index - 180) : index + phrase_length + 180]
    immediately_before = text[max(0, index - 5) : index]
    return "not " in immediately_before or any(marker in window for marker in HISTORICAL_MARKERS)


def _phrase_violations(phrases: list[str], label: str) -> list[str]:
    violations: list[str] = []
    for rel in PRIMARY_DOCS:
        path = ROOT / rel
        if not path.exists():
            continue
        text = _read(path).lower()
        for phrase in phrases:
            start = 0
            while (idx := text.find(phrase, start)) != -1:
                if not _is_historical_window(text, idx, len(phrase)):
                    violations.append(f"{rel}: {label} {phrase!r} (unqualified)")
                start = idx + len(phrase)
    return violations


def check_positioning() -> list[str]:
    return [
        *_phrase_violations(OBSOLETE_PHRASES, "obsolete positioning phrase"),
        *_phrase_violations(FOUNDATION_DRIFT_PHRASES, "pre-pivot authority statement"),
    ]


def check_research_status() -> list[str]:
    violations: list[str] = []
    for rel in REQUIRED_STATUS_FILES:
        path = ROOT / rel
        if not path.exists():
            violations.append(f"{rel}: file listed as requiring a status block but missing")
            continue

        text = _read(path)
        marker_positions = [
            (text.find(marker), marker) for marker in STATUS_MARKERS if text.find(marker) != -1
        ]
        if not marker_positions:
            violations.append(f"{rel}: missing [!NOTE] or [!WARNING] status block")
            continue

        marker_index, marker = min(marker_positions)
        status_window = text[marker_index : marker_index + 700]
        current = CURRENT_STATUS_RE.search(status_window) is not None
        historical = HISTORICAL_STATUS_RE.search(status_window) is not None
        if not current and not historical:
            violations.append(
                f"{rel}: status block must explicitly say current, historical, superseded, "
                "rejected, or deprecated"
            )
        if historical and marker != "[!WARNING]":
            violations.append(f"{rel}: historical/superseded/rejected research must use [!WARNING]")

        lines = text.splitlines()
        first_content_line = next(
            (i for i, line in enumerate(lines) if line.strip() and not line.startswith("#")), None
        )
        marker_line = next(
            i
            for i, line in enumerate(lines)
            if any(status_marker in line for status_marker in STATUS_MARKERS)
        )
        if first_content_line is not None and marker_line > first_content_line + 3:
            violations.append(f"{rel}: status block is not near the top of the file")
    return violations


def check_foundation_contract() -> list[str]:
    violations: list[str] = []
    for rel, requirements in FOUNDATION_REQUIREMENTS.items():
        path = ROOT / rel
        if not path.exists():
            violations.append(f"{rel}: required foundation document is missing")
            continue
        text = _read(path).lower()
        for requirement in requirements:
            if requirement.lower() not in text:
                violations.append(
                    f"{rel}: missing required v0-foundation commitment {requirement!r}"
                )
    return violations


def check_compatibility_links() -> list[str]:
    violations: list[str] = []
    for rel in PRIMARY_DOCS:
        path = ROOT / rel
        if not path.exists():
            continue
        text = _read(path)
        for match in BROAD_COMPAT_RE.finditer(text):
            window = text[
                max(0, match.start() - COMPAT_QUALIFIER_WINDOW) : match.end()
                + COMPAT_QUALIFIER_WINDOW
            ].lower()
            if not any(qualifier in window for qualifier in COMPAT_QUALIFIERS):
                violations.append(
                    f"{rel}: broad Superset version-compatibility claim {match.group(0)!r} "
                    "without a real-source/contract-test/pinned qualifier nearby"
                )
        for match in MD_LINK_RE.finditer(text):
            target = match.group(1).split("#")[0].strip()
            if not target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).resolve().exists():
                violations.append(f"{rel}: linked file does not exist: {target}")
    return violations


def check_repo_shape() -> list[str]:
    violations: list[str] = []
    removed_packages = (
        "hyperset/agent",
        "hyperset/artifacts",
        "hyperset/bridge",
        "hyperset/compat",
        "hyperset/mcp",
        "hyperset/semantic",
        "hyperset/trust",
        "context",
    )
    # A path that must not EXIST, not merely one that must hold no Python
    # (hy-qzfn). The earlier rule looked for `*.py`, so `hyperset/agent/`
    # survived as a directory containing only `__pycache__` -- exactly what a
    # later reader mistakes for a survivor of a package this project deleted by
    # name.
    #
    # Stricter and simpler, and it cannot start failing on build artefacts
    # elsewhere: it says nothing about any path that is not on this list.
    #
    # WHERE IT PROTECTS, stated because a guard that implies more than it gives
    # is worse than a narrow one (hy-u45k). CI checks out a fresh clone, so
    # untracked residue does not exist there and this cannot fire on it: on the
    # gate, this catches a denylisted path that was COMMITTED. The case that
    # motivated it -- residue left by a cherry-pick or a deleted package's
    # leftovers -- exists only in a working copy, and there this is the only
    # thing that looks. Both are worth having and they are not the same
    # protection; a reader trusting this in CI should know which one they have.
    for rel in removed_packages:
        if (ROOT / rel).exists():
            violations.append(f"{rel}: removed pre-v0 path exists; it must not")

    for rel in (
        ".agents/skills/caveman/SKILL.md",
        ".agents/skills/ponytail/SKILL.md",
        "tests/fixtures/superset/6.1.0/revenue/manifest.json",
        "tests/fixtures/superset/6.1.0/usage/manifest.json",
    ):
        if not (ROOT / rel).exists():
            violations.append(f"{rel}: required v0 workflow asset is missing")

    violations.extend(_check_agent_local_state())
    return violations


# Agent-local paths that must never become product history (hy-fwr).
_AGENT_LOCAL = (".beads", ".claude")

# The one file under those roots this repository shares on purpose: it enables
# the two skills CLAUDE.md requires every task to load, so it is project
# configuration rather than one agent's workspace. Tracked since a7cd306 and
# named here rather than exempted by a wildcard -- a second entry should have
# to argue for itself.
_SHARED_AGENT_FILES = frozenset({".claude/settings.json"})


def _check_agent_local_state() -> list[str]:
    """Two checks over one property, because neither covers the other.

    The line check reads `.gitignore` as text rather than asking `git
    check-ignore`, because that answer folds in `.git/info/exclude` -- the
    per-clone, untracked file whose absence is the whole defect. A clone that
    still carried the line locally would pass a behavioural check against a
    repository that had lost it.

    The index check asks `git ls-files`, which reads the index and no exclude
    file at all. It catches what the line check cannot: a path force-added past
    a correct ignore, which is exactly how `.beads/redirect` reached a polecat
    commit -- that clone had the exclude line too, so the line check would have
    been green on it. It is also immune to spelling, where the line check
    measures the line rather than the property: `.beads` and `/.beads/` both
    ignore the path correctly and would both fail the set-membership test. That
    false red is loud rather than silent, which is why the line check is worth
    keeping as well -- it pins the ignore against quietly leaving.
    """
    violations: list[str] = []
    ignored = {
        line.strip() for line in (ROOT / ".gitignore").read_text().splitlines() if line.strip()
    }
    for path in _AGENT_LOCAL:
        if f"{path}/" not in ignored:
            violations.append(
                f"{path}/: agent-local state is not ignored by the tracked .gitignore, "
                "so keeping it out of product history depends on each clone"
            )

    result = subprocess.run(
        ["git", "ls-files", "--", *_AGENT_LOCAL],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Reported rather than skipped: this script only ever runs inside the
        # repository, so an unreadable index is a broken check, and a check
        # that cannot run must not read as a check that passed.
        violations.append(f"could not read the git index: {result.stderr.strip()}")
    else:
        tracked = sorted(set(result.stdout.split()) - _SHARED_AGENT_FILES)
        if tracked:
            violations.append(
                f"agent-local state is tracked despite being ignored: {', '.join(tracked)}. "
                "A `git add -f` or a cherry-pick from a clone that force-added it puts "
                "workspace state into product history"
            )
    return violations


MERGE_CHECK_SCRIPTS = ("scripts/clause3-intersection.sh", "scripts/refcheck.sh")
OPEN_SET_MARKER = "OPEN SET"
SCRIPT_EXIT_RE = re.compile(r"\bexit\b[ \t]*([^\s;&|)]*)")
SCRIPT_EXIT_VAR_RE = re.compile(r"^\"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?\"?$")
SINGLE_QUOTED_RE = re.compile(r"'[^']*'")
DOC_EXIT_RE = re.compile(r"^#[ \t]+(.*)$")


def _script_exit_codes(text: str) -> tuple[set[str], list[str]]:
    """Exit codes a script can produce, and the exits that could not be read.

    Comment lines are dropped first. Without that this compares the directive to
    the script's header comment rather than to its behaviour, and a header that
    drifted from its own code would make the pair agree while both are wrong.

    `exit $rc` has to be followed into the assignments to `rc`, and finding that
    out is what this check is for: literal-only, it reported three of
    refcheck.sh's four documented codes as no longer produced, because that
    script sets `rc` and exits it once at the end. A code reachable only through
    a variable is still a code the merger has to act on.

    `exit $?` and a bare `exit` are a different case and the second return value
    is for them: they propagate a status this cannot enumerate, so the set stops
    being closed. `clause3-intersection.sh:108-109` uses `|| exit $?` to carry a
    code out of a subshell, and the set only looks complete there because the one
    code that arrives that way is also produced literally elsewhere (hy-bnnv).
    Treating an opaque exit as "no codes" is not conservative, it is wrong in the
    direction that fires: replacing that script's final `exit 0` with `exit $?`
    made this report exit 0 as no longer produced, which is false.

    Single-quoted spans are removed before any of that, and the reason is a false
    positive this check found in itself: `awk '... END {exit !f}'` at
    `clause3-intersection.sh:98` is awk's `exit`, not the shell's, and reading it
    as the shell's declared the code set open for a reason that does not exist.
    An unreadable exit is REPORTED WITH ITS ARGUMENT rather than dropped, so the
    next one of these is a line a reader can look at instead of a silent
    widening.
    """
    body = [
        SINGLE_QUOTED_RE.sub("''", line)
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
    ]
    codes: set[str] = set()
    variables: set[str] = set()
    opaque: list[str] = []
    for line in body:
        for argument in SCRIPT_EXIT_RE.findall(line):
            if argument.isdigit():
                codes.add(argument)
            elif match := SCRIPT_EXIT_VAR_RE.match(argument):
                variables.add(match.group(1))
            else:
                opaque.append(f"exit {argument}".strip())
    for name in variables:
        assignment = re.compile(rf"(?:^|[ \t;])({name})=([0-9]+)\b")
        for line in body:
            codes.update(match.group(2) for match in assignment.finditer(line))
    return codes, sorted(set(opaque))


def _documented_exit_codes(block: str, script: str) -> tuple[set[str], str]:
    """Codes annotated under `bash <script>` inside a fenced block, and that text.

    The text comes back as well as the numbers because one thing the directive
    has to be able to say is that a script's code set is OPEN -- and a claim
    about the annotation cannot be checked from the integers parsed out of it.
    """
    codes: set[str] = set()
    annotation: list[str] = []
    lines = block.splitlines()
    for index, line in enumerate(lines):
        if script not in line or line.lstrip().startswith("#"):
            continue
        for following in lines[index + 1 :]:
            match = DOC_EXIT_RE.match(following.strip())
            if match is None:
                break
            annotation.append(match.group(1))
            # The OPEN SET line is prose about the script, not a row of the
            # table, and it carries line numbers. Harvesting integers from it
            # made `:108-109` read as documented exit codes 108 and 109 --
            # invisible while that script's set is open, and two false
            # violations the moment it closes.
            if OPEN_SET_MARKER in match.group(1):
                continue
            codes.update(re.findall(r"\b([0-9]+)\b", match.group(1)))
    return codes, "\n".join(annotation)


def check_merge_check_exit_codes() -> list[str]:
    """Bind refinery.md's exit-code table to the scripts it is a table for.

    The directive tells the merger what each code obliges. A script that gains or
    renumbers an arm leaves that table describing a contract nobody implements,
    and the reader has no way to notice: an undocumented code reads as an
    ordinary failure, and a documented code that no longer exists never fires.
    Phrases against source text, like everything else here -- this does not run
    either script.

    THE BOUND, stated because a green run from this reads as "the table is
    right": it binds code NUMBERS, not what the directive says they oblige.
    Relabelling `2 REFUSE` as `2 clean, proceed` -- which would tell the merger
    to merge on a check that could not see -- leaves this silent, as does
    swapping the meanings of 0 and 4. Both measured (hy-kzpa). It is the same
    shape as hy-1pqa, where a ratchet matches predicate names only, and whatever
    rule settles that one belongs here too.
    """
    violations: list[str] = []
    rel = "docs/directives/refinery.md"
    path = ROOT / rel
    if not path.exists():
        return [f"{rel}: merge directive is missing"]
    blocks = re.findall(r"```bash\n(.*?)```", _read(path), flags=re.DOTALL)
    for script in MERGE_CHECK_SCRIPTS:
        script_path = ROOT / script
        if not script_path.exists():
            violations.append(f"{rel}: documents {script}, which does not exist")
            continue
        documented: set[str] = set()
        annotation = ""
        for block in blocks:
            block_codes, block_annotation = _documented_exit_codes(block, script)
            documented |= block_codes
            annotation += block_annotation
        if not documented:
            violations.append(
                f"{rel}: no exit-code annotation under a `bash {script}` line; "
                "the merger is told to run it and not what any answer obliges"
            )
            continue
        actual, opaque = _script_exit_codes(_read(script_path))
        for code in sorted(actual - documented, key=int):
            violations.append(
                f"{rel}: {script} can exit {code} and the directive does not say what that means"
            )
        # The second direction is only sound while the code set is CLOSED. An
        # `exit $?` can deliver a code no literal in the file produces, so
        # "documented but never produced" becomes an assertion this cannot make.
        # Suppressing it quietly would be the worse repair: the arm would vanish
        # for whichever script happened to gain an opaque exit, and a green run
        # would not say which half it had stopped checking. So the suppression
        # has to be declared in the directive to take effect.
        if opaque:
            if OPEN_SET_MARKER not in annotation:
                violations.append(
                    f"{rel}: {script} has {len(opaque)} exit(s) this cannot enumerate "
                    f"({', '.join(repr(item) for item in opaque)}), so its code set is open and "
                    f"'documented but never produced' cannot be checked for it; say so with an "
                    f"`{OPEN_SET_MARKER}` line under the invocation"
                )
            continue
        if OPEN_SET_MARKER in annotation:
            violations.append(
                f"{rel}: declares {OPEN_SET_MARKER} for {script}, which has no unenumerable exit"
            )
        for code in sorted(documented - actual, key=int):
            violations.append(
                f"{rel}: documents exit {code} for {script}, which no longer produces it"
            )
    return violations


# Guard scripts that must be named in the completion checklist. A guard in no
# checklist is the same green as no guard (hy-4k9u): nothing ran
# check_expected_failure_owners.py, so an expected-failure row that retired for
# the wrong reason kept the ratchet green with a stale owner, caught by nothing.
# Enumerated, not globbed (per the ruling), so adding a guard is a deliberate
# line here -- and this check reddens if one is enumerated but left off the list.
GUARD_SCRIPTS = (
    "scripts/check_expected_failure_owners.py",
    "scripts/gate.py",
    "scripts/check_docs.py",
)
COMPLETION_CHECKLIST = "CLAUDE.md"


def _completion_guard_violations(checklist: str, exists: Callable[[str], bool]) -> list[str]:
    """Pure core: a guard is a violation if it exists but is named nowhere in the
    checklist text, or is enumerated here but does not exist at all."""
    violations: list[str] = []
    for script in GUARD_SCRIPTS:
        if not exists(script):
            violations.append(f"{COMPLETION_CHECKLIST}: enumerates {script}, which does not exist")
        elif script not in checklist:
            violations.append(
                f"{COMPLETION_CHECKLIST}: {script} is a guard named in no completion command; "
                "a guard in no checklist is the same green as no guard"
            )
    return violations


def _run_block(text: str) -> str | None:
    """The concatenated ``bash`` command blocks, or None if there are none. Only
    these count: a guard named only in prose is not run, and counting the whole
    file would pass it -- the silent-green this check exists to stop."""
    blocks = re.findall(r"```bash\n(.*?)```", text, flags=re.DOTALL)
    return "\n".join(blocks) if blocks else None


def check_completion_guards() -> list[str]:
    """Every guard script exists and is RUN by the completion checklist (hy-4k9u)."""
    path = ROOT / COMPLETION_CHECKLIST
    if not path.exists():
        return [f"{COMPLETION_CHECKLIST}: completion checklist is missing"]
    run_commands = _run_block(_read(path))
    if run_commands is None:
        return [f"{COMPLETION_CHECKLIST}: no ```bash``` completion block to run the guards"]
    return _completion_guard_violations(run_commands, lambda script: (ROOT / script).exists())


SECTIONS: dict[str, Callable[[], list[str]]] = {
    "positioning": check_positioning,
    "research-status": check_research_status,
    "foundation-contract": check_foundation_contract,
    "compatibility-links": check_compatibility_links,
    "repo-shape": check_repo_shape,
    "merge-check-exit-codes": check_merge_check_exit_codes,
    "completion-guards": check_completion_guards,
}


def _report(section: str, violations: list[str]) -> int:
    if violations:
        print(f"{section} check failed ({len(violations)} violation(s)):")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print(f"{section} check passed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", choices=SECTIONS)
    args = parser.parse_args()

    if args.section:
        return _report(args.section, SECTIONS[args.section]())

    exit_code = 0
    for section, check in SECTIONS.items():
        exit_code |= _report(section, check())
    if exit_code == 0:
        print("Documentation and v0 foundation checks passed.")
        print(
            "Phrasing and structure only: this script imports nothing from hyperset and "
            "cannot detect a document that contradicts the code. The tests under "
            "tests/unit/ named for what they bind do that: test_section_7_..., "
            "test_section_8_..., and test_every_documented_bundle_states_the_served_version.py. "
            "Named as a set rather than singly, because pointing at one of them read as "
            "coverage: section 6's canonical example was checked by nothing at all until "
            "a bump left it stating the previous version."
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
