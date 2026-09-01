"""What an expression DENOTES, for guards that must not be fooled by spelling.

Three guards in this repository have now shipped with the same defect, each
found by the next reader (hy-1pqa, hy-sp3z, and hy-sp3z's third instance):

    a quoted literal scan          blind to `X == THE_CONSTANT`
    a `Code.MEMBER` attribute walk blind to `_ALIAS = Code.MEMBER; code = _ALIAS`
    an argument walk               blind to `code = code`, assigned above

Every one is keyed on how the value is SPELLED where it is used, and every new
spelling is invisible to the scan that preceded it. The fourth spelling is
already writable, so the answer cannot be a fourth pattern. It has to be
resolution: follow the name to the thing, and compare the things.

WHAT THIS RESOLVES, STATICALLY AND WITHOUT IMPORTING ANYTHING. A literal; a name
bound at module level; a name imported from another module in the package, by
its own name or `as` an alias; an attribute of an imported module; a member of
an enum or any class whose body assigns literals, reached through any of the
above; and any chain of those. `StrEnum` members and plain string constants come
back as the same thing -- the string -- which is what lets one instrument serve
a guard about proposal outcomes and a guard about scorer branch codes.

Not importing is deliberate. `hyperset.db.migrations.env` cannot be imported
outside Alembic, so an importing resolver would carry a skipped module, and a
module a scan silently skips is the shape of defect this file exists to end.

WHAT IT CANNOT RESOLVE IS NAMED, NOT SWALLOWED. A computed string -- an f-string,
a `.join`, a `.format`, a value read from a mapping at run time -- comes back as
`UNRESOLVED` rather than as "no match". A caller that must be exhaustive asserts
on `UNRESOLVED` and so goes red on the first form this file cannot follow,
instead of going green the way its three predecessors did. That is the whole
difference between a scan and a resolver: a scan reports the absence of the
spellings it knows, and cannot tell that apart from absence.

AND THAT APPLIES TO THE BINDING, NOT ONLY TO THE EXPRESSION. The first version of
this file resolved expressions exhaustively and then chose which expressions it
ever saw with an `elif` chain over `Assign`, `AnnAssign`, `ClassDef` and the two
imports, with no terminal else. So `code, explanation = ...` and
`(code := ...)` -- and `for`, `with`, `except` and `+=` -- bound nothing at all,
the guard above never saw them, and its `UNRESOLVED` arm had nothing to fire on.
The defect this file exists to end, one layer up from where it was fixed.

`bound_by` is therefore the enumerator, and it takes the union of a report over
EVERY statement with the few bindings it can follow, rather than dispatching to
one or the other. Whatever it cannot follow binds to a `_Reported` marker, which
resolves to `UNRESOLVED` and unparses to the exact form for the message.

THAT MARKER IS THE THIRD BOUNCE. A reported name used to bind to the statement
node itself, so `@_marker(_ALIAS := _Decoy)` over a `class _Shadow` reported
`_ALIAS` and handed `_follow` an `ast.ClassDef` -- which is a followable kind.
`_ALIAS` resolved to `_Shadow` and `_ALIAS.MEMBER` came back as a string out of
`_Shadow`'s body: not `UNRESOLVED`, and not a value anything reachable through
`_ALIAS` has. Unlike the first two bounces this one fails toward GREEN, because
a guard comparing against a confident wrong string finds no difference to
report. What made the module loud everywhere else was a coincidence: `Try` and
`Match` hide names as raw `str` AND are unfollowable, so the two sets happened
not to intersect, and `ClassDef` is where they do. Reported names are no longer
AST nodes at all, so no following case -- present or future -- can claim one.

TWO SENTENCES OF THIS DOCSTRING WERE THEMSELVES A BOUNCE, so read the rest of it
as a claim that has been wrong before. It said a walrus was covered and that an
unwritten spelling would be red without another line here; both were false at
e1dfb638, because the enumerator returned before reaching the very else those
sentences described. There is no list of covered forms in this file any more.
There is a mechanism, stated on `bound_by`, and a mechanism can be checked
against the code -- which is the only kind of coverage claim this module has any
business making.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

UNRESOLVED = "<unresolved>"
"""Deliberately not `None`. A caller that forgets to handle it compares it
against a real value and gets False, rather than silently reading it as "this
expression names nothing"."""


