#!/usr/bin/env python3
"""The named suite gate: one command, one pasteable evidence line (hy-y91y).

WHY THIS EXISTS. Three agents measured the suite at f02cd60 within one hour
and recorded 472, 463 and 439 tests. All three were honest, none of them was
a regression, and all three went into durable records described as "the gate
at f02cd60". Reconciled afterwards, at the cost of a day:

    refinery  439  ran `tests/unit` only, without the optional extras
    capable   463  ran `tests/unit tests/integration`, without the extras
    critic    472  ran `tests/unit tests/integration`, with `--all-extras`

    463 - 439 = 24, which is exactly `tests/integration`.
    472 - 463 =  9, which is 11 tests replaced by 2 skip entries.

That second number is the trap this script exists to remove. A MODULE-level
`pytest.importorskip` does not skip its tests -- it removes the module from
collection and leaves ONE skip entry standing in for all of them. Measured at
f02cd60 on `tests/unit`, summary-line units, with each package made genuinely
absent:

    nothing absent           445 passed /  2 skipped / 1 xfailed  = 448
    inspect_ai absent        441 /  3 / 1                         = 445
    claude_agent_sdk absent  435 /  6 / 1                         = 442
    both absent              431 /  7 / 1                         = 439

So "N passed" is a function of the command AND the environment AND the tree,
and only the command was ever written down -- and even the command was not
enough, because capable and critic ran character-identical commands and still
differed by nine.

WHERE THE GATE STANDS ON EXTRAS, decided here and not left to the runner: the
gate requires every declared optional extra to be installed. CI already syncs
`--all-extras`, so the alternative -- a gate defined without them -- would
mean the gate and CI permanently measure different suites. This script does
not run pytest at all when an extra is missing: a collected count computed
under silent uncollection is itself a misleading number, so it refuses loudly
instead of emitting one.

TWO CHECKS, AND ONLY ONE OF THEM IS EVIDENCE. `extras` is a PREDICTION: it
reports whether each declared distribution's METADATA is present, which is
what `importlib.metadata` can answer cheaply and before pytest starts. A
broken or partial install keeps its dist-info, so `extras=all` can be printed
by an environment where the module does not import. `uncollected_modules` is
the OBSERVATION, and its subject is narrower than it reads: a module pytest
tried to collect and SKIPPED, leaving one skip entry standing for all of its
tests. Three routes reach it -- a missing extra, an extra that is installed and
unimportable, a dependency GROUP this script never reads -- each contributing
zero to `collected` and one to `skipped`, and any of them makes the run
`NOT-A-GATE` with exit 2 however green pytest itself was.

A COLLECTION ERROR IS NOT ONE OF THEM, and this paragraph claimed it was
(hy-j4m4). `pytest_collectreport` increments only `if report.skipped`, and a
collection error is a `CollectReport` whose outcome is FAILED, so it lands in
`stats["error"]`. pytest then interrupts collection and nothing runs. Measured
by driving `main()` over a temp suite of one healthy module plus one subject
module, one scenario per subprocess:

    baseline        uncollected=0  skipped=0  errors=0  passed=1  PASS        0
    importorskip    uncollected=1  skipped=1  errors=0  passed=1  NOT-A-GATE  2
    ImportError     uncollected=0  skipped=0  errors=1  passed=0  FAIL        2
    SyntaxError     uncollected=0  skipped=0  errors=1  passed=0  FAIL        2

So a collection error belongs below, with the things this field cannot see --
`errors=1` is what says it happened. It is never a false green, which is why it
was filed rather than bounced: `passed=0` also falsifies the old sentence's
"even when every collected test passed" for this member.

WHAT THAT FIELD CANNOT SEE, stated as a bound and not as a hedge: a module
removed by a collection-time EXCLUSION -- `--ignore`, `--ignore-glob`,
`norecursedirs`, `collect_ignore`, `pytest_ignore_collect` -- produces no
`CollectReport`, so `pytest_collectreport` never fires and the module
contributes zero to `collected` AND zero to `skipped`. Measured at 9505de1,
clean tree, nothing else changed: `PYTEST_ADDOPTS="--ignore=$PWD/tests/unit/
evals"` printed `collected=411` against the honest run's 492 with
`uncollected_modules=0` and `result=PASS` both times; `-k <one test name>`
printed `collected=1` the same way.

THE ENVIRONMENT HALF OF THAT IS CLOSED (hy-vkh0). `cmd=` is still a constant --
`PYTEST_INVOCATION`, never derived from argv -- so instead of asserting the
constant is true, this script MAKES it true: `PYTEST_ENVIRONMENT_INPUTS` are
cleared from `os.environ` before `pytest.main` and printed as `env_cleared`, so
an override is neutralised in the open rather than silently obeyed or silently
dropped. They are cleared before the extras check too, so `env_cleared` means
the same thing on the refusal path as on a run, and the disclosed value is
escaped -- see `_ESCAPED` -- because a quote or a newline arriving from the
environment used to cost the line its own prefix.

`sha` NAMES A COMMIT AND CANNOT NAME A TREE NO COMMIT POINTS AT (hy-3esn). The
merge seat's own procedure gates an uncommitted no-ff merge, where `sha=` is the
BASE and `tree=dirty` says only "not that one": two runs at one base print the
same `sha` for two different trees, and the tree that lands is the unnamed one.
`tree_id` is the git object id of the tree the run measured -- the repository's
own index, copied, plus every path `git add -A` would add. Its DOMAIN is
therefore every tracked path plus every untracked path `.gitignore` does not
match, and an ignored UNTRACKED path is in neither half, so two runs differing
only in one print the same id (measured; `.gitignore` here holds `.env`, which
`tests/compose/test_superset_live_sync.py` and `test_datahub_live_sync.py` read
-- bounded, because this script does not run `tests/compose`). Within that
domain, two lines carry one `tree_id` exactly when the two runs measured the
same tree, so a post-merge line equal to the trial line that stood for it is the
merge having landed what was gated, whatever `sha` did in between.

It names the content ON DISK, not what `git commit` would write from the index
as it stands, and a conflicted merge prints `unknown` rather than a tree nobody
can land. See `working_tree_id` for both of those, for the temporary index the id
is computed in, for the `.gitignore` trap that made the naive version print a
wrong id on a clean checkout, and for why seeding that index from `HEAD` was not
enough (hy-hm6h).

`env_observed` IS THE OTHER HALF OF THE ENVIRONMENT (hy-ia5h), and it is a
different list with the opposite treatment: `REPOSITORY_ENVIRONMENT_INPUTS` are
what THIS repository's tests read, they are disclosed, and they are never
cleared. `CI` is the member today, worth two `passed` and two `skipped` at one
SHA and one tree with `collected` unmoved -- so this was never a false count,
only a pair of lines a reader could not tell apart.

WHAT REMAINS OPEN, because the fix is scoped to the environment: a narrowing
COMMITTED to the tree -- `addopts` in `pyproject.toml` or `pytest.ini`,
`norecursedirs`, `collect_ignore`, `pytest_ignore_collect` -- reaches pytest
regardless, and `cmd=` cannot see it. Measured at this commit: none of those
exists in this repository. That is a fact about today and nothing enforces it.
Compare whole lines, never one field.

WHAT THIS CANNOT DO. It does not run `tests/postgres` or `tests/compose`;
those need Docker and a database, and a gate whose SET depends on services
being up is the defect this script exists to prevent. Report those two
separately, as CLAUDE.md's completion block already asks. It also proves
nothing about the absent-dependency path -- by requiring the extras it stays
permanently on the high-collection side of both module gates, exactly as CI
does.

Run it:

    uv run python scripts/gate.py

Paste the last line, whole, beside any suite size that goes into a bead, a PR
comment or a review. Two agents quoting the line at one SHA are comparing the
same set, or the line is wrong.
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Mapping, MutableMapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The one command an agent runs, and the one invocation it stands for. Both
# are asserted against AGENTS.md and CLAUDE.md by
# tests/unit/test_gate_evidence_line.py, so changing either here without
# changing the documents is red.
GATE_COMMAND = "uv run python scripts/gate.py"
GATE_PATHS = ("tests/unit", "tests/integration")
PYTEST_INVOCATION = "uv run pytest " + " ".join(GATE_PATHS) + " -q"

LINE_PREFIX = "HYPERSET-GATE v2"
"""`v2` because the shape moved: `tree_id` and `env_observed` joined the line.

