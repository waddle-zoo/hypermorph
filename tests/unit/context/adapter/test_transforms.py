"""The closed context-adapter transform whitelist (hy-qswh, epic #283).

Two things are proven here, and the second is the point. First, each transform
does what a mapping needs and FAILS CLOSED rather than inventing a value.
Second -- the invariant #283 rests on -- the set is CLOSED and reaches no NAMED
execution primitive: a mapping file can name only these five functions, reaches
them by exact name and nothing else, and cannot through them run arbitrary code
or fabricate a governed value. The no-named-primitive half is asserted
structurally (an AST walk of the module), not just behaviourally, because "there
is no eval" is a property of the source a test that only calls the functions
cannot see. The complementary half -- that a mapping cannot supply an
object-traversal gadget that needs no named primitive -- is the DATA boundary
itself: a mapping names a transform, it never carries a Python expression, so
there is nothing for such a gadget to ride in on.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from hyperset.context.adapter import transforms as T
from hyperset.context.adapter.transforms import (
    TRANSFORM_NAMES,
    TRANSFORMS,
    TransformError,
    UnknownTransform,
    apply_transform,
)

MODULE = Path(T.__file__)

# The whitelist, typed by hand so ADDING or RENAMING a transform is a deliberate
# edit that reds this test -- the set cannot grow silently, which is what "closed"
# means for a security boundary.
EXPECTED = ("slug", "prefix", "default", "one_line", "catalog_urn_to_source_ref")


# --- the set is exactly these five, reachable only by exact name ---


def test_the_whitelist_is_exactly_the_declared_five():
    assert TRANSFORM_NAMES == EXPECTED
    assert set(TRANSFORMS) == set(EXPECTED)


def test_a_name_outside_the_whitelist_is_refused_naming_the_whitelist():
    with pytest.raises(UnknownTransform) as excinfo:
        apply_transform("eval", "anything")
    assert excinfo.value.name == "eval"
    assert excinfo.value.allowed == EXPECTED
    for name in EXPECTED:
        assert name in str(excinfo.value)


@pytest.mark.parametrize(
    "attribute",
    ["_require_str", "_require_arity", "TransformError", "re", "apply_transform", "TRANSFORMS"],
)
def test_dispatch_is_not_attribute_lookup(attribute):
    """A real module-level name is NOT a transform. If dispatch were
    `getattr(module, name)` these would resolve to helpers, the error classes, or
    an imported module -- the exact escape a whitelist exists to close. They must
    all be `UnknownTransform`, proving the lookup is the fixed set and not the
    module namespace."""
    with pytest.raises(UnknownTransform):
        apply_transform(attribute, "x")


def test_the_whitelist_view_is_read_only():
    """A caller cannot register a sixth transform or replace one at runtime."""
    with pytest.raises(TypeError):
        TRANSFORMS["slug"] = lambda value, args: "x"  # type: ignore[index]
    with pytest.raises(TypeError):
        del TRANSFORMS["slug"]  # type: ignore[misc]


# --- each transform: what it does, and where it fails closed ---


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Recognized Revenue", "recognized-revenue"),
        ("  Trailing / Slashes // ", "trailing-slashes"),
        ("already-a-slug", "already-a-slug"),
        ("MixedCASE_123", "mixedcase-123"),
    ],
)
def test_slug_normalizes(value, expected):
    assert apply_transform("slug", value) == expected


def test_slug_of_nothing_fails_closed():
    with pytest.raises(TransformError, match="empty"):
        apply_transform("slug", "!!!")


def test_prefix_prepends_literal_text():
    assert apply_transform("prefix", "finance", ("team:",)) == "team:finance"


def test_default_fills_only_an_absent_or_blank_value():
    assert apply_transform("default", "primary", ("supporting",)) == "primary"
    assert apply_transform("default", "", ("supporting",)) == "supporting"
    assert apply_transform("default", "   ", ("supporting",)) == "supporting"
    assert apply_transform("default", None, ("supporting",)) == "supporting"


def test_one_line_collapses_every_run_of_whitespace():
    assert apply_transform("one_line", "a\n  b\tc\r\nd") == "a b c d"
    assert apply_transform("one_line", "  edge  ") == "edge"


@pytest.mark.parametrize("value", ["", "   ", "\n\t \r\n"])
def test_one_line_of_a_blank_value_fails_closed(value):
    """Like `slug`, it refuses to yield an empty governed field rather than
    collapsing whitespace to nothing and returning it."""
    with pytest.raises(TransformError, match="blank"):
        apply_transform("one_line", value)


@pytest.mark.parametrize(
    "urn,ref",
    [
        (
            "urn:li:dataset:(urn:li:dataPlatform:postgres,analytics.public.finance_orders_daily,PROD)",
            "table:postgres:analytics.public.finance_orders_daily",
        ),
        (
            "urn:li:dataset:(urn:li:dataPlatform:superset,ae48881d-334f-54a7-94e8-1ffcc73866e2,PROD)",
            "table:superset:ae48881d-334f-54a7-94e8-1ffcc73866e2",
        ),
    ],
)
def test_catalog_urn_maps_a_dataset_to_a_governed_table_ref(urn, ref):
    assert apply_transform("catalog_urn_to_source_ref", urn) == ref


@pytest.mark.parametrize(
    "value",
    [
        "not-a-urn",
        "urn:li:chart:(urn:li:dataPlatform:superset,c1,PROD)",  # dataset only
        "urn:li:dataset:(a,b,PROD)",  # inner is not a dataPlatform urn
        "table:postgres:already.a.ref",  # a source ref is not a urn
        "urn:li:dataset:(urn:li:dataPlatform:,name,PROD)",  # empty platform
        # Injection/ambiguity: a ':' or whitespace in platform or name would make
        # the produced 'table:platform:name' ref ambiguous or mis-resolvable, so
        # the charset in the pattern rejects it rather than accept a wrong ref.
        "urn:li:dataset:(urn:li:dataPlatform:postgres,a:b:c,PROD)",  # ':' in name
        "urn:li:dataset:(urn:li:dataPlatform:postgres,pub lic.orders,PROD)",  # ws in name
        "urn:li:dataset:(urn:li:dataPlatform:pg:evil,name,PROD)",  # ':' in platform
        "urn:li:dataset:(urn:li:dataPlatform:postgres,a)extra,PROD)",  # ')' in name
    ],
)
def test_catalog_urn_fails_closed_rather_than_guessing_a_ref(value):
    with pytest.raises(TransformError, match="refusing to guess"):
        apply_transform("catalog_urn_to_source_ref", value)


# --- adversarial: arguments are DATA, and wrong shapes fail closed ---


def test_arguments_are_literal_data_never_evaluated():
    """The whole reason for a whitelist over an expression language: a code-shaped
    argument is prepended or returned as text, never executed."""
    assert apply_transform("prefix", "x", ("__import__('os').system('id');",)) == (
        "__import__('os').system('id');x"
    )
    assert apply_transform("default", "", ("`rm -rf /`",)) == "`rm -rf /`"


@pytest.mark.parametrize(
    "name,args",
    [
        ("slug", ("extra",)),
        ("one_line", ("extra",)),
        ("catalog_urn_to_source_ref", ("extra",)),
        ("prefix", ()),
        ("prefix", ("a", "b")),
        ("default", ()),
    ],
)
def test_wrong_arity_fails_closed(name, args):
    with pytest.raises(TransformError, match="argument"):
        apply_transform(name, "value", args)


@pytest.mark.parametrize("name", ["slug", "prefix", "one_line", "catalog_urn_to_source_ref"])
def test_a_non_string_value_fails_closed(name):
    args = ("team:",) if name == "prefix" else ()
    with pytest.raises(TransformError, match="string"):
        apply_transform(name, 123, args)


# --- the structural proof: the module executes no code and imports nothing dynamically ---

# Primitives that would let a "pure" transform reach arbitrary code or the module
# namespace. Their ABSENCE from the source is the closure guarantee; a behavioural
# test cannot prove a path is missing, only that one input did not hit it.
#
# The set is the exec/introspection builtins as a class: the four that run or
# produce code (`eval`, `exec`, `compile`, `__import__`), `breakpoint` (which
# hands control to whatever `PYTHONBREAKPOINT` names -- a genuine, env-directable
# execution primitive), and the namespace/attribute reach (`getattr`, `setattr`,
# `delattr`, `globals`, `locals`, `vars`) through which code is reached
# indirectly. `__builtins__`/`importlib` are added to FORBIDDEN_NAMES below.
FORBIDDEN_CALLS = frozenset(
    {
        "eval",
        "exec",
        "compile",
        "__import__",
        "breakpoint",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
    }
)

# Names whose mere PRESENCE (as a Load anywhere -- a call, an alias `_g =
# getattr`, a subscript `__builtins__['eval']`) is a code-reach, so the guard
# below flags any reference and not only a call site.
FORBIDDEN_NAMES = FORBIDDEN_CALLS | {"__builtins__", "importlib"}

# The only modules this whitelist may import, at the top level only. A dynamic
# `importlib`/`__import__` or a function-body import is exactly the code path the
# adapter must not have.
ALLOWED_IMPORTS = frozenset({"__future__", "re", "collections.abc", "types"})


def _tree() -> ast.Module:
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def _execution_offenders(source: str) -> set[str]:
    """Every code-reach the guard finds in `source`.

    Factored out of the module test so the SAME walk both clears the real
    `transforms.py` and is shown to RED on planted primitives (a guard nobody has
    watched go red is a guard nobody knows fires)."""
    offenders: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        # A reference to a forbidden name in ANY position and context -- called,
        # aliased to a local (`_g = getattr`), or subscripted
        # (`__builtins__['eval']`) -- is a code-reach, not only a direct call,
        # which a call-site-only check misses. Flagging Store/Del too is
        # deliberately stricter: binding a local named `getattr` is itself suspect.
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            offenders.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            # `re.compile` is the regex compiler, not the `compile` builtin that
            # produces a code object; anything else named `compile`, and every
            # other forbidden attr, is flagged.
            if (
                node.attr == "compile"
                and isinstance(node.value, ast.Name)
                and node.value.id == "re"
            ):
                continue
            offenders.add(node.attr)
        elif isinstance(node, ast.Call) and not isinstance(node.func, ast.Name | ast.Attribute):
            # An indirectly-resolved call target -- a subscript, a chained call, a
            # lambda -- is dynamic dispatch the name checks cannot see through.
            offenders.add(type(node.func).__name__)
    return offenders


def test_the_module_calls_no_code_execution_primitive():
    offenders = _execution_offenders(MODULE.read_text(encoding="utf-8"))
    assert not offenders, f"transforms.py reaches a code-execution primitive: {sorted(offenders)}"


@pytest.mark.parametrize(
    "planted,expected",
    [
        ("breakpoint()", "breakpoint"),  # env-directable via PYTHONBREAKPOINT
        ("eval('1')", "eval"),
        ("exec('x=1')", "exec"),
        ("compile('1', '<s>', 'eval')", "compile"),
        ("__import__('os')", "__import__"),
        ("getattr(o, 'x')", "getattr"),
        ("_g = getattr", "getattr"),  # aliased, never called at this site
        ("__builtins__['eval']('1')", "__builtins__"),  # subscript reach
        ("d[k]()", "Subscript"),  # indirectly-resolved call target
    ],
)
def test_the_guard_reds_on_a_planted_primitive(planted, expected):
    """The guard has teeth: a source carrying any exec/introspection primitive --
    called, aliased, subscripted, or indirectly dispatched -- is flagged. Without
    this the `no offenders` assertion above could pass because the walk sees
    nothing, not because the module is clean."""
    assert expected in _execution_offenders(f"x = {planted}\n")


def test_re_compile_alone_is_not_flagged():
    """The one exec-named call the module legitimately makes -- `re.compile` for a
    static pattern -- must not red the guard, or the real-module test above would
    be passing only by luck of wording."""
    assert _execution_offenders("import re\nP = re.compile('a')\n") == set()


def test_every_import_is_a_top_level_static_import_of_an_allowed_module():
    tree = _tree()
    top_level = set(tree.body)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in ALLOWED_IMPORTS, f"unexpected import {alias.name!r}"
                assert node in top_level, f"import {alias.name!r} is not at module top level"
        elif isinstance(node, ast.ImportFrom):
            assert node.module in ALLOWED_IMPORTS, f"unexpected import from {node.module!r}"
            assert node in top_level, f"import from {node.module!r} is not at module top level"


def test_dispatch_is_a_fixed_mapping_not_a_namespace_lookup():
    """`apply_transform` must dispatch through the literal `_TRANSFORMS` mapping,
    not `getattr` on the module or a globals() lookup. The forbidden-call test
    already bans `getattr`/`globals`; this pins the intended mechanism so a later
    refactor to a namespace lookup is caught as a design change, not just if it
    happens to name a banned builtin."""
    source = MODULE.read_text(encoding="utf-8")
    assert "_TRANSFORMS.get(name)" in source
