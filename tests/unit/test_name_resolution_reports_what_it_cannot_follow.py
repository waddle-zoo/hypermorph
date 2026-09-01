"""A REPORTED name resolves to `UNRESOLVED` whatever statement reported it
(hy-sp3z, third bounce).

THE DEFECT THIS ARM EXISTS FOR IS NOT A MISSED FORM. The first two bounces on
this instrument were forms that came back `UNRESOLVED` when they should have
been followed, or that were never enumerated at all; both fail loudly, and both
were found by a caller going red. This one fails GREEN with a confident WRONG
STRING:

    @_mark(_ALIAS := _Decoy)
    class _Shadow:
        MEMBER = "shadow_string"

`_ALIAS` is reported -- `bound_by` cannot follow a walrus -- and a reported name
used to bind to the STATEMENT NODE, so `_follow` saw an `ast.ClassDef` and
answered `Class(_Shadow)`. `_ALIAS.MEMBER` then read the DECORATED class's body
and returned `"shadow_string"`: not the value any runtime object reachable
through `_ALIAS` has, and not `UNRESOLVED` either. A guard built on that
compares a real value against a string nothing produces, and passes.

WHY THE ARM IS OVER STATEMENT KINDS RATHER THAN OVER THAT ONE FORM. Before the
fix the module's loudness was true by coincidence: `Try` and `Match` hide names
as raw `str` and are also unfollowable, so the reported set and the followable
set happened not to intersect, and `ClassDef` is where they do. A property that
holds because two sets happen not to intersect is a coincidence with a
docstring, and the next overlap gets written the same way this one was. So every
statement kind is either in `REPORTS`, with a statement of that kind that
reports a name, or in `NO_EXPRESSION_TO_HIDE_A_NAME_IN`, with a reason -- and
nothing this interpreter's grammar has may be in neither.

EVERY ROW ASSERTS THE ATTRIBUTE AS WELL AS THE BARE NAME. The bare name alone
does not read the wrong string: `Class(_Shadow)` is not a `str`, so a caller
that only ever compares strings survives it. The attribute is where the
coincidence turns into an answer, and it is the assertion that catches this.

Nothing here is imported or executed. Each snippet is source that gets parsed,
which is what the instrument itself does, and which is what lets a row name a
class carrying a value no import of this tree would produce.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from tests.name_resolution import UNRESOLVED, Names, bound_by

REPORTED = "_ALIAS"
MEMBER = "MEMBER"

PRELUDE = """\
class _Decoy:
    MEMBER = "decoy_string_no_runtime_object_has"


class _Error(Exception):
    MEMBER = "error_string_no_runtime_object_has"


def _mark(value):
    return value


