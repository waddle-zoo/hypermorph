"""Parse and VALIDATE a context-adapter.yaml -- never apply it (epic #283, hy-9keh).

A customer with an existing semantic layer should govern the corpus they already
have, in the shape it is already in, rather than rewrite it into v0's
manifest.yaml / context.md / evals.yaml first (#283). A `context-adapter.yaml`,
checked into the customer's OWN repository beside the context it describes,
declares how their corpus maps onto v0 context: which paths become which domains,
which source keys carry title/owners/definitions, and which whitelisted transform
reshapes each value.

This module PARSES and VALIDATES that file and does nothing else. It does not read
the customer corpus, evaluate a mapping path, apply a transform, or ingest
anything -- that is 283-4 -- and it opens no disclosure/status path -- that is
283-5. It produces a runtime `AdapterSpec`, never a second authority.

Two invariants carry over from the v0 format (`hyperset/context/schema.py`), and
both are the SAME failure being prevented -- silence about a field the customer
actually wrote:

  * an UNKNOWN KEY IS AN ERROR, at every level, exactly as an unknown manifest key
    is. The epic states it as "unknown key is an error becomes UNMAPPED KEY is an
    error": a key the adapter file names that this schema does not understand is
    refused, never dropped, so a customer cannot believe a mapping took effect that
    Hyperset ignored;
  * every `| transform` a mapping names MUST RESOLVE to the closed whitelist
    (`hyperset.context.adapter.transforms.TRANSFORM_NAMES`, landed #297). An
    unknown transform is an error here, one level up from where `apply_transform`
    refuses it, so a mapping that names code the whitelist does not enumerate is
    caught at validate time, not at apply time.

An adapter may change the SHAPE that carries meaning; it may never create meaning.
Reading `title` from `$.heading` is translation and is invisible. This slice
validates that the translation is well-formed and names only known transforms; it
does not yet run it. The boundary is stated in ADR 0028.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from hyperset.context.adapter.transforms import TRANSFORM_NAMES, UnknownTransform
from hyperset.context.errors import ContextValidationError

# The adapter FILE's own version, distinct from the v0 context `SCHEMA_VERSION`:
# the mapping contract evolves on its own clock, and a file that predates a change
# must be readable as what it is. Only `1` exists.
SCHEMA_VERSION = 1

ADAPTER_FILE = "context-adapter.yaml"

_TOP_FIELDS = frozenset({"schema_version", "adapter", "discover", "map"})
# The corpus-shape selectors. `unit` names the glob whose every match becomes one
# domain; `manifest` and `context_doc` name where a domain's structured fields and
# prose live. `evals` (the eval-scorer seam, a later sub-bead) is optional.
_DISCOVER_REQUIRED = frozenset({"unit", "manifest", "context_doc"})
_DISCOVER_OPTIONAL = frozenset({"evals"})
_DISCOVER_FIELDS = _DISCOVER_REQUIRED | _DISCOVER_OPTIONAL
# The v0 fields this slice maps. `domain` and `title` are required (a domain needs
# an id and a title); `owners` and `definitions` are optional. Fields from #283's
# fuller example (approved_sources, caveats, ...) are deliberately NOT accepted
# yet: an unmapped key is an error, so they land -- explicitly -- in a later slice
# rather than being silently tolerated now.
_MAP_REQUIRED = frozenset({"domain", "title"})
_MAP_OPTIONAL = frozenset({"owners", "definitions"})
_MAP_FIELDS = _MAP_REQUIRED | _MAP_OPTIONAL
_DEFINITIONS_FIELDS = frozenset({"from", "term", "statement"})


class AdapterValidationError(ContextValidationError):
    """A context-adapter.yaml is not a valid adapter file.

    Subclasses the v0 context error so a caller handles both the same way, and
    carries EVERY reason at once (not the first): an operator fixing a customer
    repository should see the whole list in one pass, mirroring the manifest
    parser, so a second unknown key or a third bad transform is not discovered one
    sync at a time.
    """


@dataclass(frozen=True)
class TransformStep:
    """One `| name(args)` step in a mapping expression. The NAME is validated here
    against the whitelist; `args_raw` is kept verbatim for the apply slice (283-4)
    and is not parsed or executed now -- this module runs no transform."""

    name: str
    args_raw: str


@dataclass(frozen=True)
class Mapping:
    """A parsed map expression: a source `path` piped through whitelisted `steps`.

    The path is NOT evaluated and no value is produced -- validation only checks
    that the expression is well-formed and that every step names a known transform.
    """

    raw: str
    path: str
    steps: tuple[TransformStep, ...]


@dataclass(frozen=True)
class Definitions:
    """The `map.definitions` block: a `source` glob plus the `term`/`statement`
    mappings each matched file yields."""

    source: str
    term: Mapping
    statement: Mapping


@dataclass(frozen=True)
class AdapterSpec:
    """A parsed, validated context-adapter.yaml. A runtime projection of the
    customer's mapping file, never a second store of meaning."""

    schema_version: int
    adapter: str
    discover: dict[str, str]
    domain: Mapping
    title: Mapping
    owners: Mapping | None
    definitions: Definitions | None


