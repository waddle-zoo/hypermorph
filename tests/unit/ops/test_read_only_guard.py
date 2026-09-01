"""The operator view is read-only, enforced as code (hy-9vji, design section 3).

`hyperset/ops` must never reach a repository WRITER. Rather than curate a denylist
that rots as repositories grow, this DISCOVERS the writers structurally: a
Postgres repository method is a writer exactly when its body opens a write
transaction (`session.begin()`) or calls a session mutation primitive. It then
asserts nothing REACHABLE FROM `hyperset/ops` calls any of those names, nor a raw
write primitive itself. A future slice (S2-S5) that reaches for a writer -- a
"quick triage from the status view" -- reds here.

REACHABLE, not just directly called (hy-hske, critic): the ops surface reaches
`resolver._linked_evidence` as a BARE function import, not `repo.method()`, and a
guard that only inspects `x.method()` inside files under `hyperset/ops/` never
walks into that function to see what it calls -- a writer one bare call away was
invisible. So the walk RESOLVES bare-name calls to the hyperset module that
defines them and descends into that FunctionDef, transitively, collecting the
`x.method()` names reached along the way. The bound is stated so it is not
re-overclaimed: it follows bare-name calls to `def`/`async def` in hyperset
modules; it does NOT enter class constructors or dynamic dispatch, which remain
a follow-up if ops ever reaches a writer that way.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OPS_DIR = ROOT / "hyperset" / "ops"
REPOS_DIR = ROOT / "hyperset" / "repositories" / "postgres"

# Session mutation primitives: a write transaction and the ORM writes it wraps.
# A repository method that calls any of these mutates; so would ops if it called
# them directly.
WRITE_PRIMITIVES = frozenset({"begin", "add", "delete", "merge", "commit"})


def _called_attrs(node: ast.AST) -> set[str]:
    """Every `x.method(...)` method name called anywhere under `node`."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            names.add(child.func.attr)
    return names