Bumped rather than added silently, for the reason `SCHEMA_VERSION` moves for an
added key: a reader holding two lines has to be able to tell "this run did not
print a tree id" from "this script cannot". `parse_evidence_line` reads any
version and reports which, so the lines already pasted into beads stay
readable."""

_VERSION = re.compile(r"v\d+")
"""What the token after the name has to look like for the rest to be fields.

Matched instead of compared so that `parse_evidence_line` reads a line printed
by a version of this script that no longer exists, which is the whole reason
the version is a token rather than part of the name."""

PASS = "PASS"
FAIL = "FAIL"
NOT_A_GATE = "NOT-A-GATE"

PYTEST_ENVIRONMENT_INPUTS = (
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
)
"""The environment inputs pytest reads that can change WHICH TESTS RUN.

That is the whole membership rule, and it is narrow on purpose. `PYTEST_ADDOPTS`
appends arbitrary argv, so `-k`, `--ignore` and `--ignore-glob` all arrive
through it. `PYTEST_PLUGINS` force-imports plugins, and a plugin implements
collection hooks. `PYTEST_DISABLE_PLUGIN_AUTOLOAD` stops installed plugins from
loading, which removes whatever collection they contributed.

DELIBERATELY ABSENT, so the omissions are decisions rather than oversights:
`PYTEST_CURRENT_TEST` is pytest's OUTPUT -- it writes it during the run -- and
clearing it would corrupt the reporting of any run this is nested inside;
`PYTEST_DEBUG`, `PYTEST_DEBUG_TEMPROOT` and `PYTEST_THEME` change tracing, the
temp root and colour, none of which changes the set.

