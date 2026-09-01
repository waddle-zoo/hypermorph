"""APPLY a validated context-adapter.yaml to a customer corpus (epic #283, hy-s8up).

283-3 (schema.py) parses and validates the mapping file. This slice RUNS it: it
reads the customer's corpus at its own commit, projects it through the validated
`AdapterSpec` into the EXISTING v0 normalized context shape, running the
whitelisted transforms on the REAL values -- and it fabricates nothing. An adapter
may change the shape that carries meaning; it may never create meaning (ADR 0028).

The projection reuses `hyperset.context.schema.parse_context`: the adapter builds a
v0-shaped manifest and context document from the customer corpus, and the existing
parser validates and normalizes them. So the adapter cannot produce a shape the v0
format does not already accept, and every v0 rule (ref conflicts, casefolding, the
round-trip) applies unchanged. The provenance win rides the sync path unchanged --
the snapshot's `commit_sha` is the corpus commit the reader resolved, so authority
points at the reviewed commit in the customer's repository, not a projector's build
artifact (#283).

Two invariants carry over from the schema, now on LIVE data:

  * UNMAPPED source key is an ERROR. Every key the customer's source manifest
    carries must be consumed by a `map` expression; a key with no mapping is
    refused, not dropped, so a field the customer wrote can never vanish silently.
  * every transform resolves to the whitelist -- reused directly, because the
    projection calls `apply_transform`, which is the whitelist's own closed
    dispatch. A value a transform cannot produce FAILS CLOSED (the transform
    raises); nothing is guessed.

Scope of THIS slice, fail-closed at every boundary rather than silently narrowed:

  * ONE unit per source. A context source's `path` already scopes one context
    directory, so the adapter projects that one unit into one domain -- the shape
    the one-source-one-domain sync persists. `discover.unit`'s full-corpus glob
    (one repository, many projects) is realized as one source per unit; expanding
    it into MANY domains from one source needs a multi-domain ingest model and is
    a later bead.
  * `map.definitions` reads a SEPARATE file set (`definitions.from`): each matched
    file yields one `{term, statement}` run through the whitelist (283-4b, hy-1xc6).
    This is what lets an adapter domain DECLARE concepts, so a directive naming it
    passes the coverage-claim gate and the domain is SELECTABLE via resolve --
    making `resolution.projection` reachable end-to-end, not only by driving
    `_governed` directly. Same fail-closed rules as the unit projection:
    unmapped-is-error per definition file, and a glob matching nothing is refused.
"""

from __future__ import annotations

import fnmatch

import yaml

from hyperset.context.adapter.schema import (
    ADAPTER_FILE,
    AdapterSpec,
    Definitions,
    Mapping,
    parse_adapter,
)
from hyperset.context.adapter.transforms import TransformError, apply_transform
from hyperset.context.errors import ContextValidationError
from hyperset.context.schema import ContextDocument, parse_context

# The v0 files the projection synthesises for `parse_context`. A fixed manifest
# name and context-doc name, so the adapter output is an ordinary v0 context
# directory the existing parser validates -- no adapter-only code path in the
# parser.
_V0_MANIFEST = "manifest.yaml"
_V0_CONTEXT_DOC = "context.md"


class AdapterApplyError(ContextValidationError):
    """Applying a validated adapter to the customer corpus failed.

    Subclasses the v0 context error and carries every reason at once, like the
    schema and the manifest parser: an operator sees the whole list -- an unmapped
    source key, a transform that could not produce a value, a missing corpus file
    -- in one sync, not one per attempt.
    """


def has_adapter(files: dict[str, str]) -> bool:
    """Whether the corpus read carries a context-adapter.yaml. A source without
    one is ordinary v0 context and is parsed unchanged (back-compat)."""
    return ADAPTER_FILE in files


def adapter_projection(files: dict[str, str]) -> dict | None:
    """The DISCLOSURE of what the adapter did, or `None` for a non-adapter snapshot
    (283-5, hy-13b8). Served as `resolution.projection` so an agent reading a bundle
    can see it came through an adapter, which one, and what -- if anything -- was
    unmapped, lossy, or derived.

    Recomputed DETERMINISTICALLY from the stored adapter file (a snapshot keeps its
    `context-adapter.yaml`), so it needs no new persisted field and cannot drift
    from the file the sync validated. Report only.

    In this slice (283-4 apply) the three field-lists are EMPTY BY CONSTRUCTION: an
    unmapped source key is an ERROR (never tolerated, so `fields_unmapped` is
    empty), and the adapter authors nothing and loses nothing (`fields_lossy` /
    `fields_derived` empty). The lists are the FORK-AGNOSTIC substrate every fork
    needs; the status-degrade for an unreviewed derived field is Brandon's fork 3,
    built at 283-6, and is NOT decided here. A `fields_derived` entry will carry
    `{field, reviewed_by}` when that lands.
    """
    if not has_adapter(files):
        return None
    try:
        spec = parse_adapter(files[ADAPTER_FILE])
    except ContextValidationError:
        # A snapshot exists only if apply -- which validates the adapter --
        # succeeded, so this cannot happen for a stored snapshot; fail safe to no
        # disclosure rather than raise on a serve path.
        return None
    return {
        "adapter": spec.adapter,
        "adapter_version": spec.schema_version,
        "fields_unmapped": [],
        "fields_lossy": [],
        "fields_derived": [],
    }


