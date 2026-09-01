"""The reporting path answers about the run it was handed, and nothing else.

Two properties, and neither had a test before hy-szg4 -- both were true of the
code and true by nobody's decision, which is the state a refactor changes
without anything going red.

FIRST, THE REPORT READS NO STORE AND RUNS NOTHING. `stability_report` derives
both evidence identities from the recording's own trace, so a version fetched
live at report time would be today's answer about yesterday's run: the model
read what it read. Asserted by making the store and the subprocess unusable and
then producing a report anyway.

SECOND, THE REPORTING MODULE CANNOT REACH AN INFERENCE RUNTIME. `stability.py`
imported the evidence walk from `evals/run.py`, which put `subprocess` and
`OpenAIAgentsRuntime` in its import closure -- a module that reports on runs
being one import away from starting one. `source_identity.py` exists to hold the
walk without that reachability.
"""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

from hyperset.evals.cases import load_cases
from hyperset.evals.recording import GOVERNED_ARM, Recording
from hyperset.evals.run import recordings_of
from hyperset.evals.stability import parse_stability_line, stability_report

ROOT = Path(__file__).resolve().parents[3]

FORBIDDEN = (
    "subprocess",
    "agents",
    "hyperset.evals.run",
    "hyperset.planner.openai_runtime",
)
"""What a module that REPORTS on runs must not be able to reach.

The capability the hy-szg4 change nearly added and the store/subprocess test
above pins behaviourally: inference, and a subprocess.
"""

REPOSITORY_FORBIDDEN = (
    "hyperset.repositories",
    "hyperset.bundle.resolver",
    "hyperset.transport.operations",
    "hyperset.evals.arms",
    "hyperset.evals.raw_arm",
)
"""What the eval reporting/scoring SOURCE closure must not name (hy-quol).

A customer who installs the eval surface to SCORE recorded runs reads a
recording and applies predicates; that path has no business PULLING the Postgres
repository layer or the bundle resolver runtime. It used to, by two source
edges, both now cut:

- `evals.pins -> transport.operations` (top-level) and `evals.pins ->
  evals.arms -> raw_arm -> repositories.postgres` (function-body, but the static
  walk still counted it) -- both existed ONLY to serve `repository_pins`, the
  #25 record/gate path, now behind a lazy `importlib.import_module` boundary in
  `pins.py`.
- `source_identity -> raw_arm` for one operation-name string, now imported from
  the leaf `evals.raw_operations` instead of the heavy arm module.

`bundle.schema` and `bundle.equivalence` remain reachable: leaf value/schema
modules the predicates legitimately read (e.g. `REF_NOT_OBSERVED`), carrying no
repository or resolver of their own -- they are not the bundle RUNTIME.

STATIC, by `import_closure` over the SOURCE, the same tool the critic measured
this bead's edges with. It does not traverse package `__init__` files, so it is
a guard against RE-ADDING a source edge, not a claim about `sys.modules`: at
runtime, importing any `hyperset.planner.*` submodule still runs
`hyperset/planner/__init__.py`, which eagerly imports `loop`/`executor` (-> the
resolver and the repository). That residual is pre-existing and repo-wide, not
on the eval path, and lazifying that core package's re-exports is tracked
separately (hy-v09i) rather than smuggled into an eval-hygiene change.
"""

REPORTING_AND_SCORING = (
    "hyperset.evals.stability",
    "hyperset.evals.scorers",
    "hyperset.evals.recording",
    "hyperset.evals.source_identity",
    "hyperset.evals.pins",
    "hyperset.eval.scorer",
    "hyperset.eval.runner",
)
"""The reporting module, the scoring predicates, the recording they read, and
the shipped customer scorer/runner (#400). None may name a repository or the
resolver in its source closure."""


def module_file(name: str) -> Path | None:
    direct = ROOT / (name.replace(".", "/") + ".py")
    if direct.exists():
        return direct
    package = ROOT / name.replace(".", "/") / "__init__.py"
    return package if package.exists() else None