Third-party plugins with their own environment inputs are not covered by name.
Measured at this commit: `pytest` is the only installed distribution whose name
begins with `pytest`, so there is no such input today. A plugin that arrives with
one belongs on this list, by the rule in the first sentence."""

REPOSITORY_ENVIRONMENT_INPUTS = ("CI",)
"""The environment inputs THIS REPOSITORY'S OWN TESTS read to decide what runs.

A different list from `PYTEST_ENVIRONMENT_INPUTS` and a different treatment:
these are DISCLOSED and never cleared. The membership rule is that a test in
`tests/unit` or `tests/integration` branches on the value, so it can move
`passed` and `skipped` without moving `collected`.

`CI` is the live member (hy-ia5h, critic at 14c036c):
`tests/unit/planner/test_planner.py` and
`tests/unit/planner/test_runtime_conformance.py` each skip when it is unset.
Measured at that SHA, same clean tree, nothing else changed --
`collected=526 passed=523 skipped=2` with `CI` unset against
`collected=526 passed=525 skipped=0` with `CI=true`, every other field
identical. `collected` holds, so this was never a false count; what was missing
was any way to tell the two lines apart.

CLEARING IT WOULD BE THE WRONG FIX, which is why the two lists are separate.
Those two tests are backstops against a CI that synced without `--all-extras`,
so clearing `CI` would make them skip in the one place they exist to fire.
Neutralising an input is right when the input NARROWS the set; it is wrong when
the input is what the environment legitimately is. Disclosed, so a CI line and
a laptop line are distinguishable to a reader who knows nothing about either."""

NO_ENVIRONMENT_INPUT = "none"
"""Printed by `env_cleared` and `env_observed` when there was nothing to report.
A positive claim, because an absent field reads as an older version of the
line."""

UNKNOWN = "unknown"
"""What `sha` and `tree_id` say when git could not answer -- no repository, no
commit, no git on PATH. Named rather than blank for the same reason as
`NO_ENVIRONMENT_INPUT`: a field that vanishes is indistinguishable from a line
an older script printed."""

# PEP 508 requirement strings begin with the distribution name; everything
# from the first version specifier, extra bracket or marker onwards is not it.
_DISTRIBUTION_NAME = re.compile(r"^\s*([A-Za-z0-9._-]+)")


def extra_distributions(pyproject_text: str) -> tuple[str, ...]:
    """Every distribution named by `[project.optional-dependencies]`.

    Read from the file rather than hard-coded so that declaring a new extra
    puts it behind the gate without anyone remembering to add it here. The
    two that started this -- `inspect-ai` and `claude-agent-sdk` -- are only
    the extras that existed when it was written.
    """
    optional = tomllib.loads(pyproject_text).get("project", {}).get("optional-dependencies", {})
    names: list[str] = []
    for requirements in optional.values():
        for requirement in requirements:
            match = _DISTRIBUTION_NAME.match(requirement)
            if match and match.group(1) not in names:
                names.append(match.group(1))
    return tuple(names)


def missing_distributions(names: tuple[str, ...], is_installed) -> tuple[str, ...]:
    return tuple(name for name in names if not is_installed(name))


def _installed(name: str) -> bool:
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def strip_pytest_environment(environ: MutableMapping[str, str]) -> dict[str, str]:
    """Remove every `PYTEST_ENVIRONMENT_INPUTS` name, returning what was set.

    Takes the mapping rather than reaching for `os.environ`, so the membership
    decision is testable without mutating the environment of the run doing the
    testing.
    """
    return {name: environ.pop(name) for name in PYTEST_ENVIRONMENT_INPUTS if name in environ}


_ESCAPED = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
"""What an override's value may not carry into the line, and what it becomes.