def parse_adapter(source: str | dict) -> AdapterSpec:
    """Parse and validate `source` (YAML text or an already-loaded mapping) into an
    `AdapterSpec`, or raise `AdapterValidationError` carrying every reason.

    Validate-only: no path is evaluated, no transform is applied, no corpus is
    read. Unknown keys at any level and unknown transforms are errors, never
    silent drops.
    """
    if isinstance(source, str):
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise AdapterValidationError([f"{ADAPTER_FILE} is not valid YAML: {exc}"]) from exc
    else:
        data = source
    if not isinstance(data, dict):
        raise AdapterValidationError([f"{ADAPTER_FILE} must be a mapping at the top level"])

    reasons: list[str] = []
    _reject_unknown(data, ADAPTER_FILE, _TOP_FIELDS, reasons)

    if data.get("schema_version") != SCHEMA_VERSION:
        reasons.append(
            f"{ADAPTER_FILE} schema_version must be {SCHEMA_VERSION}, "
            f"got {data.get('schema_version')!r}"
        )
    adapter = data.get("adapter")
    if not isinstance(adapter, str) or not adapter.strip():
        reasons.append("adapter must be a non-empty string naming the adapter")
        adapter = ""

    discover = _parse_discover(data.get("discover"), reasons)
    domain, title, owners, definitions = _parse_map(data.get("map"), reasons)

    if reasons:
        raise AdapterValidationError(reasons)
    return AdapterSpec(
        schema_version=SCHEMA_VERSION,
        adapter=adapter,
        discover=discover,
        domain=domain,
        title=title,
        owners=owners,
        definitions=definitions,
    )


def _reject_unknown(entry: dict, where: str, allowed: frozenset[str], reasons: list[str]) -> None:
    for key in sorted(set(entry) - allowed):
        reasons.append(
            f"{where}: unknown key {key!r} -- an unmapped key is an error, not a silent "
            f"drop; the accepted keys are {', '.join(sorted(allowed))}"
        )


def _parse_discover(discover: object, reasons: list[str]) -> dict[str, str]:
    if not isinstance(discover, dict):
        reasons.append("discover must be a mapping of corpus-shape selectors")
        return {}
    _reject_unknown(discover, "discover", _DISCOVER_FIELDS, reasons)
    out: dict[str, str] = {}
    for key in sorted(_DISCOVER_FIELDS):
        if key not in discover:
            if key in _DISCOVER_REQUIRED:
                reasons.append(f"discover.{key} is required (a glob or path string)")
            continue
        value = discover[key]
        if not isinstance(value, str) or not value.strip():
            reasons.append(f"discover.{key} must be a non-empty glob or path string")
            continue
        out[key] = value
    return out