def _bound_here(node) -> list[str]:
    """Names one statement binds ITSELF, not counting the statements inside it.

    Stopping at a nested `stmt` is what keeps this usable as a terminal else. An
    `if` whose body assigns `code` binds nothing -- the assignment inside it is
    its own statement and a caller walking the tree reaches it separately -- so a
    plain if/elif chain stays followable. An `if` whose TEST walruses the name
    binds it, because a `NamedExpr` is an expression and never a statement.

    The raw-`str` cases below are the same plumbing as `ExceptHandler`: the AST
    keeps the bound name as a string with no `Name` node anywhere, so a
    Store-context walk cannot see it at all. Adding them makes a form LOUD, not
    quiet, which is why they are allowed here and forbidden in `bound_by`.

    `def` and `async def` are here rather than in `bound_by`'s followable cases,
    where they used to be, and that placement is the correction to a claim this
    bead made and got wrong (mayor, round 4). A `def` name was FOLLOWABLE, which
    made "the fix adds no following case" false as written. It was also inert:
    `resolve` has no arm for a `FunctionDef`, so following one reached the
    terminal else and answered `UNRESOLVED` -- the same answer reporting it
    gives, by a route that did not say so. Inert is not the same as safe, since
    an inert entry sits in the one dict this module says nothing may be added
    to, next to the `ClassDef` entry that is not inert and that produced the
    third bounce.
    """
    found: list[str] = []
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        found.append(node.id)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        found.append(node.name)
    elif isinstance(node, ast.ExceptHandler) and node.name:
        found.append(node.name)
    elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
        found.append(node.name)
    elif isinstance(node, ast.MatchMapping) and node.rest:
        found.append(node.rest)

    # The rule for both of these is EVALUATION SCOPE, not a list of node types.
    # Descend where the sub-expression is evaluated in the scope this statement
    # lives in, and stop where it is evaluated in a scope of its own.
    if isinstance(node, ast.Lambda):
        # Defaults are evaluated at definition time in the enclosing scope, so a
        # walrus there binds here. The body is evaluated at call time in the
        # lambda's own scope and binds nothing that escapes.
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                found.extend(_bound_here(default))
        return found
    if isinstance(node, ast.comprehension):
        # Same test, opposite answer per PEP 572: the iteration variable lives
        # in the comprehension's own scope, while a walrus in a filter binds in
        # the enclosing one and must be followed.
        found.extend(_bound_here(node.iter))
        for condition in node.ifs:
            found.extend(_bound_here(condition))
        return found

    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.stmt):
            continue
        found.extend(_bound_here(child))
    return found


def bound_by(statement) -> dict[str, ast.AST | _Imported | _Reported]:
    """Every name one statement binds -> the node to follow for its value.

    THE MECHANISM, WHICH IS WHAT TO CHECK. `_bound_here` runs on EVERY statement,
    unconditionally, and the followable cases are unioned OVER its result.
    So the question "is this form covered" has one answer for all forms and it is
    not a list: a name is reported unless a followable case claims it by name, and
    a name is reported at all unless it is bound in a scope other than this
    statement's. Read those two sentences against the code below; a list of forms
    cannot be read against anything, which is why there is not one here.

    THAT UNION IS THE SECOND BOUNCE AND IT USED TO BE AN ELSE. Written as four
    early returns, a statement matching one of them never reached `_bound_here`
    at all, so it was assumed to bind only its own target. `_hint = (code := X)`
    bound `_hint`. So did a walrus in a comprehension filter, in an f-string, in
    a lambda default and in a decorator; `match` captures bound nothing. Seven
    forms escaped the terminal else by never being tested against it -- the first
    bounce's defect with the escape hatch moved one line up. A statement binds
    what its target says AND whatever its expressions bind; a followable case
    answers only the first half, so it may narrow the ANSWER and must not narrow
    the QUESTION.

    THE PROSE HERE WAS PART OF THAT BOUNCE. This docstring previously asserted
    that a walrus was covered and that an unwritten spelling would be red without
    another line. Both were false as written, in the file whose whole purpose is
    to end guards that look closed. A claim about coverage in this module is a
    claim about the two sentences above and nothing else.

    THE INVARIANT IS NOT "no new isinstance". Ask what a new branch makes a form
    DO. A branch that causes a form to be FOLLOWED makes it QUIET -- that is a
    pattern, and it is what this module refuses. A branch that causes a form to
    be REPORTED makes it LOUD -- that is plumbing, and it is what makes the
    terminal else reachable.

    AND LOUD IS NOW STRUCTURAL RATHER THAN CIRCUMSTANTIAL. The report binds each
    name to a `_Reported`, not to the statement, so the reported half of this
    dict holds nothing any following case dispatches on -- they all test AST node
    kinds. Before that, a name reported out of a statement whose KIND happened to
    be followable was followed as if the statement had bound it deliberately, and
    `ClassDef` answered with the decorated class. The loudness of `except E as
    code` did not depend on `Try` being unfollowable, but it was not distinguish-
    able from a module where it did, which is the same thing as not holding.
    `tests/unit/test_name_resolution_reports_what_it_cannot_follow.py` states it
    over every statement kind this interpreter has.

    So: NOTHING MAY BE ADDED TO THE FOLLOWING CASES BELOW for a binding form.
    `_bound_here` MAY grow when the AST hides a name somewhere new, which is what
    its `ExceptHandler` and `match` branches are -- `except E as code` keeps the
    name as a raw str with no `Name` node anywhere, so those branches do not
    teach this module a spelling, they teach it that a name exists at all, and
    they route straight into the else and come back `UNRESOLVED`.

    That rule was BROKEN BY THIS BEAD before it was stated: `FunctionDef` and
    `AsyncFunctionDef` were added to the followable cases in an earlier round,
    and a report of this work said the fix added no following case. It did add
    two. They are in `_bound_here` now, which is where a raw-`str` name belongs
    and where they answer `UNRESOLVED` by saying so rather than by falling
    through. Moving them changed no test, which is the measurement, not the
    justification: the reason they are gone is that the dict this sentence
    protects should hold only cases that follow a name to a VALUE.

    Tuple unpacking could be followed positionally instead of reported. It is
    not, because reporting is what makes the unwritten form safe and following it
    would only make one more form quiet.
    """
    followable: dict[str, ast.AST | _Imported | _Reported] = {}
    if isinstance(statement, ast.Assign) and all(
        isinstance(target, ast.Name) for target in statement.targets
    ):
        followable = {target.id: statement.value for target in statement.targets}
    elif isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
        if statement.value is not None:
            followable = {statement.target.id: statement.value}
    elif isinstance(statement, ast.ClassDef):
        followable = {statement.name: statement}
    elif isinstance(statement, (ast.Import, ast.ImportFrom)):
        followable = {
            alias.asname or alias.name.split(".")[0]: _Imported(statement, alias)
            for alias in statement.names
        }
    reported = {name: _Reported(statement) for name in _bound_here(statement)}
    return {**reported, **followable}


