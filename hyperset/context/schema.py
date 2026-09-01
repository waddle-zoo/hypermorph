"""The narrow v0 Git context format (hy-gh-43).

    domains/revenue/
      manifest.yaml   structured fields the revenue benchmark needs
      context.md      human-readable domain guidance
      evals.yaml      customer-owned locked evaluation cases

Only fields the revenue benchmark requires are supported, and an unknown
field is an error rather than a silent drop: a customer who writes a key
Hyperset ignores would otherwise believe governed context says something it
does not. This is a customer-owned Git contract, not a Hyperset CMS model --
parsing produces a runtime projection, never a second authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from hyperset.context.errors import ContextValidationError

MANIFEST_FILE = "manifest.yaml"
SCHEMA_VERSION = 1

# Why a ref could not be used, for the callers that must serve the refusal to
# an agent rather than print it. Defined at the layer that produces the
# refusal and imported by the bundle contract, never restated there. They are
# separate codes because a caller acts on them differently: a malformed ref is
# fixable by editing the directive, an ambiguous one by qualifying it, and an
# absent one is not fixable by editing at all -- it takes a different ref, a
# change in the estate, or falling back to governed context alone.
#
# `ref_awaiting_sync` splits that last case in two, and the split is about what
# the absence is EVIDENCE OF rather than about what fixes it (hy-gh-118). A ref
# that matched nothing on a connector whose connections have all read
# successfully is a ref whose asset was looked for and is not there.  The same
# ref on a connector that has never completed a sync, or whose last attempt
# failed, was never looked for at all: reporting "not observed" there states an
# absence nobody measured. A sync is what changes `ref_awaiting_sync`, and is
# exactly what does not change `ref_not_observed` ON AN UNCHANGED ESTATE --
# once the asset exists, a sync is what makes it visible, since observations
# reach the resolver only through a sync run (hy-qjam). That is why one
# sentence about "an unobserved ref needs a sync" cannot cover both. Neither is
# retryable in the sense `RETRYABLE_WARNING_CODES` means -- see the note there.
REF_MALFORMED = "ref_malformed"
REF_AMBIGUOUS = "ref_ambiguous"
REF_NOT_OBSERVED = "ref_not_observed"
REF_AWAITING_SYNC = "ref_awaiting_sync"

SUPPORTED_MANIFEST_FIELDS = (
    "schema_version",
    "domain",
    "title",
    "parent",
    "owners",
    "context_doc",
    "evals",
    "definitions",
    "approved_sources",
    "prohibited_sources",
    "fields",
    "joins",
    "filters",
    "grain",
    "checks",
    "caveats",
)

# Every asset type the two v0 connectors actually observe. A ref naming
# anything else cannot be linked to evidence, so it is rejected at parse
# time rather than failing later as "unknown evidence".
EVIDENCE_ASSET_TYPES = {
    "superset": ("database", "dataset", "chart", "dashboard"),
    "datahub": ("dataset", "domain", "corp_user", "glossary_term"),
}

GOVERNED_SOURCE_KINDS = ("table", "pipeline")

# The manifest fields one agent-drafted CANDIDATE definition may carry (hy-jg2v,
# flywheel step 4). A subset of `SUPPORTED_MANIFEST_FIELDS`: a draft proposes the
# semantic core -- what a term means, which sources it rests on, the fields and
# joins -- and NOT the directory scaffolding (`schema_version`, `owners`,
# `context_doc`, `evals`) a human writes when they approve it into Git. Named
# here so `validate_definition_draft` and the authoring tool schema agree on one
# set rather than two.
DRAFT_DEFINITION_FIELDS = (
    "definitions",
    "approved_sources",
    "prohibited_sources",
    "fields",
    "joins",
    "filters",
    "grain",
    "checks",
    "caveats",
)


@dataclass(frozen=True)
class EvidenceRef:
    """An optional connector-native corroboration of a governed source."""

    connector: str
    asset_type: str
    external_id: str
    governed_source_ref: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.connector}:{self.asset_type}:{self.external_id}"


@dataclass(frozen=True)
class ContextDocument:
    """One parsed, validated context directory."""

    domain: str
    title: str
    manifest: dict
    normalized: dict
    owner_refs: list[str]
    parent: str | None = None
    evidence_refs: list[EvidenceRef] = field(default_factory=list)


def parse_context(files: dict[str, str]) -> ContextDocument:
    """Parse the files of one context directory. Raises
    `ContextValidationError` carrying every reason the directory is not
    valid v0 context."""
    reasons: list[str] = []
    manifest = _load_manifest(files, reasons)
    if manifest is None:
        raise ContextValidationError(reasons)

    # Casefolded to ONE canonical form (hy-gh-282, panel): a domain is an
    # identity two sources must not both claim, and `Revenue` vs `revenue` would
    # otherwise be two identities -- so an operator scripting slugs silently gets
    # two domains, the collision check misses them, and a resolve for one ignores
    # the other. Canonicalizing here means the stored snapshot domain, the
    # collision query, and resolve matching all agree on case. (Demo/shipped
    # contexts are already lowercase; a pre-existing mixed-case domain would
    # re-canonicalize on its next sync, which is the intended reconciliation.)
    domain = (_string(manifest, "domain", reasons, required=True) or "").strip().casefold()
    title = _string(manifest, "title", reasons) or domain
    # The governed hierarchy parent (ADR-0031): a domain MAY name exactly one parent
    # domain by its governed SLUG, casefolded to the same canonical form as `domain` so
    # the parent chain matches on case. Absent/blank means ROOT. Never inferred from a
    # name, prefix, title, or ref -- only the slug stated in Git (ADR 0012). The only
    # forest rule checkable from this manifest alone is the self-cycle; unknown-parent
    # and multi-domain cycles need the full parent map and are checked at sync
    # (`validate_forest`), where every domain is known.
    parent = (_string(manifest, "parent", reasons) or "").strip().casefold() or None
    if parent is not None and parent == domain:
        reasons.append(f"domain {domain!r} names itself as its parent")
    owner_refs = _string_list(manifest, "owners", reasons)

    context_doc = _referenced_file(manifest, "context_doc", files, reasons, required=True)

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "domain": domain,
        "title": title,
        "parent": parent,
        "owner_refs": owner_refs,
        "definitions": _definitions(manifest, reasons),
        "approved_sources": _approved_sources(manifest, reasons),
        "prohibited_sources": _prohibited_sources(manifest, reasons),
        "fields": _fields(manifest, reasons),
        "joins": _joins(manifest, reasons),
        "filters": _string_list(manifest, "filters", reasons),
        "grain": _grain(manifest, "grain", reasons, "grain"),
        "checks": _string_list(manifest, "checks", reasons),
        "caveats": _string_list(manifest, "caveats", reasons),
        "documents": {
            "context_doc": {
                "path": manifest.get("context_doc"),
                "text": files.get(str(manifest.get("context_doc"))),
            }
        },
        "evals": _evals(manifest, files, reasons),
    }
    if context_doc is not None and not context_doc.strip():
        reasons.append(f"{manifest.get('context_doc')!r} is empty")

    refs = _collect_refs(normalized, reasons)
    _check_ref_conflicts(normalized, reasons)
    if reasons:
        raise ContextValidationError(reasons)
    return ContextDocument(
        domain=domain,
        title=title,
        manifest=manifest,
        normalized=normalized,
        owner_refs=owner_refs,
        parent=parent,
        evidence_refs=refs,
    )


def normalize(files: dict[str, str]) -> dict:
    """The deterministic runtime representation of one context directory."""
    return parse_context(files).normalized


def to_manifest_document(normalized: dict) -> dict:
    """Rebuild the supported manifest fields from the normalized form.

    The round-trip guarantee hy-gh-43 asks for: for a manifest using only
    supported v0 fields, this returns exactly what the checked-in YAML
    declared, so the runtime projection can be proven lossless rather than
    assumed to be.
    """
    document = {
        "schema_version": normalized["schema_version"],
        "domain": normalized["domain"],
        "title": normalized["title"],
        "parent": normalized.get("parent"),
        "owners": list(normalized["owner_refs"]),
        "context_doc": normalized["documents"]["context_doc"]["path"],
        "evals": normalized["evals"]["path"],
        "definitions": [dict(item) for item in normalized["definitions"]],
        "approved_sources": [dict(item) for item in normalized["approved_sources"]],
        "prohibited_sources": [dict(item) for item in normalized["prohibited_sources"]],
        "fields": [dict(item) for item in normalized["fields"]],
        "joins": [dict(item) for item in normalized["joins"]],
        "filters": list(normalized["filters"]),
        "grain": normalized["grain"],
        "checks": list(normalized["checks"]),
        "caveats": list(normalized["caveats"]),
    }
    return {key: value for key, value in document.items() if value not in (None, [], {})}


def validate_definition_draft(draft: dict, *, domain: str) -> dict:
    """Validate one agent-drafted candidate definition and return its normalized
    form, or raise `ContextValidationError` carrying every reason (hy-jg2v).

    An assist-class draft faces the SAME structural rules a human's Git commit
    faces at sync time, by REUSING the parse helpers rather than a parallel
    ruleset: the definitions, the source refs, the field/join shapes, and the
    cross-field conflicts (`_collect_refs`, `_check_ref_conflicts`) -- a field
    reading a non-approved source, a ref both approved and prohibited. So an
    invalid draft is refused here and never persisted.

    Deliberately NOT `parse_context`: a single-definition draft has no
    `context_doc` or `evals` files yet -- those are the human's to write on
    approval -- so this validates the semantic core and nothing about the
    directory scaffolding a full context requires.
    """
    if not isinstance(draft, dict):
        raise ContextValidationError(["a candidate definition draft must be a mapping"])
    reasons: list[str] = []
    for key in sorted(set(draft) - set(DRAFT_DEFINITION_FIELDS)):
        reasons.append(
            f"draft has unsupported field {key!r}; a candidate definition supports "
            f"{', '.join(DRAFT_DEFINITION_FIELDS)}"
        )
    normalized = {
        "domain": domain,
        "definitions": _definitions(draft, reasons),
        "approved_sources": _approved_sources(draft, reasons),
        "prohibited_sources": _prohibited_sources(draft, reasons),
        "fields": _fields(draft, reasons),
        "joins": _joins(draft, reasons),
        "filters": _string_list(draft, "filters", reasons),
        "grain": _string(draft, "grain", reasons),
        "checks": _string_list(draft, "checks", reasons),
        "caveats": _string_list(draft, "caveats", reasons),
        # No case scaffolding on a draft, but `_governed_refs` reads this key.
        "evals": {"path": None, "cases": []},
    }
    if not normalized["definitions"]:
        reasons.append("a candidate definition must state at least one definition")
    _collect_refs(normalized, reasons)
    _check_ref_conflicts(normalized, reasons)
    if reasons:
        raise ContextValidationError(reasons)
    return normalized


# --------------------------------------------------------------------------
# Field parsing. Every helper appends to `reasons` instead of raising, so one
# sync reports every problem in the customer's repository at once.
# --------------------------------------------------------------------------


def _load_manifest(files: dict[str, str], reasons: list[str]) -> dict | None:
    if MANIFEST_FILE not in files:
        reasons.append(f"{MANIFEST_FILE} is missing")
        return None
    try:
        manifest = yaml.safe_load(files[MANIFEST_FILE])
    except yaml.YAMLError as exc:
        reasons.append(f"{MANIFEST_FILE} is not valid YAML: {exc}")
        return None
    if not isinstance(manifest, dict):
        reasons.append(f"{MANIFEST_FILE} must be a mapping")
        return None
    if manifest.get("schema_version") != SCHEMA_VERSION:
        reasons.append(
            f"{MANIFEST_FILE} schema_version must be {SCHEMA_VERSION}, "
            f"got {manifest.get('schema_version')!r}"
        )
        return None
    for key in manifest:
        if key not in SUPPORTED_MANIFEST_FIELDS:
            reasons.append(
                f"{MANIFEST_FILE} has unsupported field {key!r}; v0 supports "
                f"{', '.join(SUPPORTED_MANIFEST_FIELDS)}"
            )
    return manifest


def _string(manifest: dict, key: str, reasons: list[str], *, required: bool = False) -> str | None:
    value = manifest.get(key)
    if value is None:
        if required:
            reasons.append(f"{key} is required")
        return None
    if not isinstance(value, str) or not value.strip():
        reasons.append(f"{key} must be a non-empty string")
        return None
    return value.strip()


def _grain(container: dict, key: str, reasons: list[str], subject: str) -> str | None:
    """A grain string, rejected if it carries the node-id delimiter ':' (hy-c89s).

    A grain is embedded VERBATIM into a `grain:<...>` domain_graph node id, and ':'
    is that id's segment delimiter. The domain grain keys `grain:{grain}` and a
    per-source grain keys `grain:{ref}:{grain}`; a grain that itself contained ':'
    could make one served id the concatenation of a DIFFERENT (ref, grain) pair, or
    a domain-grain id collide with a source-grain id -- the aliasing class the
    adversary flagged. The source ref is allowed to contain ':' (governed refs are
    `table:...` by construction and are the id's natural multi-part key); only the
    appended free-text grain is forbidden the delimiter, because it, not the ref, is
    the ambiguous trailing segment. No governed grain uses ':' today, so this
    rejects only a would-be colliding value and changes no current served id."""
    value = _string(container, key, reasons)
    if value is not None and ":" in value:
        reasons.append(f"{subject} must not contain ':' (a node-id delimiter)")
        return None
    return value


def _string_list(manifest: dict, key: str, reasons: list[str]) -> list[str]:
    value = manifest.get(key, [])
    if not isinstance(value, list):
        reasons.append(f"{key} must be a list")
        return []
    items = []
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            reasons.append(f"{key} entries must be non-empty strings")
            continue
        items.append(entry.strip())
    return items


def _mappings(manifest: dict, key: str, reasons: list[str]) -> list[dict]:
    value = manifest.get(key, [])
    if not isinstance(value, list):
        reasons.append(f"{key} must be a list")
        return []
    items = []
    for entry in value:
        if not isinstance(entry, dict):
            reasons.append(f"{key} entries must be mappings")
            continue
        items.append(entry)
    return items


def _reject_unknown(entry: dict, key: str, allowed: set[str], reasons: list[str]) -> None:
    for field_name in entry:
        if field_name not in allowed:
            reasons.append(f"{key} entry has unsupported field {field_name!r}")


def _referenced_file(
    manifest: dict, key: str, files: dict[str, str], reasons: list[str], *, required: bool
) -> str | None:
    name = _string(manifest, key, reasons, required=required)
    if name is None:
        return None
    if name not in files:
        reasons.append(f"{key} points at {name!r}, which is not in the context path")
        return None
    return files[name]


def _definitions(manifest: dict, reasons: list[str]) -> list[dict]:
    definitions = []
    for entry in _mappings(manifest, "definitions", reasons):
        _reject_unknown(entry, "definitions", {"term", "statement"}, reasons)
        term = _string(entry, "term", reasons, required=True)
        statement = _string(entry, "statement", reasons, required=True)
        if term is None or statement is None:
            continue
        definitions.append({"term": term, "statement": statement})
    return definitions


# The per-source facets a manifest may attach to an approved source. `grain`
# (hy-gh-284 slice 1) is a bare string; `classification` (284-6a, hy-4giv) is a
# CLOSED value set -- a governed sensitivity label surfaced like grain but NOT
# enforced here (enforcing whether a restricted/pii source's payload may enter a
# bundle is 284-9, a resolve-path hook that needs the enterprise access model).
# The remaining policy-laden facets (freshness, lineage, checks) stay DELIBERATELY
# absent; they gate later #284 beads. An unrecognized facet sub-key is an ERROR,
# not a silent drop, exactly as an unknown top-level manifest key is: the failure
# being prevented is silence about something the customer actually wrote.
_APPROVED_SOURCE_FACETS = {"grain", "classification", "freshness", "lineage", "checks"}

# The closed classification vocabulary, most restrictive first. A value outside
# this set is an ERROR, never a silent drop or a passed-through label a consumer
# would read as governed while nothing verified it. Surface-only: this states the
# customer's own label; no access or PII decision is made from it here (284-9).
_CLASSIFICATION_VALUES = ("restricted", "pii", "internal", "public")


def _classification(facets: dict, reasons: list[str]) -> str | None:
    """An approved source's `facets.classification`, validated against the closed
    vocabulary, or `None` when it declares none. Absent/null stores nothing; a
    present value outside `_CLASSIFICATION_VALUES` (or a blank/non-string one, via
    `_string`) is an error -- an unknown sensitivity label must not ride through as
    if it were governed."""
    value = _string(facets, "classification", reasons)
    if value is None:
        return None
    if value not in _CLASSIFICATION_VALUES:
        reasons.append(
            f"approved_sources facets classification {value!r} is not one of "
            f"{', '.join(_CLASSIFICATION_VALUES)}"
        )
        return None
    return value


# The freshness contract a source may declare (284-6b, hy-6c8z): a small
# structured shape, not a bare string. `cadence` is how often the source refreshes
# and `max_staleness` is the SLA -- the oldest the data may be before it is stale.
# Both are non-empty strings (a label like `daily` / `24h`, stated as the governed
# context states it, not parsed into a duration here). An unknown sub-key is an
# error, and a freshness block that declares NEITHER is malformed -- a contract
# that says nothing. Surface-only: no staleness is computed or enforced from this
# (that is a later check bead); this states the governed contract and no more.
_FRESHNESS_SUBKEYS = {"cadence", "max_staleness"}


def _freshness(facets: dict, reasons: list[str]) -> dict | None:
    """An approved source's `facets.freshness`, or `None` when it declares none.
    Absent/null stores nothing; a non-mapping, an unknown sub-key, a blank/
    non-string `cadence`/`max_staleness`, or a block that declares NEITHER is an
    error -- an incomplete or malformed contract must not ride through as if it
    were a governed one."""
    value = facets.get("freshness")
    if value is None:
        return None
    if not isinstance(value, dict):
        reasons.append(
            "approved_sources facets freshness must be a mapping of cadence/max_staleness"
        )
        return None
    _reject_unknown(value, "approved_sources facets freshness", _FRESHNESS_SUBKEYS, reasons)
    contract: dict = {}
    cadence = _string(value, "cadence", reasons)
    if cadence is not None:
        contract["cadence"] = cadence
    max_staleness = _string(value, "max_staleness", reasons)
    if max_staleness is not None:
        contract["max_staleness"] = max_staleness
    if not contract:
        reasons.append(
            "approved_sources facets freshness declares neither cadence nor max_staleness"
        )
        return None
    return contract


# The lineage contract a source may declare (284-7, hy-sr7w): a small structured
# provenance shape, `produced_by` (the upstream system/job that produces this
# source) and/or `upstream` (a list of refs this source derives from). `produced_by`
# is a non-empty string; `upstream` is a list of non-empty string refs, stated as
# the governed context states them -- not resolved to nodes, not walked. An unknown
# sub-key is an error, and a lineage block that declares NEITHER is malformed.
# Surface-only: no cycle detection, reachability, or derived status is computed
# from this (a later check bead); this states the governed contract and no more.
_LINEAGE_SUBKEYS = {"produced_by", "upstream"}


def _lineage(facets: dict, reasons: list[str]) -> dict | None:
    """An approved source's `facets.lineage`, or `None` when it declares none.
    Absent/null stores nothing; a non-mapping, an unknown sub-key, a blank/
    non-string `produced_by`, a non-list or blank-entry `upstream`, or a block that
    declares NEITHER is an error -- an incomplete or malformed provenance contract
    must not ride through as if it were a governed one."""
    value = facets.get("lineage")
    if value is None:
        return None
    if not isinstance(value, dict):
        reasons.append("approved_sources facets lineage must be a mapping of produced_by/upstream")
        return None
    _reject_unknown(value, "approved_sources facets lineage", _LINEAGE_SUBKEYS, reasons)
    contract: dict = {}
    before = len(reasons)
    produced_by = _string(value, "produced_by", reasons)
    if produced_by is not None:
        contract["produced_by"] = produced_by
    # `upstream` is present only when the key is; an absent key is not an empty
    # declaration. `_string_list` errors on a non-list or a blank/non-string entry.
    if "upstream" in value:
        upstream = _string_list(value, "upstream", reasons)
        if upstream:
            contract["upstream"] = upstream
    if not contract:
        # An empty contract is malformed either way, but only say "declares neither"
        # when no more specific reason was already recorded -- a blank `produced_by`
        # or a blank `upstream` entry is a stated-but-malformed field, not a missing
        # one, and its own error already names it. `{}` and an empty `upstream` list
        # record nothing above, so they fall to this reason.
        if len(reasons) == before:
            reasons.append(
                "approved_sources facets lineage declares neither produced_by nor upstream"
            )
        return None
    return contract


# The checks contract a source may declare (284-8, hy-w16y): the data-quality
# checks the source OWNS/asserts, each a small structured shape -- a required
# `name` (the check's identifier) plus an optional `description` and `severity`,
# all non-empty strings stated as the governed context states them. `facets.checks`
# is a LIST, since a source asserts zero or more checks; an entry that is not a
# mapping, an unknown sub-key, an entry missing `name`, and a `checks` that lists
# no valid check are all errors -- a malformed or empty quality contract. Surface-
# only: no check is RUN, no pass/fail is computed, no status is derived from this
# (a later check bead); this states the governed contract and no more.
_CHECK_SUBKEYS = {"name", "description", "severity"}


def _checks(facets: dict, reasons: list[str]) -> list[dict] | None:
    """An approved source's `facets.checks`, or `None` when it declares none.
    Absent/null stores nothing; a non-list, a non-mapping entry, an unknown
    sub-key, an entry missing `name`, a blank `name`/`description`/`severity`, or a
    `checks` that lists no valid check is an error -- a malformed or empty
    data-quality contract must not ride through as if it were a governed one."""
    value = facets.get("checks")
    if value is None:
        return None
    if not isinstance(value, list):
        reasons.append("approved_sources facets checks must be a list of checks")
        return None
    before = len(reasons)
    parsed: list[dict] = []
    for entry in value:
        if not isinstance(entry, dict):
            reasons.append("approved_sources facets checks entries must be mappings")
            continue
        _reject_unknown(entry, "approved_sources facets checks", _CHECK_SUBKEYS, reasons)
        name = _string(entry, "name", reasons, required=True)
        check: dict = {}
        if name is not None:
            check["name"] = name
        description = _string(entry, "description", reasons)
        if description is not None:
            check["description"] = description
        severity = _string(entry, "severity", reasons)
        if severity is not None:
            check["severity"] = severity
        # An entry is kept only when it carries the required `name`; one missing it
        # already recorded its own error above and must not ride through nameless.
        if name is not None:
            parsed.append(check)
    if not parsed:
        # Only say "lists no checks" when no more specific reason was already
        # recorded -- a malformed entry (missing/blank name, unknown key) names its
        # own error and must not be recharacterized as an empty declaration. An
        # empty list records nothing above, so it falls to this reason.
        if len(reasons) == before:
            reasons.append("approved_sources facets checks lists no checks")
        return None
    return parsed


def _source_facets(entry: dict, reasons: list[str]) -> dict | None:
    """Parse an approved source's `facets`, or `None` when it declares none.

    Surfaced (hy-gp99, 284-3): the parsed facets ride into the served bundle via
    `git_instructions`. Absent `facets` is today's behaviour and adds nothing to
    the source, so a source without facets is byte-identical to before.

    A facet that is absent or null stores nothing (as every optional string in
    this module treats a null value); a facet that is PRESENT but blank is an
    error, an unknown `classification` value is an error, a malformed or empty
    `freshness`/`lineage`/`checks` contract is an error, and any facet sub-key
    other than `grain`/`classification`/`freshness`/`lineage`/`checks` is an error
    -- the ways a customer states something malformed, as opposed to stating
    nothing.
    """
    facets = entry.get("facets")
    if facets is None:
        return None
    if not isinstance(facets, dict):
        reasons.append("approved_sources facets must be a mapping")
        return None
    _reject_unknown(facets, "approved_sources facets", _APPROVED_SOURCE_FACETS, reasons)
    parsed: dict = {}
    grain = _grain(facets, "grain", reasons, "approved_sources facets grain")
    if grain is not None:
        parsed["grain"] = grain
    classification = _classification(facets, reasons)
    if classification is not None:
        parsed["classification"] = classification
    freshness = _freshness(facets, reasons)
    if freshness is not None:
        parsed["freshness"] = freshness
    lineage = _lineage(facets, reasons)
    if lineage is not None:
        parsed["lineage"] = lineage
    checks = _checks(facets, reasons)
    if checks is not None:
        parsed["checks"] = checks
    return parsed or None


def _approved_sources(manifest: dict, reasons: list[str]) -> list[dict]:
    sources = []
    for entry in _mappings(manifest, "approved_sources", reasons):
        _reject_unknown(
            entry, "approved_sources", {"ref", "role", "reason", "bi_override", "facets"}, reasons
        )
        ref = _string(entry, "ref", reasons, required=True)
        role = _string(entry, "role", reasons, required=True)
        if ref is None or role is None:
            continue
        source = {"ref": ref, "role": role}
        reason = _string(entry, "reason", reasons)
        if reason:
            source["reason"] = reason
        override = _bi_override(entry, reasons)
        if override:
            source["bi_override"] = override
        facets = _source_facets(entry, reasons)
        if facets:
            source["facets"] = facets
        sources.append(source)
    return sources


def _prohibited_sources(manifest: dict, reasons: list[str]) -> list[dict]:
    sources = []
    for entry in _mappings(manifest, "prohibited_sources", reasons):
        _reject_unknown(entry, "prohibited_sources", {"ref", "reason", "bi_override"}, reasons)
        ref = _string(entry, "ref", reasons, required=True)
        # A prohibition without a stated reason is guidance an agent cannot
        # explain to a human, so it is not accepted.
        reason = _string(entry, "reason", reasons, required=True)
        if ref is None or reason is None:
            continue
        source = {"ref": ref, "reason": reason}
        override = _bi_override(entry, reasons)
        if override:
            source["bi_override"] = override
        sources.append(source)
    return sources


def _bi_override(entry: dict, reasons: list[str]) -> dict | None:
    value = entry.get("bi_override")
    if value is None:
        return None
    if not isinstance(value, dict):
        reasons.append("bi_override must be a mapping")
        return None
    _reject_unknown(value, "bi_override", {"ref", "reason"}, reasons)
    ref = _string(value, "ref", reasons, required=True)
    reason = _string(value, "reason", reasons, required=True)
    if ref is None:
        return None
    parsed = parse_evidence_ref(ref, reasons)
    if parsed is None:
        return None
    if (parsed.connector, parsed.asset_type) != ("superset", "dataset"):
        reasons.append(
            f"bi_override ref {ref!r} must identify a Superset dataset, not "
            f"{parsed.connector}:{parsed.asset_type}"
        )
        return None
    if reason is None:
        return None
    return {"ref": ref, "reason": reason}


def _fields(manifest: dict, reasons: list[str]) -> list[dict]:
    fields = []
    for entry in _mappings(manifest, "fields", reasons):
        _reject_unknown(entry, "fields", {"name", "source_ref", "expression"}, reasons)
        name = _string(entry, "name", reasons, required=True)
        source_ref = _string(entry, "source_ref", reasons, required=True)
        expression = _string(entry, "expression", reasons, required=True)
        if name is None or source_ref is None or expression is None:
            continue
        fields.append({"name": name, "source_ref": source_ref, "expression": expression})
    return fields


def _joins(manifest: dict, reasons: list[str]) -> list[dict]:
    joins = []
    for entry in _mappings(manifest, "joins", reasons):
        _reject_unknown(entry, "joins", {"from", "to", "type"}, reasons)
        left = _string(entry, "from", reasons, required=True)
        right = _string(entry, "to", reasons, required=True)
        join_type = _string(entry, "type", reasons, required=True)
        if left is None or right is None or join_type is None:
            continue
        joins.append({"from": left, "to": right, "type": join_type})
    return joins


# The value a manifest gives `evals` to declare, honestly, that the domain has
# NO eval bank yet (hy-gh-287, GitHub #287). Omitting the key or leaving it null
# reads the same way. Reserved: a context path may not name a file `none` and
# point `evals` at it -- the sentinel wins, because the whole point is that "no
# bank" is a stated fact, not a filename.
EVALS_NONE = "none"


def is_unevaluated(normalized: dict) -> bool:
    """Whether a domain declared no eval bank (hy-gh-287).

    True exactly when the domain has no eval cases: `evals: none`, an omitted
    key, or a null value all normalize to `{"path": None, "cases": []}`. A domain
    WITH a real bank always has at least one case, because a manifest that names
    an eval file holding zero cases is refused at parse time -- so the two states
    never overlap and this predicate is total. The single source of truth the
    catalog and the bundle both read, so "unevaluated" is defined once and cannot
    drift between the discovery surface and the resolved answer.
    """
    return not (normalized.get("evals") or {}).get("cases")


def _evals(manifest: dict, files: dict[str, str], reasons: list[str]) -> dict:
    """Parse the eval bank, or record that the domain declares none (hy-gh-287).

    `evals: none` -- or omitting the key, or a null value -- is an HONEST
    declaration that a domain has no eval bank yet. It validates and syncs, and
    the domain is disclosed as unevaluated downstream (`is_unevaluated`). This is
    DISTINCT from naming a file that holds ZERO cases, which stays a hard error:
    a human who pointed `evals` at an empty file made a mistake, not a
    declaration. The two must not collapse, for the same reason `ref_awaiting_sync`
    and `ref_not_observed` do not (see the note at the top of this module): a
    fabricated or empty bank that passes forever is exactly the laundered
    uncertainty this distinction exists to prevent.
    """
    raw = manifest.get("evals")
    if raw is None or (isinstance(raw, str) and raw.strip().casefold() == EVALS_NONE):
        return {"path": None, "cases": []}
    document = _referenced_file(manifest, "evals", files, reasons, required=True)
    path = manifest.get("evals")
    parsed = {"path": path, "cases": []}
    if document is None:
        return parsed
    try:
        loaded = yaml.safe_load(document)
    except yaml.YAMLError as exc:
        reasons.append(f"{path} is not valid YAML: {exc}")
        return parsed
    if not isinstance(loaded, dict) or loaded.get("schema_version") != SCHEMA_VERSION:
        reasons.append(f"{path} must be a mapping with schema_version {SCHEMA_VERSION}")
        return parsed
    for entry in _mappings(loaded, "cases", reasons):
        name = _string(entry, "name", reasons, required=True)
        question = _string(entry, "question", reasons, required=True)
        expected = entry.get("expected")
        if not isinstance(expected, dict) or not expected:
            reasons.append(f"{path} case {name!r} needs a non-empty expected mapping")
            continue
        if name is None or question is None:
            continue
        parsed["cases"].append(
            {
                "name": name,
                "question": question,
                "critical": bool(entry.get("critical", False)),
                "expected": expected,
            }
        )
    if not parsed["cases"]:
        reasons.append(f"{path} declares no evaluation cases")
    return parsed


# --------------------------------------------------------------------------
# Governed and evidence refs
# --------------------------------------------------------------------------


def parse_governed_source_ref(raw: str, reasons: list[str]) -> str | None:
    """Validate a durable Git-owned table or pipeline identity.

    The middle component names the system that owns the code-level object;
    the remainder is its stable native name and may itself contain colons.
    """
    parts = raw.split(":", 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        reasons.append(f"source ref {raw!r} must be '<table|pipeline>:<platform>:<external_id>'")
        return None
    kind, platform, external_id = (part.strip() for part in parts)
    if kind not in GOVERNED_SOURCE_KINDS:
        reasons.append(
            f"source ref {raw!r} names unsupported kind {kind!r}; "
            f"expected one of {', '.join(GOVERNED_SOURCE_KINDS)}"
        )
        return None
    if any(char.isspace() for char in platform):
        reasons.append(f"source ref {raw!r} platform must not contain whitespace")
        return None
    return f"{kind}:{platform}:{external_id}"


def parse_evidence_ref(
    raw: str, reasons: list[str], *, coded: list[dict] | None = None
) -> EvidenceRef | None:
    """Parse a connector-native evidence ref used by directives or BI overrides.

    `coded` collects the same refusals as `{code, ref, message}`, for the
    caller that has to serve them to an agent rather than to a person -- the
    same record `EvidenceResolution.findings` carries, so a caller can merge
    the two without reshaping either. The code is assigned here, where the
    reason is known: a caller mapping prose back to a code would be grepping
    English, which is what carrying the code exists to avoid. Every refusal
    here is `ref_malformed` -- a ref that is the wrong shape, names an unknown
    connector, or names an asset type that connector does not have is wrong in
    the directive itself, and the caller fixes it by editing what it sent.
    """

    def refuse(message: str) -> None:
        reasons.append(message)
        if coded is not None:
            coded.append({"code": REF_MALFORMED, "ref": raw, "message": message})

    parts = raw.split(":", 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        refuse(f"evidence ref {raw!r} must be '<connector>:<asset_type>:<external_id>'")
        return None
    connector, asset_type, external_id = (part.strip() for part in parts)
    if connector not in EVIDENCE_ASSET_TYPES:
        refuse(f"evidence ref {raw!r} names unknown connector {connector!r}")
        return None
    if asset_type not in EVIDENCE_ASSET_TYPES[connector]:
        refuse(f"evidence ref {raw!r} names unknown {connector} asset type {asset_type!r}")
        return None
    return EvidenceRef(connector=connector, asset_type=asset_type, external_id=external_id)


def _governed_refs(normalized: dict) -> list[str]:
    """Every durable source identity declared by the manifest or its evals."""
    raws = []
    for source in normalized["approved_sources"] + normalized["prohibited_sources"]:
        raws.append(source["ref"])
    for item in normalized["fields"]:
        raws.append(item["source_ref"])
    for case in normalized["evals"]["cases"]:
        raws.extend(_expected_refs(case["expected"]))
    return raws


def _expected_refs(expected) -> list[str]:
    found: list[str] = []
    if isinstance(expected, dict):
        for key, value in expected.items():
            if key.endswith("_ref") and isinstance(value, str):
                found.append(value)
            elif key.endswith("_refs") and isinstance(value, list):
                found.extend(item for item in value if isinstance(item, str))
            else:
                found.extend(_expected_refs(value))
    elif isinstance(expected, list):
        for item in expected:
            found.extend(_expected_refs(item))
    return found


def _collect_refs(normalized: dict, reasons: list[str]) -> list[EvidenceRef]:
    for raw in _governed_refs(normalized):
        parse_governed_source_ref(raw, reasons)

    seen: dict[str, EvidenceRef] = {}
    for source in normalized["approved_sources"] + normalized["prohibited_sources"]:
        override = source.get("bi_override")
        if override is None:
            continue
        parsed = parse_evidence_ref(override["ref"], reasons)
        if parsed is not None:
            linked = EvidenceRef(
                connector=parsed.connector,
                asset_type=parsed.asset_type,
                external_id=parsed.external_id,
                governed_source_ref=source["ref"],
            )
            seen[linked.ref] = linked
    return [seen[key] for key in sorted(seen)]


def _check_ref_conflicts(normalized: dict, reasons: list[str]) -> None:
    approved = [source["ref"] for source in normalized["approved_sources"]]
    prohibited = [source["ref"] for source in normalized["prohibited_sources"]]
    for label, refs in (("approved_sources", approved), ("prohibited_sources", prohibited)):
        duplicates = {ref for ref in refs if refs.count(ref) > 1}
        for ref in sorted(duplicates):
            reasons.append(f"{label} lists {ref!r} more than once")
    for ref in sorted(set(approved) & set(prohibited)):
        # Ambiguous source identity: an agent cannot be told to both use and
        # avoid the same dataset, and Hyperset must not pick a side.
        reasons.append(f"{ref!r} is both approved and prohibited")
    declared_sources = set(approved)
    for item in normalized["fields"]:
        if declared_sources and item["source_ref"] not in declared_sources:
            reasons.append(
                f"field {item['name']!r} reads {item['source_ref']!r}, "
                "which is not an approved source"
            )