def apply_adapter(files: dict[str, str]) -> ContextDocument:
    """Project the customer corpus `files` (one unit) through its
    context-adapter.yaml into a v0 `ContextDocument`, or raise `AdapterApplyError`.

    Validate-then-apply: the adapter file is parsed and validated first (283-3),
    then run on the real corpus. Every failure -- an invalid adapter, an unmapped
    source key, a transform that fails, a missing corpus file, a not-yet-applied
    block -- is a reason, never a silent drop.
    """
    reasons: list[str] = []
    try:
        spec = parse_adapter(files[ADAPTER_FILE])
    except ContextValidationError as exc:
        # An invalid adapter file is an apply failure too; carry its reasons.
        raise AdapterApplyError(exc.reasons) from exc

    source_data, body = _read_unit(spec, files, reasons)
    if source_data is None:
        raise AdapterApplyError(reasons)

    manifest = _project(spec, source_data, reasons)
    if spec.definitions is not None:
        # 283-4b: apply `map.definitions`, which reads a SEPARATE file set (the
        # glob in `definitions.from`) rather than the unit manifest. Each matched
        # file yields one `{term, statement}` by running the two mappings on its
        # structured data. This is what lets an adapter domain DECLARE concepts,
        # so a directive naming it can pass the coverage-claim gate and be
        # SELECTED via resolve -- making `resolution.projection` reachable
        # end-to-end, not only by driving `_governed` directly. Read from the
        # customer's own files through the whitelist: translation, never
        # authoring (ADR 0028), so `adapter_projection` still reports nothing
        # derived.
        definitions = _project_definitions(spec.definitions, files, reasons)
        if definitions:
            manifest["definitions"] = definitions
    if reasons:
        raise AdapterApplyError(reasons)

    # Hand the synthesised v0 directory to the existing parser: it validates and
    # normalizes exactly as it does a hand-written manifest, so the adapter output
    # is held to every v0 rule and produces the identical normalized shape.
    v0_files = {
        _V0_MANIFEST: yaml.safe_dump(manifest, sort_keys=True),
        _V0_CONTEXT_DOC: body,
    }
    try:
        return parse_context(v0_files)
    except ContextValidationError as exc:
        raise AdapterApplyError(exc.reasons) from exc


def _read_unit(
    spec: AdapterSpec, files: dict[str, str], reasons: list[str]
) -> tuple[dict | None, str]:
    """The customer source manifest (parsed) and the context-doc body for the unit.

    `discover.manifest` names the file the structured fields live in -- YAML front
    matter of a markdown file, or a whole YAML file -- and `discover.context_doc`
    the prose. A named file absent from the corpus is an error, never an empty
    default.
    """
    manifest_name = spec.discover.get("manifest", "")
    if manifest_name not in files:
        reasons.append(f"discover.manifest names {manifest_name!r}, absent from the corpus")
        return None, ""
    front, manifest_body = _split_front_matter(files[manifest_name])
    raw = front if front is not None else files[manifest_name]
    try:
        source_data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        reasons.append(f"{manifest_name} is not valid YAML: {exc}")
        return None, ""
    if not isinstance(source_data, dict):
        reasons.append(f"{manifest_name} must parse to a mapping of source fields")
        return None, ""

    doc_name = spec.discover.get("context_doc", "")
    if doc_name == manifest_name:
        # Same file: the prose is the body after the front matter.
        body = manifest_body
    elif doc_name in files:
        # A separate file is prose in full.
        body = files[doc_name]
    else:
        reasons.append(f"discover.context_doc names {doc_name!r}, absent from the corpus")
        body = ""
    return source_data, body