_mapping = {}
_other = 0
_subject = None
"""

# Keyed by class NAME, not by class. `ast.TypeAlias` does not exist before 3.12,
# and a table written as `ast.TypeAlias: ...` is an AttributeError at import
# time on the interpreter CI runs -- which would take this whole file out on the
# version where it is least watched.
REPORTS: dict[str, str] = {
    "FunctionDef": "@_mark(_ALIAS := _Decoy)\ndef _f():\n    pass\n",
    "AsyncFunctionDef": "@_mark(_ALIAS := _Decoy)\nasync def _f():\n    pass\n",
    # The row this bounce is about: the reported name and the class the
    # statement DEFINES come out of one statement node.
    "ClassDef": '@_mark(_ALIAS := _Decoy)\nclass _Shadow:\n    MEMBER = "shadow_string"\n',
    "Return": "def _f():\n    return (_ALIAS := _Decoy)\n",
    "Delete": "def _f():\n    del _mapping[(_ALIAS := _Decoy)]\n",
    "Assign": "_first = (_ALIAS := _Decoy)\n",
    "AugAssign": "_other += (_ALIAS := _Decoy)\n",
    "AnnAssign": "_first: int = (_ALIAS := _Decoy)\n",
    "For": "for _each in [(_ALIAS := _Decoy)]:\n    pass\n",
    "AsyncFor": "async def _f():\n    async for _each in [(_ALIAS := _Decoy)]:\n        pass\n",
    "While": "while (_ALIAS := _Decoy):\n    break\n",
    "If": "if (_ALIAS := _Decoy):\n    pass\n",
    "With": "with _mark((_ALIAS := _Decoy)):\n    pass\n",
    "AsyncWith": "async def _f():\n    async with _mark((_ALIAS := _Decoy)):\n        pass\n",
    # Raw-`str` carriers: the AST keeps the bound name as a string with no `Name`
    # node anywhere, which is why `_bound_here` knows them by kind.
    "Match": "match _subject:\n    case _ALIAS:\n        pass\n",
    "Try": "try:\n    pass\nexcept _Error as _ALIAS:\n    pass\n",
    "TryStar": "try:\n    pass\nexcept* _Error as _ALIAS:\n    pass\n",
    "Raise": "raise _Error((_ALIAS := _Decoy))\n",
    "Assert": "assert (_ALIAS := _Decoy)\n",
    "Expr": "_mark((_ALIAS := _Decoy))\n",
    "TypeAlias": "type _ALIAS = _Decoy\n",
}

NO_EXPRESSION_TO_HIDE_A_NAME_IN: dict[str, str] = {
    "Pass": "no children at all",
    "Break": "no children at all",
    "Continue": "no children at all",
    "Global": "names are raw `str` and it binds nothing new to follow",
    "Nonlocal": "names are raw `str` and it binds nothing new to follow",
    "Import": "carries aliases and no expression; every name it binds is followable",
    "ImportFrom": "carries aliases and no expression; every name it binds is followable",
}


def statement_kinds() -> dict[str, type[ast.stmt]]:
    """The statement kinds THIS interpreter's grammar has.

    Read off `ast`'s own members rather than `ast.stmt.__subclasses__()`, which
    is a runtime-global registry: anything imported into the session that
    subclasses `ast.stmt` appears there, so that expression would not say what
    the sentence around it claims. The two agree today; only one of them keeps
    agreeing.
    """
    return {
        name: member
        for name, member in vars(ast).items()
        if isinstance(member, type) and issubclass(member, ast.stmt) and member is not ast.stmt
    }


RUNNABLE = sorted(set(REPORTS) & set(statement_kinds()))


@pytest.fixture(scope="module")
def package(tmp_path_factory) -> Path:
    """One file per runnable statement kind, in a package `Names` resolves in.

    On disk because `Names` reads source, and only the kinds this interpreter
    HAS: `type _ALIAS = _Decoy` is a `SyntaxError` before 3.12, so writing every
    row would fail at parse rather than at the assertion.
    """
    root = tmp_path_factory.mktemp("corpus") / "corpus"
    root.mkdir()
    (root / "__init__.py").write_text("")
    for name in RUNNABLE:
        (root / f"{name.lower()}.py").write_text(f"{PRELUDE}\n\n{REPORTS[name]}")
    return root


def _statement(package: Path, name: str) -> tuple[Names, Path, ast.stmt]:
    """The one statement of that kind in its snippet which REPORTS `_ALIAS`.

    Selected by what it reports rather than by position, and that selection is
    the row's vacuity check as well as its lookup. A snippet that stopped
    reporting -- an edit to `_bound_here`, a Python that parses the form
    differently -- would otherwise resolve `_ALIAS` to `UNRESOLVED` because
    nothing bound it at all, and the row would pass while testing nothing. The
    prelude puts statements of several of these kinds in every file, so "the
    only one" has to mean this rather than "the only one of its kind".
    """
    names = Names(package)
    path = package / f"{name.lower()}.py"
    kind = statement_kinds()[name]
    found = [
        node
        for node in ast.walk(names.tree(path))
        if isinstance(node, kind) and REPORTED in bound_by(node)
    ]
    assert len(found) == 1, (
        f"{len(found)} statements of kind {name} report {REPORTED} in this snippet; exactly one "
        "is what makes the assertions below assertions about the form they name"
    )
    return names, path, found[0]


def test_no_statement_kind_this_python_has_is_unclassified():
    """The guard half of the classification, split from the reporting half.

    Those two directions have opposite risk profiles and one equality cannot
    serve both honestly (mayor, round 4). UNCLASSIFIED is the whole guard: a
    statement kind the running interpreter has that neither table names, which
    is what a future Python adding a kind looks like and what has to fail.

    STALE -- a kind classified here that this interpreter does not have -- is
    not a defect at all. On 3.11 it is exactly `TypeAlias`, and asserting it
    empty would redden this arm in CI for a version fact, on this PR and every
    unrelated one after it. The cheapest repair to a red like that is to weaken
    the arm, so the arm does not ask for it; it reports it with the version.
    """
    kinds = set(statement_kinds())
    classified = set(REPORTS) | set(NO_EXPRESSION_TO_HIDE_A_NAME_IN)
    stale = sorted(classified - kinds)

    assert set(REPORTS) & set(NO_EXPRESSION_TO_HIDE_A_NAME_IN) == set()
    assert sorted(kinds - classified) == [], (
        f"statement kinds this Python has that neither table classifies, on "
        f"{sys.version_info.major}.{sys.version_info.minor}: {sorted(kinds - classified)}; "
        "a reported name out of one of them is unmeasured here"
    )
    if stale:  # pragma: no cover - only on an interpreter older than the table
        print(
            f"classified but absent from ast on "
            f"{sys.version_info.major}.{sys.version_info.minor}: {stale}"
        )


@pytest.mark.parametrize("name", RUNNABLE)
def test_a_reported_name_resolves_to_UNRESOLVED_whatever_statement_reported_it(name, package):
    """`_ALIAS`, and `_ALIAS.MEMBER`, out of a statement of every kind.

    Resolved against the statement's own bindings rather than the module's, so a
    kind that cannot appear at module level -- `Return`, `AsyncFor` -- is asked
    the same question as one that can. That is also how the caller in
    `tests/unit/evals` uses this: it walks a function, takes what `bound_by`
    hands back for a name, and resolves that.

    ONE ASSERTION OVER BOTH FORMS, not two assertions in a row. The bare name is
    the stricter of the two and fails first, so a pair of `assert` statements
    would report `Class(_Shadow)` and never evaluate the attribute -- and the
    attribute is where the failure becomes a plausible string. At d2c5f25 the
    two answers were `Class(_Shadow)` and `'shadow_string'`; only the second
    tells the reader why the first matters.
    """
    names, path, statement = _statement(package, name)
    bound = bound_by(statement)

    denoted = {
        form: names.resolve(ast.parse(form, mode="eval").body, path, bound)
        for form in (REPORTED, f"{REPORTED}.{MEMBER}")
    }

    assert {form: value for form, value in denoted.items() if value is not UNRESOLVED} == {}, (
        f"out of a {name}, a name this module reported denotes {denoted}; it cannot follow what "
        "bound the name, so the only honest answer is UNRESOLVED. A string here is worse than a "
        "wrong one: no object reachable through that name has it, and a guard comparing against "
        "it finds no difference to report"
    )