The value is arbitrary text from outside this script, echoed inside a
double-quoted field, so two things have to hold. A `"` in it ended the field
early and `parse_evidence_line` refused the whole line. A NEWLINE was worse: it
SPLIT the line, and the fragment carrying `collected` and `result` had no prefix
and no `sha` -- a bare count with no provenance, which is the defect named in
this module's first paragraph. Escaping is visible, and POSIX `shlex` unescapes
`\\"` inside a double-quoted field, so the field round-trips to the raw value."""


def _disclosable(value: str) -> str:
    """One line, one field, and still readable as what was set."""
    return "".join(_ESCAPED.get(character, character) for character in value)


def render_cleared_environment(cleared: Mapping[str, str]) -> str:
    """The neutralised inputs as one field value: disclosed, never erased.

    A gate that silently dropped an override would replace a false count with a
    true count and an unexplained difference from the run the operator thought
    they asked for.

    The counts were never wrong under any of these values, so this escapes
    rather than refuses: what an odd override threatened was the line's
    readability, and a refusal would throw away a correct measurement to
    punish a quotation mark.
    """
    if not cleared:
        return NO_ENVIRONMENT_INPUT
    return ";".join(f"{name}={_disclosable(value)}" for name, value in cleared.items())


def render_observed_environment(environ: Mapping[str, str]) -> str:
    """The `REPOSITORY_ENVIRONMENT_INPUTS` that were set, as one field value.

    Reads the mapping rather than clearing it, which is the difference from
    `render_cleared_environment` in one line: that field says what this run
    removed, this one says what this run ran under.
    """
    observed = {name: environ[name] for name in REPOSITORY_ENVIRONMENT_INPUTS if name in environ}
    if not observed:
        return NO_ENVIRONMENT_INPUT
    return ";".join(f"{name}={_disclosable(value)}" for name, value in observed.items())


def working_tree_id(root: Path = ROOT) -> str:
    """The git object id of the tree this run is measuring, committed or not.

    `sha` names a COMMIT, so it cannot name a tree no commit points at, and
    `tree=dirty` only says the measured tree is not the one `sha` names. The
    merge seat's own procedure runs the gate on an uncommitted no-ff merge
    (hy-3esn), where those two fields together print the base's id twice for two
    different trees and never identify either. This is the id of the tree, so a
    post-merge line and the trial line that stood for it are equal exactly when
    the tree that landed is the tree that was measured.

    Computed in a TEMPORARY index, for two reasons that both cost a measurement
    when they were got wrong. The real index is untouched, so a gate run in the
    middle of an edit cannot stage anything. And the temporary index is SEEDED
    before `git add -A`, because `add -A` applies `.gitignore` to every path it
    has never seen: measured on a CLEAN checkout of this repository at 9df1a19,
    an empty temporary index gave a80ee89 where `HEAD^{tree}` is 8ad82ee,
    because `CLAUDE.md` and `.claude/settings.json` are tracked AND matched by
    `.gitignore`, so they dropped out of the id that claimed to identify the
    tree.

    THE SEED IS A COPY OF THE REAL INDEX, not `read-tree HEAD` (hy-hm6h).
    Seeding from `HEAD` covers every ignored path already tracked in HEAD and no
    others, so an ignored path a MERGE INTRODUCES is still untracked to
    `add -A`, still dropped, and the trap survives one commit further along.
    Measured on a fixture built to hy-3esn's own sentence: two trial merges
    landing DIFFERENT trees (b27c488d and 1590ad91) printed one `sha` and one
    `tree_id` b27c488d. The real index during an uncommitted merge already
    holds the merged content, ignored paths included, so a copy of it seeds
    what actually merged; on that fixture the copy returns the tree that would
    land.

    TWO BOUNDS, both measured, because the mechanism above states exactly what
    the id ranges over and a reader is owed what falls outside it. An ignored
    path that is UNTRACKED is in neither half -- not in the index, and not
    something `add -A` will add -- so two runs differing only in one print the
    same id (`.gitignore` here holds `.env`, which `tests/compose` reads; bounded,
    because this script does not run `tests/compose`). And the id names the
    content ON DISK, not what `git commit` would write from the index as it
    stands: across the gap this seat opens by gating a resolved merge before
    `git add`, a path deleted in the working tree alone printed 21c428d6 where
    the tree that landed was 9f2260ab. Comparing a trial line to a post-merge
    line survives both, since both are computed this way; verifying a line
    against `HEAD^{tree}` of the merge commit needs the tree to have been clean.

    UNMERGED entries are checked FOR, not waited on. Letting `write-tree` fail
    was the earlier plan and it does not work: the seed copies the unmerged
    entries in, then `git add -A` stages the marker-bearing working-tree file at
    stage 0, resolving the conflict inside the temporary index. Measured on a
    real conflict at 316b940 -- `git write-tree` on the REAL index exits 128
    `fatal: git-write-tree: error building trees`, while this printed 8de0d87d,
    a tree whose conflicted file holds `<<<<<<< HEAD`. That is this function's
    own failure class through a different door: an id nobody can land, printed
    with no hedge. So `git ls-files -u` asks the real index first, and a
    non-empty answer returns `unknown` -- mid-conflict there is no tree to name.

    `ls-files -u` and `--git-path index` read the same index, including the one
    an exported `GIT_INDEX_FILE` puts in force, so the refusal and the seed
    cannot disagree about which index they are talking about.
    """
    unmerged = subprocess.run(["git", "ls-files", "-u"], cwd=root, capture_output=True, text=True)
    if unmerged.returncode != 0 or unmerged.stdout.strip():
        return UNKNOWN
    with tempfile.TemporaryDirectory() as directory:
        index = Path(directory) / "index"
        if not _seed_from_real_index(root, index):
            return UNKNOWN
        environment = {**os.environ, "GIT_INDEX_FILE": str(index)}
        added = subprocess.run(
            ["git", "add", "-A"], cwd=root, env=environment, capture_output=True, text=True
        )
        if added.returncode != 0:
            return UNKNOWN
        written = subprocess.run(
            ["git", "write-tree"], cwd=root, env=environment, capture_output=True, text=True
        )
    return written.stdout.strip() if written.returncode == 0 else UNKNOWN


def _seed_from_real_index(root: Path, destination: Path) -> bool:
    """Copy the repository's index to `destination`, or report that it could not.

    Located with `git rev-parse --git-path index` rather than assembled as
    `.git/index`, because `.git` is a FILE in a worktree and this script runs in
    one: the critic reviews from a worktree, and a hand-built path would have
    returned `unknown` there while working in the main checkout.

    Read-only on the real index -- copied, never opened for writing -- so
    `test_identifying_the_tree_does_not_stage_anything` holds by construction
    rather than by the temporary index alone.

    `copy2`, NOT `copyfile`, because the copy's mtime is load-bearing (hy-ooiv).
    git re-hashes an entry only when its cached mtime is at least the index
    file's own mtime -- its racy-clean rule, and its own protection against a
    write landing in the same filesystem-timestamp second as the last index
    update. `copyfile` gave the temporary index a FRESH mtime, so every entry
    was strictly older, git ruled them all definitively clean, and a same-second
    same-size rewrite went into the tree as the COMMITTED blob. Measured by the
    refinery on an ordinary repository: two consecutive calls over one unchanged
    working tree returned 805cf4d3 and a4a62f8c, the second carrying `one` for a
    file holding `two`, which is one `tree_id` naming two different trees -- the
    failure this field exists to prevent, reached through the seed. Preserving
    the mtime hands git's protection back rather than replacing it with a
    `--really-refresh` of our own, which would re-stat the whole tree on every
    gate run to re-derive an answer git already has.
    """
    located = subprocess.run(
        ["git", "rev-parse", "--git-path", "index"], cwd=root, capture_output=True, text=True
    )
    if located.returncode != 0:
        return False
    source = Path(located.stdout.strip())
    if not source.is_absolute():
        source = root / source
    if not source.exists():
        # A repository with no index yet -- `git init` before the first `add`.
        # `read-tree HEAD` failed there too, and there is no tree to name.
        return False
    shutil.copy2(source, destination)
    return True


def evidence_line(
    *,
    sha: str,
    tree: str,
    tree_id: str,
    extras: str,
    collected: int,
    uncollected_modules: int,
    counts: dict[str, int],
    result: str,
    invocation: str = PYTEST_INVOCATION,
    env_cleared: str = NO_ENVIRONMENT_INPUT,
    env_observed: str = NO_ENVIRONMENT_INPUT,
) -> str:
    """One line, one set. Order is fixed so two lines diff readably."""
    fields = [
        LINE_PREFIX,
        f"sha={sha}",
        f"tree={tree}",
        f"tree_id={tree_id}",
        f"extras={extras}",
        f'cmd="{invocation}"',
        f'env_cleared="{env_cleared}"',
        f'env_observed="{env_observed}"',
        f"collected={collected}",
        f"uncollected_modules={uncollected_modules}",
    ]
    for outcome in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed"):
        fields.append(f"{outcome}={counts.get(outcome, 0)}")
    fields.append(f"result={result}")
    return " ".join(fields)


def parse_evidence_line(line: str) -> dict[str, str]:
    """Split a pasted line back into its fields, quoted command included.

    ANY version parses, and the version is returned as `line_version` rather
    than checked. Lines are pasted into beads and PR comments to be compared
    later, so a parser that accepted only today's shape would make every field
    added afterwards cost the record already written -- and the reader's real
    question, "did this run print a tree id or can its script not", is answered
    by the version beside the missing field, not by a refusal.
    """
    tokens = shlex.split(line.strip())
    name, _, _ = LINE_PREFIX.partition(" ")
    if len(tokens) < 2 or tokens[0] != name or not _VERSION.fullmatch(tokens[1]):
        raise ValueError(f"not a gate evidence line: {line!r}")
    fields: dict[str, str] = {"line_version": tokens[1]}
    for token in tokens[2:]:
        key, separator, value = token.partition("=")
        if not separator:
            raise ValueError(f"unparseable field {token!r} in {line!r}")
        fields[key] = value
    return fields


class GitEnvError(RuntimeError):
    """A git invocation failed for an ENVIRONMENT reason -- not a git repository, a
    ref/object that does not resolve, git missing -- as distinct from a clean merge
    or a genuine conflict. It is raised, never returned as `None`, because a poll
    that read an env fault as "no single tree, skip" skipped EVERY PR silently: the
    refinery ran `would_land_tree` from a non-git scratchpad copy, git errored
    outside a repo, and every verdict's tree-identity check came back `None`, so
    `merge_authorizers` went empty and nothing ever landed while the log said
    "polling" (hy-wpqa). A genuine conflict is a fact about the code; an env fault
    is a fact about the runner, and the two must not share an answer."""


_OID = re.compile(r"^[0-9a-f]{40}$")


def would_land_tree(base: str, head: str, root: Path | None = None) -> str | None:
    """The tree that would result from landing `head` onto `base` right NOW, or
    `None` when that merge CONFLICTS and so has no single tree to name (hy-kiwk).

    `git merge-tree --write-tree` computes it from the object graph alone -- no
    checkout, index, or working tree -- as the same 3-way merge the merger's
    tree-match discipline already reads. It is fast (no test run) and, being
    recomputed on every call, is a graph fact and never a stored one.

    When `base` is an ancestor of `head` the merge is a fast-forward and this is
    `head^{tree}`; when both sides carry the same content by different commits it
    is STILL that tree. Only base content the head does not already have changes it.

    `None` means CONFLICT and ONLY conflict. A conflicting merge exits non-zero but
    still writes the merged tree oid on stdout line 1 (followed by the conflicted
    stages), so a non-zero exit whose first line IS a 40-hex oid is a real conflict
    -> `None`. A non-zero exit with NO tree oid -- `fatal: not a git repository`, a
    ref that does not resolve, git absent -- is an ENVIRONMENT fault and raises
    `GitEnvError`, never a silent `None` (hy-wpqa). The env fault that returned
    `None` for every PR is now impossible to mistake for "nothing to land".
    """
    result = subprocess.run(
        ["git", "merge-tree", "--write-tree", base, head],
        cwd=str(ROOT if root is None else root),
        capture_output=True,
        text=True,
    )
    lines = result.stdout.splitlines()
    first = lines[0].strip() if lines else ""
    if result.returncode == 0:
        return first or None
    # Non-zero: a genuine conflict still names the merged tree on line 1; anything
    # without that oid is the runner's fault, not the code's, and must be loud.
    if _OID.match(first):
        return None
    raise GitEnvError(
        f"git merge-tree could not compute a merge of {base}..{head} in "
        f"{root if root is not None else ROOT}: rc={result.returncode}, "
        f"stderr={result.stderr.strip()!r}. This is a git ENVIRONMENT fault "
        "(not a repo, an unresolvable ref, git missing) -- not a merge conflict."
    )


def is_git_repo(root: Path | None = None) -> bool:
    """Whether `root` (default `ROOT`) is inside a real git work tree. The poll
    asserts this at startup: the hy-wpqa stall was running the whole poll from a
    non-git directory, where every merge-tree call fails for the environment, not
    the code."""
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(ROOT if root is None else root),
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def gate_describes_would_land(
    *, tree_id: str, base: str, head: str, root: Path | None = None
) -> bool:
    """Whether a gate that measured `tree_id` still describes what would land NOW.

    A HYPERSET-GATE line is a permanent claim that ONE tree passes; it does not
    expire (hy-kiwk). "Stale" is the wrong word for "no longer describes what
    would land now": the gate stays exactly true about the tree it named, and the
    only question at re-gate time is whether the tree that would land now IS that
    tree. So validity is a tree-IDENTITY check and NOT a function of the base:

      * a base move the head ALREADY CONTAINS -- a landed commit that is an
        ancestor of the head (a fast-forward), OR the same content reached by a
        different commit -- leaves the would-land tree unchanged, so the gate
        stays valid and no re-run is owed. Both are one mechanism, "the head
        already has it", and the tree comparison catches both where a bare
        ancestor test would catch only the first.
      * a base move in content the head does NOT have, or a new head (real new
        work), produces a DIFFERENT would-land tree, so this gate is a true claim
        about a tree that will not land -- a fresh measurement is owed, not
        because the gate is wrong but because it is about another tree.

    Recomputed from the graph each call, so there is no stored result and hence no
    false-HIT mode in which a skipped gate was in fact needed.
    """
    if not tree_id or tree_id == UNKNOWN:
        return False
    landed = would_land_tree(base, head, root)
    return landed is not None and landed == tree_id


class _Recorder:
    """Counts taken from pytest itself rather than parsed off its output.

    `uncollected_modules` is the field the reconciliation above needed and
    nobody had: a module SKIPPED during collection contributes ZERO to
    `collected` and ONE to `skipped`, so without this field the two numbers
    silently disagree about how many tests exist.

    "Skipped during collection" is the exact subject, per the docstring above:
    `pytest_collectreport` fires for a module pytest tried to collect, and this
    counts it only when that report is skipped. A collection ERROR is a report
    whose outcome is FAILED and lands in `errors` instead (hy-j4m4); a module an
    exclusion removed yields no report at all.
    """

    def __init__(self) -> None:
        self.collected = 0
        self.uncollected_modules = 0
        self.counts: dict[str, int] = {}

    def pytest_collectreport(self, report) -> None:
        if report.skipped:
            self.uncollected_modules += 1

    def pytest_collection_finish(self, session) -> None:
        self.collected = len(session.items)

    def pytest_terminal_summary(self, terminalreporter) -> None:
        stats = terminalreporter.stats
        self.counts = {
            "passed": len(stats.get("passed", ())),
            "failed": len(stats.get("failed", ())),
            "errors": len(stats.get("error", ())),
            "skipped": len(stats.get("skipped", ())),
            "xfailed": len(stats.get("xfailed", ())),
            "xpassed": len(stats.get("xpassed", ())),
        }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()


def main() -> int:
    sha = _git("rev-parse", "HEAD") or UNKNOWN
    tree = "dirty" if _git("status", "--porcelain") else "clean"
    tree_id = working_tree_id()
    observed = render_observed_environment(os.environ)

    # Cleared BEFORE anything can return, and before `pytest.main` above all,
    # which is what makes `cmd=` true by construction rather than by assertion.
    # Ahead of the extras check rather than after it so `env_cleared` means one
    # thing on every path this can take: the refusal line used to print `none`
    # while an override sat in the environment untouched. Restored in the
    # `finally` because `main` is also called in-process -- by this repository's
    # own tests among others -- and a gate that permanently edits its caller's
    # environment is a second surprise of the kind this closes.
    cleared = strip_pytest_environment(os.environ)
    disclosure = render_cleared_environment(cleared)
    if cleared:
        print(
            "Neutralised pytest environment inputs so this run is the gate's: "
            f"{disclosure}. They are disclosed on the line and restored afterwards.",
            file=sys.stderr,
        )
    try:
        return _gate(sha=sha, tree=tree, tree_id=tree_id, disclosure=disclosure, observed=observed)
    finally:
        os.environ.update(cleared)


def _gate(*, sha: str, tree: str, tree_id: str, disclosure: str, observed: str) -> int:
    """The gate proper, with pytest's environment inputs already neutralised."""
    missing = missing_distributions(
        extra_distributions((ROOT / "pyproject.toml").read_text(encoding="utf-8")), _installed
    )
    if missing:
        print(
            "The optional extras are not installed, so whole test modules would be "
            "replaced by one skip entry each and the count would not be the gate's. "
            f"Missing: {', '.join(missing)}. Run `uv sync --all-extras --all-groups`.",
            file=sys.stderr,
        )
        print(
            evidence_line(
                sha=sha,
                tree=tree,
                tree_id=tree_id,
                extras="missing:" + ",".join(missing),
                collected=0,
                uncollected_modules=0,
                counts={},
                result=NOT_A_GATE,
                env_cleared=disclosure,
                env_observed=observed,
            )
        )
        return 2

    import pytest

    recorder = _Recorder()
    exit_code = pytest.main([*(str(ROOT / path) for path in GATE_PATHS), "-q"], plugins=[recorder])

    if recorder.uncollected_modules:
        print(
            f"{recorder.uncollected_modules} module(s) were removed from collection, each "
            "leaving one skip entry standing for all of its tests, so this count is not "
            "the gate's. Run `uv sync --all-extras --all-groups` and check that every "
            "declared extra IMPORTS, not merely that it is installed.",
            file=sys.stderr,
        )

    result = NOT_A_GATE if recorder.uncollected_modules else (PASS if int(exit_code) == 0 else FAIL)
    print(
        evidence_line(
            sha=sha,
            tree=tree,
            tree_id=tree_id,
            extras="all",
            collected=recorder.collected,
            uncollected_modules=recorder.uncollected_modules,
            counts=recorder.counts,
            result=result,
            env_cleared=disclosure,
            env_observed=observed,
        )
    )
    if result == NOT_A_GATE:
        return 2
    return int(exit_code)