def _project(spec: AdapterSpec, source_data: dict, reasons: list[str]) -> dict:
    """Build a v0 manifest dict from the source data, running the transforms.

    Enforces unmapped-is-error: every key the source manifest carries must be
    consumed by a map expression, or it is refused. A transform that cannot produce
    a value fails closed and becomes a reason.
    """
    # Unmapped-is-error is checked at the LEAF, not the top level: a mapping that
    # reads a nested path (`$.meta.title`) consumes exactly that leaf, so a SIBLING
    # the customer wrote under the same parent (`meta.secret`) is unmapped and must
    # error, never vanish because its top-level parent was touched (adversary).
    consumed = _consumed_paths(spec)
    if () not in consumed:  # a `$` mapping would consume the whole document
        for leaf in sorted(_present_leaves(source_data)):
            if leaf not in consumed:
                dotted = ".".join(leaf) or "<root>"
                reasons.append(
                    f"source key {dotted!r} is unmapped -- an unmapped key is an error, not a "
                    "silent drop; add a mapping for it or remove it from the source"
                )

    manifest: dict = {"schema_version": 1, "context_doc": _V0_CONTEXT_DOC}
    domain = _apply_scalar(spec.domain, source_data, "map.domain", reasons)
    if domain is not None:
        manifest["domain"] = domain
    title = _apply_scalar(spec.title, source_data, "map.title", reasons)
    if title is not None:
        manifest["title"] = title
    if spec.owners is not None:
        owners = _apply_list(spec.owners, source_data, "map.owners", reasons)
        if owners is not None:
            manifest["owners"] = owners
    return manifest


def _project_definitions(
    definitions: Definitions, files: dict[str, str], reasons: list[str]
) -> list[dict]:
    """Project the corpus files matching `definitions.from` into v0 `definitions`.

    One matched file yields one `{term, statement}`: the `term` and `statement`
    mappings are run against the file's structured data (front matter, or the
    whole file as YAML), exactly as the unit mappings run against the unit
    manifest. The customer's OWN words, reshaped by the whitelist -- a definition
    is read, never invented.

    Fail closed at every boundary, never a silent gap:
      * a glob matching NO file is an error, not an empty `definitions` block;
      * unmapped-is-error holds per file -- a leaf the `term`/`statement`
        mappings do not consume is refused, so a field the customer wrote under a
        concept file cannot vanish;
      * the adapter file itself is never a definition source, even if the glob
        would match it -- it is Hyperset's projection control, not customer data.
    """
    consumed = {_path_keys(definitions.term.path), _path_keys(definitions.statement.path)}
    matched = sorted(
        name for name in files if name != ADAPTER_FILE and fnmatch.fnmatch(name, definitions.source)
    )
    if not matched:
        reasons.append(
            f"map.definitions.from {definitions.source!r} matched no corpus file -- "
            "a declared definitions source that finds nothing is an error, not an empty block"
        )
        return []

    projected: list[dict] = []
    for name in matched:
        front, _ = _split_front_matter(files[name])
        raw = front if front is not None else files[name]
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            reasons.append(f"definitions source {name!r} is not valid YAML: {exc}")
            continue
        if not isinstance(data, dict):
            reasons.append(f"definitions source {name!r} must parse to a mapping of fields")
            continue
        if () not in consumed:
            for leaf in sorted(_present_leaves(data)):
                if leaf not in consumed:
                    dotted = ".".join(leaf) or "<root>"
                    reasons.append(
                        f"definitions source {name!r} key {dotted!r} is unmapped -- an unmapped "
                        "key is an error, not a silent drop; map it with term/statement or remove"
                    )
        term = _apply_scalar(definitions.term, data, f"map.definitions.term in {name!r}", reasons)
        statement = _apply_scalar(
            definitions.statement, data, f"map.definitions.statement in {name!r}", reasons
        )
        if term is not None and statement is not None:
            projected.append({"term": term, "statement": statement})
    return projected


def _consumed_paths(spec: AdapterSpec) -> set[tuple[str, ...]]:
    """The exact source LEAF each unit mapping reads, as a key tuple. `$.urn` ->
    ('urn',); `$.owners[*]` -> ('owners',); `$.meta.title` -> ('meta','title');
    `$` -> () (the whole document). Only mappings that read the unit manifest count
    -- definitions, when applied, read a separate file set."""
    consumed: set[tuple[str, ...]] = set()
    for mapping in (spec.domain, spec.title, spec.owners):
        if mapping is not None:
            consumed.add(_path_keys(mapping.path))
    return consumed


def _path_keys(path: str) -> tuple[str, ...]:
    remainder = path.lstrip("$").lstrip(".")
    if not remainder:
        return ()
    keys: list[str] = []
    for segment in remainder.split("."):
        key = segment[:-3] if segment.endswith("[*]") else segment
        keys.append(key.split("[", 1)[0])
    return tuple(keys)


