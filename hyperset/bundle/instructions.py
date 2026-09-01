"""The Git snapshot's instructions, projected -- and nothing else.

These three functions read a `ContextSnapshot.normalized` dict and copy fields
out of it. They merge nothing with observation, resolve nothing, and touch no
repository, which is exactly why they can be a leaf both the resolver (the
governed answer) and `hyperset.bundle.gather` (the ungoverned ranking) depend
on without either depending on the other. They lived in `resolver.py` until the
gather producer needed the same projection; `resolver` re-exports them so its
existing importers are unchanged.
"""

from __future__ import annotations


def git_instructions(normalized: dict) -> dict:
    """Copied from the Git snapshot, never merged with observation. `checks`
    is the manifest's own word for what the contract calls `validations`."""
    return {
        "definitions": normalized.get("definitions", []),
        # Per-source `facets` (hy-gh-284 slice 1: `facets.grain`) is surfaced as
        # stored (hy-gp99, 284-3): a source that declares a grain carries it into
        # the served instructions, so a caller sees the grain a source is
        # aggregated at, not only the domain's. This EXPOSES the stored facet and
        # decides nothing -- refine-vs-replace against the domain grain is fork 2,
        # the fan-out check at 284-4. A source that declared no facets grows no
        # `facets` key here (`_source_facets` stored none), so it is byte-identical
        # to before this bead -- the surfacing is additive, and it moved
        # SCHEMA_VERSION.
        "approved_sources": normalized.get("approved_sources", []),
        "fields": normalized.get("fields", []),
        "filters": normalized.get("filters", []),
        "joins": normalized.get("joins", []),
        "grain": normalized.get("grain"),
        "caveats": normalized.get("caveats", []),
        "validations": normalized.get("checks", []),
        "prohibited_sources": normalized.get("prohibited_sources", []),
        "context_doc": (normalized.get("documents", {}).get("context_doc") or {}).get("text"),
    }


def concept_terms(instructions: dict) -> list[str]:
    """The domain's declared concept terms, in declaration order. One
    expression, read by the catalog that lists them and the coverage check
    that verifies a claim against them, so a caller cannot be refused for
    naming exactly what it was shown."""
    return [definition["term"] for definition in instructions["definitions"]]


def _source_refs(source: dict) -> tuple[str, ...]:
    """The durable source plus an explicit BI object governed as its override."""
    override = source.get("bi_override")
    if override is None:
        return (source["ref"],)
    return source["ref"], override["ref"]
