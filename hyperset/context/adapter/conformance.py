"""Adapter conformance kit (epic #283, hy-7oe6, slice 283-9).

Given a customer corpus and its context-adapter.yaml, ASSERT the adapter's
projection is well-behaved:

  1. STABLE      -- the same corpus+adapter yields byte-identical normalized
                    output and the same `resolution.projection` on every run.
  2. FULLY MAPPED -- no corpus key is silently dropped (an unmapped key is an
                    apply error, surfaced here as a conformance failure).
  3. ATTRIBUTED  -- every field the adapter DERIVES (authors, via an authoring
                    transform) is declared in `fields_derived` WITH a
                    `reviewed_by`; nothing is authored anonymously.
  4. V0-VALID    -- the projected output satisfies the existing v0 schema (it
                    passed `parse_context`, which apply runs).
  5. LOSSLESS-OR-DECLARED -- a content field the adapter projects through a
                    LOSSY transform is declared in `fields_lossy`; a lossy
                    projection that does not say so fails.

FORK-AGNOSTIC: this checks DISCLOSURE, ATTRIBUTION, and DETERMINISM. It does NOT
decide what an unreviewed derived field means for the governed STATUS -- that is
Brandon's fork 3 (283-6), and nothing here depends on it. The kit is a
test/CI harness, not a served surface: it imports the adapter apply/projection
already shipped and adds no field to any bundle, so it moves neither
`SCHEMA_VERSION` nor `tools_hash`.

Why a transform-usage oracle rather than trusting the projection's own lists:
the served `resolution.projection` lists are empty by construction today (283-4
apply refuses unmapped keys and the current transforms author nothing), so a kit
that only re-read those lists would pass every adapter hollowly. Instead the kit
reads the adapter's map expressions INDEPENDENTLY, classifies each transform, and
holds the projection's declarations to what the adapter actually did. That makes
every negative arm fire on a real bad adapter, and it is exactly the guard a
populated fork-3 projection will need.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import yaml

from hyperset.context.adapter.apply import (
    AdapterApplyError,
    adapter_projection,
    apply_adapter,
    has_adapter,
)
from hyperset.context.adapter.schema import ADAPTER_FILE, AdapterSpec, Mapping, parse_adapter

# A transform that LOSES information about a content field: its output cannot
# reconstruct the source text. `slug` lowercases and collapses separators;
# `one_line` collapses every whitespace run. Applied to a human-readable field
# (a title, a statement) this drops what the customer wrote, so it must be
# disclosed in `fields_lossy`. `catalog_urn_to_source_ref` also drops a URN
# segment, but it is a reshape of an IDENTIFIER (like a slugged domain), not a
# content field, and no current v0 map target is projected through it; when a
# later slice maps a source-ref field through it, revisit this set for that field.
LOSSY_TRANSFORMS = frozenset({"slug", "one_line"})

# A transform that AUTHORS a value rather than translating one: `default` returns
# a literal the customer did not write when the source is blank or absent. A
# field produced through it is DERIVED and must carry a human reviewer.
AUTHORING_TRANSFORMS = frozenset({"default"})

# The identity field. Slugging an identifier to its canonical form is what the
# domain IS, not a lossy copy of some other field, so `slug` on `domain` is not a
# lossy-content drop. Lossy classification therefore skips the identity field --
# the standard `$.urn | slug` domain mapping is conformant.
_IDENTITY_FIELD = "domain"


@dataclass
class ConformanceResult:
    """The verdict. `passed` is true only when `failures` is empty. `checks`
    names every arm that RAN, so a caller can see the kit was not vacuous."""

    passed: bool
    failures: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


def run_conformance(files: dict[str, str]) -> ConformanceResult:
    """Run the conformance kit over one corpus (`files`) carrying a
    context-adapter.yaml. Returns a `ConformanceResult`; never raises for a
    non-conformant adapter -- non-conformance is data, reported in `failures`."""
    checks = [
        "stable-projection",
        "fully-mapped",
        "derived-attributed",
        "v0-valid",
        "lossless-or-declared",
    ]
    failures: list[str] = []

    # Fail closed on misuse: a corpus with no context-adapter.yaml has nothing to
    # conform, and applying it would raise. Report it as a failure rather than
    # crash, so the kit "never raises for a non-conformant input" holds literally.
    if not has_adapter(files):
        failures.append("no context-adapter.yaml in the corpus: nothing to conform")
        return ConformanceResult(passed=False, failures=failures, checks=checks)

    # Arms 2 (fully-mapped) and 4 (v0-valid): apply projects through the adapter
    # and hands the output to the v0 parser. An unmapped key, a failed transform,
    # or a v0 violation is an `AdapterApplyError` -- a conformance failure, not a
    # crash.
    try:
        first = apply_adapter(files)
    except AdapterApplyError as exc:
        failures.append(f"apply failed (fully-mapped/v0-valid): {'; '.join(exc.reasons)}")
        return ConformanceResult(passed=False, failures=failures, checks=checks)

    # Arm 1 (stable): a second apply must produce byte-identical normalized output
    # and the same projection. Compared through a canonical dump so key ORDER can
    # never mask a difference or invent one.
    second = apply_adapter(files)
    if _canonical(first.normalized) != _canonical(second.normalized):
        failures.append("unstable: normalized output differs between two runs of the same input")
    projection = adapter_projection(files)
    if adapter_projection(files) != projection:
        failures.append(
            "unstable: resolution.projection differs between two runs of the same input"
        )

    # Arms 3 (derived-attributed) and 5 (lossless-or-declared): read the adapter's
    # transform usage independently and hold the projection to it.
    spec = parse_adapter(files[ADAPTER_FILE])
    failures.extend(_disclosure_failures(spec, projection or {}))

    return ConformanceResult(passed=not failures, failures=failures, checks=checks)


def _disclosure_failures(spec: AdapterSpec, projection: dict) -> list[str]:
    """The attribution and lossy-disclosure arms, as pure checks over the spec's
    transform usage and the projection's declared lists. Separated so a crafted
    projection can exercise each branch directly in a unit test."""
    failures: list[str] = []
    declared_lossy = _declared_fields(projection.get("fields_lossy", []))
    derived_entries = _derived_by_field(projection.get("fields_derived", []))

    for label in sorted(_fields_using(spec, LOSSY_TRANSFORMS)):
        if label == _IDENTITY_FIELD:
            continue
        if label not in declared_lossy:
            failures.append(
                f"lossy-undeclared: {label!r} is projected through a lossy transform "
                "but is not declared in fields_lossy"
            )

    for label in sorted(_fields_using(spec, AUTHORING_TRANSFORMS)):
        entry = derived_entries.get(label)
        if entry is None:
            failures.append(
                f"unattributed-derived: {label!r} is authored by an adapter transform "
                "but is not declared in fields_derived"
            )
        elif not (isinstance(entry, dict) and str(entry.get("reviewed_by", "")).strip()):
            failures.append(
                f"unattributed-derived: {label!r} is in fields_derived without a reviewed_by"
            )
    return failures


def _mappings(spec: AdapterSpec) -> Iterator[tuple[str, Mapping]]:
    """Each v0 field the adapter maps, with the mapping that produces it."""
    yield "domain", spec.domain
    yield "title", spec.title
    if spec.owners is not None:
        yield "owners", spec.owners
    if spec.definitions is not None:
        yield "definitions.term", spec.definitions.term
        yield "definitions.statement", spec.definitions.statement


def _fields_using(spec: AdapterSpec, names: frozenset[str]) -> set[str]:
    """The v0 field labels whose transform chain uses any transform in `names`."""
    return {
        label
        for label, mapping in _mappings(spec)
        if any(step.name in names for step in mapping.steps)
    }


def _declared_fields(entries: list) -> set[str]:
    """The field labels a projection list declares, whether its entries are bare
    strings or `{field: ...}` mappings (the shape a populated fork carries)."""
    labels: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            labels.add(entry)
        elif isinstance(entry, dict) and entry.get("field"):
            labels.add(entry["field"])
    return labels


def _derived_by_field(entries: list) -> dict[str, object]:
    """`fields_derived` indexed by field label, so the attribution check can find
    an entry (and inspect its `reviewed_by`) for a field the adapter authored."""
    indexed: dict[str, object] = {}
    for entry in entries:
        if isinstance(entry, str):
            indexed[entry] = entry
        elif isinstance(entry, dict) and entry.get("field"):
            indexed[entry["field"]] = entry
    return indexed


def _canonical(normalized: dict) -> str:
    """A deterministic dump: sorted keys, so two equal projections compare equal
    regardless of insertion order, and a real difference always shows."""
    return yaml.safe_dump(normalized, sort_keys=True)