@dataclass(frozen=True)
class Module:
    """A resolved module, so `discovery.NO_GOVERNING_DOMAIN` can continue."""

    path: Path


@dataclass(frozen=True)
class Class:
    """A resolved class body, so `Code.NEVER_CALLED_VALIDATE` can continue.

    Any class whose body assigns literals, not just an enum: the guard should
    not care whether the author reached for `StrEnum` or a bare namespace, and
    caring would be one more rule about spelling.
    """

    path: Path
    name: str


class Names:
    """Resolves expressions against a package's source, one parse per file."""

    def __init__(self, package: Path) -> None:
        self.package = package
        self.root = package.parent
        self._trees: dict[Path, ast.Module] = {}
        self._bindings: dict[Path, dict[str, ast.AST | _Imported | _Reported]] = {}

    def tree(self, path: Path) -> ast.Module:
        if path not in self._trees:
            self._trees[path] = ast.parse(path.read_text())
        return self._trees[path]

    def module_path(self, dotted: str) -> Path | None:
        """`hyperset.bundle.discovery` -> its file, or None if it is not ours.

        Third-party and stdlib modules resolve to nothing on purpose: this
        resolves the package's own vocabulary, and following `re` or `pathlib`
        would buy nothing and cost a filesystem walk per name.
        """
        base = self.root / Path(*dotted.split("."))
        for candidate in (base.with_suffix(".py"), base / "__init__.py"):
            if candidate.is_file():
                return candidate
        return None

    def bindings(self, path: Path) -> dict[str, ast.AST | _Imported | _Reported]:
        """Module-level name -> the node that binds it, via `bound_by`.

        The module's own `body`, so a name bound inside a module-level `if` or
        `try` is not reachable. That is a real limit and it is stated rather than
        left to be discovered: it never reads as a resolved value, only as
        `UNRESOLVED`, so it fails in the safe direction.
        """
        if path not in self._bindings:
            found: dict[str, ast.AST | _Imported | _Reported] = {}
            for node in self.tree(path).body:
                found.update(bound_by(node))
            self._bindings[path] = found
        return self._bindings[path]

    def resolve(self, expression, path: Path, local=None, seen=frozenset()):
        """The string, `Module`, or `Class` an expression denotes.

        `local` is the enclosing function's own assignments, so a name bound in
        a branch above the use site resolves the same way a module-level one
        does -- that indirection is the third of the three defects above.
        """
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            return expression.value
        if isinstance(expression, ast.Name):
            key = (path, expression.id)
            if key in seen:
                return UNRESOLVED
            bound = (local or {}).get(expression.id) or self.bindings(path).get(expression.id)
            if bound is None:
                return UNRESOLVED
            return self._follow(bound, path, local, seen | {key})
        if isinstance(expression, ast.Attribute):
            base = self.resolve(expression.value, path, local, seen)
            if isinstance(base, Module):
                return self.resolve(
                    ast.Name(id=expression.attr, ctx=ast.Load()), base.path, None, seen
                )
            if isinstance(base, Class):
                member = self._class_members(base).get(expression.attr)
                return UNRESOLVED if member is None else self.resolve(member, base.path, None, seen)
        return UNRESOLVED

    def _follow(self, bound, path: Path, local, seen):
        # First, and stated rather than left to the terminal else: a name this
        # module could not follow does not become followable because the
        # statement that reported it is of a kind that would have been. The
        # marker is not an `ast.AST`, so none of the cases below can match it
        # anyway -- this arm is here so that a case added below cannot, and so
        # that the guarantee does not rest on `resolve` being reached.
        if isinstance(bound, _Reported):
            return UNRESOLVED
        if isinstance(bound, ast.ClassDef):
            return Class(path=path, name=bound.name)
        if isinstance(bound, _Imported):
            return self._import(bound, seen)
        return self.resolve(bound, path, local, seen)

    def _import(self, imported: _Imported, seen):
        if isinstance(imported.node, ast.Import):
            target = self.module_path(imported.alias.name)
            return UNRESOLVED if target is None else Module(path=target)
        if imported.node.level:
            return UNRESOLVED
        dotted = imported.node.module or ""
        # `from hyperset.bundle import discovery` binds a MODULE, and nothing in
        # `hyperset/bundle/__init__.py` binds that name -- so the submodule is
        # tried first or every `package.module.CONSTANT` reference resolves to
        # nothing and reads as absence.
        submodule = self.module_path(f"{dotted}.{imported.alias.name}")
        if submodule is not None:
            return Module(path=submodule)
        target = self.module_path(dotted)
        if target is None:
            return UNRESOLVED
        return self.resolve(ast.Name(id=imported.alias.name, ctx=ast.Load()), target, None, seen)

    def _class_members(self, klass: Class) -> dict[str, ast.expr]:
        for node in ast.walk(self.tree(klass.path)):
            if isinstance(node, ast.ClassDef) and node.name == klass.name:
                return {
                    target.id: statement.value
                    for statement in node.body
                    if isinstance(statement, ast.Assign)
                    for target in statement.targets
                    if isinstance(target, ast.Name)
                }
        return {}

    def strings_named_in(self, path: Path, wanted: set[str]) -> dict[str, list[str]]:
        """Every expression in one file that DENOTES one of `wanted`.

        Keyed by the wanted string and valued by how each hit was written, so a
        failure can quote the form it found rather than only the file. Literals
        and resolvable references both count and are not distinguished, which is
        the point: `"no_governing_domain"` and `NO_GOVERNING_DOMAIN` and
        `discovery.NO_GOVERNING_DOMAIN` are one fact about the tree.
        """
        found: dict[str, list[str]] = {}
        for node in ast.walk(self.tree(path)):
            if not isinstance(node, (ast.Constant, ast.Name, ast.Attribute)):
                continue
            value = self.resolve(node, path)
            if isinstance(value, str) and value in wanted:
                found.setdefault(value, []).append(ast.unparse(node))
        return {value: sorted(set(forms)) for value, forms in found.items()}


def unparse(expression) -> str:
    """The source form of anything `bound_by` hands back, for a message.

    A caller that reports what it could not resolve is holding whatever
    `bound_by` bound the name to, and two of those three things are not AST
    nodes: `ast.unparse` raises on them. It raised on `_Imported` before this
    marker existed too, which nothing had reached only because no caller had yet
    reported a name bound by an import.
    """
    if isinstance(expression, _Reported):
        return ast.unparse(expression.statement)
    if isinstance(expression, _Imported):
        return ast.unparse(expression.node)
    return ast.unparse(expression)


@dataclass(frozen=True)
class _Imported:
    node: ast.Import | ast.ImportFrom
    alias: ast.alias


@dataclass(frozen=True)
class _Reported:
    """A name this module saw bound and cannot follow.

    Carries the statement for the message and nothing else. Deliberately not the
    statement itself: `_follow` dispatches on AST node kinds, and a reported name
    that IS an AST node is one `isinstance` away from being followed as though
    the statement had bound it on purpose -- which is what `ClassDef` did.
    """

    statement: ast.stmt