def _parse_map(
    mapping: object, reasons: list[str]
) -> tuple[Mapping, Mapping, Mapping | None, Definitions | None]:
    empty = Mapping(raw="", path="", steps=())
    if not isinstance(mapping, dict):
        reasons.append("map must be a mapping of v0 fields to source expressions")
        return empty, empty, None, None
    _reject_unknown(mapping, "map", _MAP_FIELDS, reasons)

    domain = _parse_mapping_expr(mapping.get("domain"), "map.domain", reasons, required=True)
    title = _parse_mapping_expr(mapping.get("title"), "map.title", reasons, required=True)
    owners = (
        _parse_mapping_expr(mapping.get("owners"), "map.owners", reasons, required=False)
        if "owners" in mapping
        else None
    )
    definitions = (
        _parse_definitions(mapping.get("definitions"), reasons)
        if "definitions" in mapping
        else None
    )
    return domain or empty, title or empty, owners, definitions


def _parse_definitions(block: object, reasons: list[str]) -> Definitions | None:
    if not isinstance(block, dict):
        reasons.append("map.definitions must be a mapping with from, term, and statement")
        return None
    _reject_unknown(block, "map.definitions", _DEFINITIONS_FIELDS, reasons)
    source = block.get("from")
    if not isinstance(source, str) or not source.strip():
        reasons.append("map.definitions.from must be a non-empty glob or path string")
        source = ""
    term = _parse_mapping_expr(block.get("term"), "map.definitions.term", reasons, required=True)
    statement = _parse_mapping_expr(
        block.get("statement"), "map.definitions.statement", reasons, required=True
    )
    if source and term and statement:
        return Definitions(source=source, term=term, statement=statement)
    return None


def _parse_mapping_expr(
    expr: object, where: str, reasons: list[str], *, required: bool
) -> Mapping | None:
    if expr is None:
        if required:
            reasons.append(f"{where} is required (a source expression like '$.field | slug')")
        return None
    if not isinstance(expr, str) or not expr.strip():
        reasons.append(f"{where} must be a non-empty source expression")
        return None

    segments = _split_pipes(expr)
    if segments is None:
        reasons.append(f"{where}: unterminated quote in {expr!r}")
        return None
    path, steps_text = segments[0], segments[1:]
    if not path or not path.startswith("$"):
        reasons.append(f"{where}: a source path must start with '$' (got {path!r})")

    steps: list[TransformStep] = []
    for text in steps_text:
        step = _parse_step(text, where, reasons)
        if step is not None:
            steps.append(step)
    return Mapping(raw=expr, path=path, steps=tuple(steps))


def _parse_step(text: str, where: str, reasons: list[str]) -> TransformStep | None:
    # A step is `name` or `name(...)`. The name is matched exactly; the arg text is
    # kept verbatim for the apply slice, not parsed here (this module runs nothing).
    if not text:
        reasons.append(f"{where}: an empty transform step (a stray '|')")
        return None
    name, sep, rest = text.partition("(")
    name = name.strip()
    args_raw = (sep + rest).strip() if sep else ""
    if not name.isidentifier():
        reasons.append(f"{where}: malformed transform step {text!r}")
        return None
    if sep and not args_raw.endswith(")"):
        reasons.append(f"{where}: unbalanced parentheses in transform step {text!r}")
        return None
    if name not in TRANSFORM_NAMES:
        # Reuse the whitelist's own refusal text so validate-time and apply-time
        # name the same closed set (transforms.py is the one authority).
        reasons.append(f"{where}: {UnknownTransform(name, allowed=TRANSFORM_NAMES)}")
        return None
    return TransformStep(name=name, args_raw=args_raw)


def _split_pipes(expr: str) -> list[str] | None:
    """Split a mapping expression on top-level `|`, respecting single/double quotes
    so a `|` inside a transform argument (`default('a|b')`) does not split a step.
    Returns None on an unterminated quote, which the caller reports as malformed."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in expr:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == "|":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if quote is not None:
        return None
    parts.append("".join(current))
    return [part.strip() for part in parts]