def _present_leaves(data: object, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Every root-to-leaf key path in the source data, where a leaf is any value
    that is NOT a (non-empty) mapping. A list is a leaf (a mapping reads the whole
    list via `[*]`); descending only into dicts means every scalar the customer
    wrote is a leaf that must be mapped or it is reported unmapped."""
    if isinstance(data, dict) and data:
        leaves: set[tuple[str, ...]] = set()
        for key, value in data.items():
            leaves |= _present_leaves(value, prefix + (str(key),))
        return leaves
    return {prefix}


def _apply_scalar(mapping: Mapping, data: dict, where: str, reasons: list[str]) -> str | None:
    value = _resolve(mapping, data, where, reasons)
    if value is None:
        return None
    if isinstance(value, list):
        reasons.append(f"{where}: expected one value, the path resolved to a list")
        return None
    return value


def _apply_list(mapping: Mapping, data: dict, where: str, reasons: list[str]) -> list[str] | None:
    value = _resolve(mapping, data, where, reasons)
    if value is None:
        return None
    return value if isinstance(value, list) else [value]


def _resolve(mapping: Mapping, data: dict, where: str, reasons: list[str]) -> object:
    """Evaluate a mapping's path against the data, then run every transform step on
    the real value(s). Returns a str, a list[str], or None on any reason."""
    try:
        value = _evaluate_path(mapping.path, data)
    except _PathError as exc:
        reasons.append(f"{where}: {exc}")
        return None
    try:
        if isinstance(value, list):
            return [_run_steps(mapping, item) for item in value]
        return _run_steps(mapping, value)
    except TransformError as exc:
        reasons.append(f"{where}: {exc}")
        return None


def _run_steps(mapping: Mapping, value: object) -> str:
    for step in mapping.steps:
        value = apply_transform(step.name, value, _parse_args(step.args_raw))
    if not isinstance(value, str):
        # A bare path with no transform must resolve to a string; a structured
        # value with no transform to flatten it is not a governed value.
        raise TransformError(
            f"path resolved to a {type(value).__name__}, not a string, and no transform reshaped it"
        )
    return value


class _PathError(ValueError):
    """A mapping path could not be resolved against the source data."""


def _evaluate_path(path: str, data: object) -> object:
    """A small, closed JSONPath subset: `$`, `$.key`, `$.a.b`, and `$.key[*]`.

    No wildcards on keys, no filters, no functions -- the mapping expresses WHICH
    field, and the transform (from the whitelist) is the only thing that reshapes a
    value. An absent key is an error (fail closed, no fabricated default); `[*]`
    requires a list.
    """
    if not path.startswith("$"):
        raise _PathError(f"a source path must start with '$' (got {path!r})")
    remainder = path[1:].lstrip(".")
    current: object = data
    if not remainder:
        return current
    for segment in remainder.split("."):
        key, wildcard = (segment[:-3], True) if segment.endswith("[*]") else (segment, False)
        if not key:
            raise _PathError(f"malformed path segment in {path!r}")
        if not isinstance(current, dict) or key not in current:
            raise _PathError(f"source has no value at {path!r} (missing {key!r})")
        current = current[key]
        if wildcard:
            if not isinstance(current, list):
                raise _PathError(f"{path!r} used [*] on {key!r}, which is not a list")
    return current


def _parse_args(args_raw: str) -> tuple[str, ...]:
    """Parse a transform's verbatim `(...)` arg text into literal string args.

    Args are DATA, never code: each is a single- or double-quoted literal, or a
    bare token. `('team:')` -> ('team:',); `()` -> (). This is the only place the
    schema's stored `args_raw` is interpreted, and it interprets it as literals."""
    text = args_raw.strip()
    if not text:
        return ()
    if not (text.startswith("(") and text.endswith(")")):
        raise TransformError(f"malformed transform arguments {args_raw!r}")
    inner = text[1:-1].strip()
    if not inner:
        return ()
    args: list[str] = []
    for part in _split_args(inner):
        part = part.strip()
        if len(part) >= 2 and part[0] in "'\"" and part[-1] == part[0]:
            args.append(part[1:-1])
        else:
            args.append(part)
    return tuple(args)


def _split_args(inner: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in inner:
        if quote is not None:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            current.append(char)
        elif char == ",":
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def _split_front_matter(text: str) -> tuple[str | None, str]:
    """Split a markdown file's YAML front matter from its body.

    A file that opens with a `---` line has the block up to the next `---` line as
    front matter (the structured manifest) and the rest as body (the context doc).
    A file with no front matter returns `(None, text)` -- the caller treats the
    whole file as YAML for a manifest, or as prose for a context doc.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            front = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            return front, body
    # Unterminated front matter: hand the whole file back. The caller loads it as
    # YAML for a manifest, where the leading `---` is a document marker, so its keys
    # are still read and held to unmapped-is-error -- not silently treated as prose.
    return None, text