def describes_cli(argv: list[str]) -> int:
    """`gate.py describes <base> <head> <tree-id-or-gate-line>` (hy-kiwk).

    The re-gate-time question as a command a merger or refinery can shell out to:
    does the gate that measured this tree still describe what would land from
    `base..head` now? Exit 0 REUSE (the would-land tree IS the measured one, skip
    the re-run), exit 1 RE-GATE (a different tree would land, measure it). The
    third argument is a bare tree id or a whole HYPERSET-GATE line, so a caller
    can pass the record it already has without extracting the field first.
    """
    if len(argv) != 3:
        print("usage: gate.py describes <base> <head> <tree-id-or-gate-line>", file=sys.stderr)
        return 2
    base, head, record = argv
    try:
        tree_id = parse_evidence_line(record).get("tree_id", "")
    except ValueError:
        tree_id = record.strip()
    if not tree_id or tree_id == UNKNOWN:
        print(f"RE-GATE: the record names no tree id ({tree_id or 'missing'})")
        return 1
    # Computed ONCE and reused for the decision and the message: a merger asking
    # the question should not pay for two merge-trees on the re-gate path.
    landed = would_land_tree(base, head)
    if landed is not None and landed == tree_id:
        print(
            f"REUSE: the gate for tree {tree_id} still describes what would land from {head[:12]}"
        )
        return 0
    if landed is None:
        # merge-tree exits non-zero on a real conflict AND on a base/head it
        # cannot resolve; either way nothing clean lands, so this does not
        # over-claim a conflict it has not proven.
        detail = "no single tree would land (a merge conflict, or an unresolvable base/head)"
    else:
        detail = f"would land {landed[:12]}, not the gated {tree_id[:12]}"
    print(f"RE-GATE: {detail}")
    return 1


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "describes":
        raise SystemExit(describes_cli(sys.argv[2:]))
    raise SystemExit(main())