def _bare_call_names(node: ast.AST) -> set[str]:
    """Every `name(...)` bare-function call anywhere under `node`.

    These are the calls `_called_attrs` cannot see and the ones the ops guard was
    blind to: `_linked_evidence(session_factory, ...)` is one of them.
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            names.add(child.func.id)
    return names


def _func_defs(tree: ast.AST) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _module_file(dotted: str, root: Path) -> Path | None:
    """The file a `hyperset...` dotted module resolves to, or None for a package
    with no `__init__` / a name outside the tree."""
    base = root / Path(*dotted.split("."))
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py")
    if (base / "__init__.py").exists():
        return base / "__init__.py"
    return None


def _from_imports(tree: ast.AST, root: Path) -> dict[str, tuple[Path, str]]:
    """local name -> (defining file, original name) for `from hyperset... import x`.

    Only absolute hyperset imports: a writer lives in a hyperset repository, so a
    bare call that could reach one is a bare call to a hyperset-defined name.
    """
    mapping: dict[str, tuple[Path, str]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.level == 0
            and node.module
            and node.module.split(".")[0] == "hyperset"
        ):
            path = _module_file(node.module, root)
            if path is None:
                continue
            for alias in node.names:
                mapping[alias.asname or alias.name] = (path, alias.name)
    return mapping


def _reachable_called_methods(entry_files: list[Path], root: Path = ROOT) -> set[str]:
    """Every `x.method(...)` name reachable from `entry_files`, descending through
    bare-name calls that resolve to a hyperset-defined function.

    A whole entry file is walked in full (its own `def` bodies are already in the
    tree); a function reached across a module boundary is walked as just that
    `FunctionDef`, and its own bare calls -- to other modules or to same-module
    helpers -- are followed in turn. `visited` bounds it against cycles.
    """
    trees: dict[Path, ast.AST] = {}
    imports: dict[Path, dict[str, tuple[Path, str]]] = {}
    defs: dict[Path, dict[str, ast.AST]] = {}

    def load(path: Path) -> None:
        if path not in trees:
            tree = ast.parse(path.read_text())
            trees[path] = tree
            imports[path] = _from_imports(tree, root)
            defs[path] = _func_defs(tree)

    called: set[str] = set()
    visited: set[tuple[Path, str | None]] = set()
    queue: list[tuple[Path, str | None]] = [(path, None) for path in entry_files]
    while queue:
        path, func_name = queue.pop()
        if (path, func_name) in visited:
            continue
        visited.add((path, func_name))
        load(path)
        node = trees[path] if func_name is None else defs[path].get(func_name)
        if node is None:
            continue
        called |= _called_attrs(node)
        for name in _bare_call_names(node):
            if name in imports[path]:
                queue.append(imports[path][name])
            elif func_name is not None and name in defs[path]:
                # A same-module helper reached from a cross-module function; the
                # whole-file entry walk already covers same-module defs.
                queue.append((path, name))
    return called


def _discover_writer_methods() -> set[str]:
    """Every Postgres repository method whose body opens a write transaction or
    calls a session mutation primitive -- i.e. every writer, by construction."""
    writers: set[str] = set()
    for path in sorted(REPOS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        for func in ast.walk(tree):
            if isinstance(func, ast.FunctionDef) and _called_attrs(func) & WRITE_PRIMITIVES:
                writers.add(func.name)
    return writers


def _ops_entry_files() -> list[Path]:
    return sorted(OPS_DIR.rglob("*.py"))


def test_writer_discovery_is_not_vacuous():
    # If discovery found nothing, the guard below would pass for the wrong reason.
    writers = _discover_writer_methods()
    assert len(writers) >= 10, f"writer discovery looks broken: {sorted(writers)}"
    # Spot-check known writers are seen.
    for known in ("begin_run", "finish_run", "fail_run", "set_checkpoint"):
        assert known in writers, f"{known} should be discovered as a writer"


def test_the_guard_descends_through_a_bare_name_call_out_of_ops():
    # The mechanism, on the REAL tree: `list_findings` and `get` are called only
    # inside `resolver._linked_evidence`, which ops reaches by a BARE import --
    # never `x.method()` in an ops file. Their presence in the reachable set is
    # proof the walk crossed the bare call into resolver.py. Reverting to a
    # direct-only walk drops them and reds this.
    reached = _reachable_called_methods(_ops_entry_files())
    direct = set()
    for path in _ops_entry_files():
        direct |= _called_attrs(ast.parse(path.read_text()))

    assert "list_findings" in reached, (
        "the guard did not descend into resolver._linked_evidence; a writer one "
        "bare call out of ops/ would be invisible"
    )
    assert "list_findings" not in direct, (
        "list_findings is called directly in ops now, so it no longer proves the "
        "transitive descent -- pick a method only reachable across the bare call"
    )


def test_the_ops_package_reaches_no_repository_writer():
    called = _reachable_called_methods(_ops_entry_files())
    writers = _discover_writer_methods()

    reached_writers = sorted(called & writers)
    assert not reached_writers, f"hyperset/ops reaches repository writer(s): {reached_writers}"
    reached_primitives = sorted(called & WRITE_PRIMITIVES)
    assert not reached_primitives, (
        f"hyperset/ops opened a write primitive directly: {reached_primitives}"
    )


def test_a_writer_reached_by_a_bare_call_out_of_ops_reds_the_guard(tmp_path):
    """The regression for the exact blindness critic found (hy-hske): a writer
    reached only through a bare-name call OUT of the ops package must red.

    A synthetic tree so the proof does not depend on injecting a writer into real
    resolver.py: an ops-like entry file bare-calls `helper()` imported from a
    sibling module whose body calls `repo.mark_missing_deleted(...)`. The old
    direct-only walk saw nothing; the transitive walk reaches the writer name.
    """
    pkg = tmp_path / "hyperset"
    (pkg).mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "ops_like.py").write_text(
        "from hyperset.reader import helper\n\n\n"
        "def view(session_factory):\n"
        "    return helper(session_factory)\n"
    )
    # Two hops on purpose: a CROSS-module bare call (ops_like -> reader.helper)
    # then a SAME-module bare call (helper -> deeper), and only `deeper` touches
    # the writer. A one-level walk stops at `helper` and misses it.
    (pkg / "reader.py").write_text(
        "def helper(session_factory):\n"
        "    return deeper(session_factory)\n\n\n"
        "def deeper(session_factory):\n"
        "    repo = object()\n"
        "    return repo.mark_missing_deleted(session_factory)\n"
    )

    reached = _reachable_called_methods([pkg / "ops_like.py"], root=tmp_path)
    assert "mark_missing_deleted" in reached, (
        "a writer reached via a bare-name call out of ops was invisible -- the "
        "transitive walk is not following the bare import"
    )

    # And direct-only inspection of the entry file alone does NOT see it, which is
    # why the transitive walk is load-bearing rather than decorative.
    direct = _called_attrs(ast.parse((pkg / "ops_like.py").read_text()))
    assert "mark_missing_deleted" not in direct