def imported_names(path: Path) -> set[str]:
    """Every absolute module name this file imports, dotted attributes included.

    Read with `ast` rather than by importing and inspecting `sys.modules`: the
    question is what the source says, and a test that imports the module under
    test to measure its imports is answering it from a process that already
    imported everything else too.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names |= {node.module} | {f"{node.module}.{alias.name}" for alias in node.names}
    return names


def import_closure(start: str) -> dict[str, str]:
    """Every module reachable from `start`, mapped to the importer that pulled it."""
    reached: dict[str, str] = {start: start}
    pending = [start]
    while pending:
        current = pending.pop()
        path = module_file(current)
        if path is None:
            continue
        for name in imported_names(path):
            candidate = name if module_file(name) else name.rsplit(".", 1)[0]
            resolved = candidate if module_file(candidate) else name.split(".")[0]
            if resolved not in reached:
                reached[resolved] = current
                if module_file(resolved):
                    pending.append(resolved)
    return reached


def test_the_reporting_module_cannot_reach_an_inference_runtime_or_a_subprocess():
    """The import edge this change broke, asserted so it cannot come back.

    A reporting path that imports the runtime is one refactor away from being
    able to run the thing it reports on, and the refactor that reintroduced it
    would be a one-line import with every other test still green.
    """
    closure = import_closure("hyperset.evals.stability")

    reachable = {name: closure[name] for name in FORBIDDEN if name in closure}

    assert not reachable, (
        f"hyperset.evals.stability reports on runs and must not reach {sorted(reachable)} -- "
        f"pulled in by {sorted(set(reachable.values()))}"
    )
    assert "hyperset.evals.source_identity" in closure, (
        "and it reaches the walk itself, so this test is measuring the closure it thinks it is"
    )


def test_the_eval_reporting_and_scoring_source_closure_is_repository_free():
    """No eval reporting/scoring module names a repository or the resolver.

    The edge hy-quol cut: `evals.scorers -> recording -> pins -> ...` reached
    `hyperset.repositories.postgres` (via `transport.operations -> bundle` and
    via `arms -> raw_arm`), and `stability -> source_identity -> raw_arm` reached
    it too. Both are gone from the SOURCE closure now; this fails the moment a
    source edge to a repository or the bundle resolver comes back on the path a
    customer imports to score recorded runs. See `REPOSITORY_FORBIDDEN` for the
    boundary (leaf `bundle.schema`/`equivalence` are allowed; the runtime
    `planner/__init__` residual is a tracked, out-of-scope follow-on).
    """
    dirty: dict[str, list[str]] = {}
    for start in REPORTING_AND_SCORING:
        closure = import_closure(start)
        reachable = sorted(
            name
            for name in closure
            for bad in REPOSITORY_FORBIDDEN
            if name == bad or name.startswith(bad + ".")
        )
        if reachable:
            dirty[start] = reachable

    assert not dirty, (
        "the eval reporting/scoring source closure must not name a repository or the bundle "
        f"resolver, but reached: {dirty}"
    )


def test_a_report_reads_no_store_and_starts_no_process(monkeypatch):
    """The committed recordings, reported with the store and subprocess denied.

    `git_commit` comes from `git rev-parse HEAD` in a subprocess and the evidence
    payload comes from a repository read -- both at RECORD time. A report that
    reached for either would be answering today's question about yesterday's
    run, and the recording is the only thing it is allowed to consult. Denying
    them is how that stays true: a refactor that re-reads an asset to freshen an
    identity constructs a repository, and this goes red.
    """
    import hyperset.repositories.postgres.observed_assets as observed_assets

    def refuse(*args, **kwargs):
        raise AssertionError("the reporting path consulted something other than the recording")

    monkeypatch.setattr(observed_assets.PostgresObservedAssetRepository, "__init__", refuse)
    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    payload = json.loads(recordings_of(GOVERNED_ARM, "revenue_by_region")[0].read_text())
    recordings = [Recording.from_dict(payload), Recording.from_dict(payload)]
    case = next(entry for entry in load_cases() if entry.id == "revenue_by_region")

    subject = stability_report(recordings, case)

    fields = parse_stability_line(subject.line())
    assert fields["case"] == "revenue_by_region"
    assert int(fields["source_refs_distinct"]) == 1
    assert int(fields["source_versions_distinct"]) == 1, (
        "one recording read twice: the same bytes must come out one set at both identities, or "
        "this test would pass while measuring nothing"
    )
    assert fields["unversioned"] == "0", "the committed governed recording carries versions"


def test_the_denial_this_test_relies_on_actually_fires(monkeypatch):
    """The other direction, so the test above is evidence rather than a hope.

    A monkeypatch that silently failed to bind -- a renamed class, a repository
    constructed through another name -- would leave the assertion above green
    while nothing was denied at all.
    """
    import hyperset.repositories.postgres.observed_assets as observed_assets

    def refuse(*args, **kwargs):
        raise AssertionError("denied")

    monkeypatch.setattr(observed_assets.PostgresObservedAssetRepository, "__init__", refuse)

    with pytest.raises(AssertionError, match="denied"):
        observed_assets.PostgresObservedAssetRepository(session_factory=None)
