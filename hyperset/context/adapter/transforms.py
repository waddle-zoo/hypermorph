"""The closed whitelist of context-adapter transforms (hy-qswh, epic #283).

A `context-adapter.yaml` maps a customer's own corpus shape onto v0 context by
naming a transform after `|` -- `$.urn | slug`, `$.owners[*] | prefix('team:')`.
This module IS that whitelist and nothing else: a fixed, enumerated set of pure
functions plus one lookup that refuses any name outside it.

The whitelist exists instead of an expression language because of the invariant
epic #283 rests on -- **an adapter may change the shape that carries meaning; it
may never create meaning or execute code.** So there is deliberately no `eval`,
no `exec`, no `compile`, no `getattr`-by-name, no import-by-name, and no dynamic
dispatch anywhere here. A transform is reached ONLY by exact name through
`apply_transform`, and a name outside the set is an error, never an attribute
lookup and never a silent skip -- the one-level-down form of #283's "an unmapped
key is an error".

Every transform is pure: deterministic, no I/O, a value in and a value out, the
arguments treated as literal DATA and never as code. A transform that cannot
produce a real value FAILS CLOSED (raises `TransformError`) rather than
returning a guessed or empty one, because a governed value a transform
fabricates is meaning the customer never wrote -- exactly what the whitelist
forbids.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from types import MappingProxyType

Transform = Callable[[object, tuple[str, ...]], str]


class TransformError(ValueError):
    """A transform could not produce a value from its input.

    Raised rather than returning a guessed or empty value: a governed field a
    transform invents is meaning the customer never wrote, which is the failure
    the whitelist exists to prevent.
    """


class UnknownTransform(TransformError):
    """A `| fn` naming a transform outside the closed whitelist.

    #283's "an unmapped key is an error", one level down: a mapping that names a
    transform this module does not define is refused here, never resolved by an
    attribute lookup and never quietly ignored. The message enumerates the
    whitelist so a caller can correct the mapping without reading this source.
    """

    def __init__(self, name: str, *, allowed: tuple[str, ...]) -> None:
        self.name = name
        self.allowed = allowed
        super().__init__(f"unknown transform {name!r}; the whitelist is {', '.join(allowed)}")


def _require_arity(transform: str, args: tuple[str, ...], expected: int) -> None:
    if len(args) != expected:
        raise TransformError(f"{transform} takes {expected} argument(s), got {len(args)}")


def _require_str(value: object, transform: str) -> str:
    if not isinstance(value, str):
        raise TransformError(f"{transform} needs a string value, got {type(value).__name__}")
    return value


_SLUG_SEPARATORS = re.compile(r"[^a-z0-9]+")


def slug(value: object, args: tuple[str, ...] = ()) -> str:
    """A URL/identifier-safe form: lowercase alphanumerics joined by single
    hyphens, with leading and trailing hyphens trimmed. A value that slugs to
    nothing (`'!!!'`) fails closed rather than yielding an empty identifier."""
    _require_arity("slug", args, 0)
    result = _SLUG_SEPARATORS.sub("-", _require_str(value, "slug").lower()).strip("-")
    if not result:
        raise TransformError(f"slug of {value!r} is empty; refusing to invent an identifier")
    return result


def prefix(value: object, args: tuple[str, ...] = ()) -> str:
    """`value` with a literal prefix prepended -- `prefix('team:')` on `'finance'`
    is `'team:finance'`. The argument is prepended as text; it is never evaluated,
    so `prefix('$(x)')` yields the literal `'$(x)...'`."""
    _require_arity("prefix", args, 1)
    return args[0] + _require_str(value, "prefix")


def default(value: object, args: tuple[str, ...] = ()) -> str:
    """`value`, or a literal default when `value` is absent (`None`) or blank
    (whitespace only). The default is returned verbatim as data, never
    evaluated."""
    _require_arity("default", args, 1)
    if value is None or (isinstance(value, str) and not value.strip()):
        return args[0]
    return _require_str(value, "default")


_WHITESPACE = re.compile(r"\s+")


def one_line(value: object, args: tuple[str, ...] = ()) -> str:
    """`value` with every run of whitespace -- newlines and tabs included --
    collapsed to a single space, and the ends stripped. For a title or a reason
    lifted from a multi-line source field. A value that is empty or all
    whitespace fails closed rather than yielding an empty governed field."""
    _require_arity("one_line", args, 0)
    result = _WHITESPACE.sub(" ", _require_str(value, "one_line")).strip()
    if not result:
        raise TransformError("one_line of a blank value is empty; refusing to invent a value")
    return result


# platform and name are held to safe charsets IN the pattern, not merely
# extracted and hoped over: the produced ref is delimited by ':', so a platform
# or name that itself contained ':' (or whitespace, or the URN's own ',()') would
# be an ambiguous, mis-resolvable ref -- a wrong-but-accepted governed value.
# Anything outside these charsets is not matched and fails closed. `name` allows
# a dotted/slashed dataset path (DataHub percent-encodes reserved characters, so
# a real reserved char arrives as `%3A` and passes) but never a raw ':' or
# whitespace; `platform` is a single DataHub identifier token. The charset guards
# ref AMBIGUITY (the delimiter and URN structure), not terminal-display safety --
# a rendering layer that shows a ref to a person owns any control/bidi concern.
_DATASET_URN = re.compile(
    r"\Aurn:li:dataset:\("
    r"urn:li:dataPlatform:(?P<platform>[A-Za-z0-9_-]+),"
    r"(?P<name>[^\s:,()]+),"
    r"(?P<env>[A-Za-z0-9_-]+)\)\Z"
)


def catalog_urn_to_source_ref(value: object, args: tuple[str, ...] = ()) -> str:
    """A DataHub dataset URN mapped to a governed table source ref:
    `urn:li:dataset:(urn:li:dataPlatform:postgres,analytics.public.orders,PROD)`
    becomes `table:postgres:analytics.public.orders`. The environment segment is
    dropped -- the governed ref names the table, not the deployment. Any input
    that is not a well-formed dataset URN with a safe-charset platform and name
    fails closed rather than guessing a ref."""
    _require_arity("catalog_urn_to_source_ref", args, 0)
    text = _require_str(value, "catalog_urn_to_source_ref")
    match = _DATASET_URN.match(text)
    if match is None:
        raise TransformError(
            f"{text!r} is not a DataHub dataset urn "
            "'urn:li:dataset:(urn:li:dataPlatform:<platform>,<name>,<env>)' with a "
            "safe-charset platform and name; refusing to guess a source ref"
        )
    return f"table:{match.group('platform')}:{match.group('name')}"


# The closed whitelist. Written out as a literal so the set is enumerated in one
# place a reader (and the closure test) can see whole -- there is no registration
# hook, no decorator that discovers functions, and no way to reach a sixth.
_TRANSFORMS: dict[str, Transform] = {
    "slug": slug,
    "prefix": prefix,
    "default": default,
    "one_line": one_line,
    "catalog_urn_to_source_ref": catalog_urn_to_source_ref,
}

TRANSFORM_NAMES: tuple[str, ...] = tuple(_TRANSFORMS)
"""The whitelist's names, in definition order -- the exact set a mapping may name."""

TRANSFORMS: MappingProxyType[str, Transform] = MappingProxyType(_TRANSFORMS)
"""A READ-ONLY view of the whitelist, so a caller handed this mapping cannot
register a sixth transform or replace one through it. The threat model this
closes is a DATA one -- a customer's mapping FILE, which names a transform and
cannot reach Python -- so the guarantee is against untrusted input, not against
in-process code, which is already trusted and could rebind any global."""


def apply_transform(name: str, value: object, args: tuple[str, ...] = ()) -> str:
    """Apply the whitelisted transform `name` to `value` with literal `args`.

    The ONLY dispatch: an exact-name lookup in the closed set. A name outside it
    raises `UnknownTransform` -- there is no attribute lookup, no import, and no
    fallback, so a mapping cannot reach code this module did not enumerate.
    """
    fn = _TRANSFORMS.get(name)
    if fn is None:
        raise UnknownTransform(name, allowed=TRANSFORM_NAMES)
    return fn(value, args)
