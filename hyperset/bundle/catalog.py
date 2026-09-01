"""The discovery surface a planner reads before it asks for anything
(hy-x7f), GitHub #70's context catalog, bounded (hy-aq3).

Progressive disclosure: an agent cannot name exact domains and refs it has
never seen, and sending the whole corpus to the strong model is what #70
forbids. So this is the cheap middle -- what governed context exists, in
identifiers and titles, with counts instead of contents for the observed
side.

Bounded for the same reason it exists. A listing whose size is a property of
the customer's repository is not a discovery surface; twenty domains with two
hundred definitions each would be exactly the packet this module was added to
avoid. Every list is capped, every cap is disclosed with the full count
beside it, and paging is positional over the ordering the catalog already
uses. Nothing is scored, ranked, sampled, or judged to decide what survives a
cap -- that would re-introduce the relevance logic #70 removed. Which entries
you get is a function of position and nothing else.

It carries no meaning. Definitions appear here as terms, never as their
text; sources appear as refs, never as their fields, expressions, filters,
or caveats. A caller that wants governed meaning resolves a bundle, which is
the only shape that carries authority, provenance, and the warnings that go
with them -- and which serves a domain's governed lists whole. If the catalog
ever answers a question on its own it has become a second, unprovenanced
context surface.

Nothing here infers or ranks. Every line is a fact already stored: the pinned
Git snapshot's own declarations and a `GROUP BY` over live observed assets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hyperset.bundle.resolver import concept_terms, git_instructions, projection_summary
from hyperset.bundle.schema import SCHEMA_VERSION
from hyperset.context.schema import is_unevaluated
from hyperset.db.base import utcnow
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresContextRepository,
    PostgresObservedAssetRepository,
)
from hyperset.repositories.scope import resolve_workspace_scope

# Domains per page. This is the caller's one knob and it bounds one axis:
# how many domains a page carries.
DEFAULT_LIMIT = 50

# A caller cannot lift the cap to infinity: an unbounded catalog is what this
# module is bounded against, whoever asks for it. The bounds live here and
# nowhere else -- the transports import them rather than restating them, so
# there is one number to change and no second copy to drift.
MIN_LIMIT = 1
MAX_LIMIT = 500
MIN_OFFSET = 0

# Entries per list inside a domain. Fixed, and deliberately not the caller's
# to raise: while `limit` governed both axes the response was O(limit^2), and
# a second caller-supplied knob would leave it quadratic in caller input.
#
# The number is chosen against what the catalog is FOR, not against corpus
# evidence -- this repository has one real manifest and every list in it is
# single digit, which would justify any bound from 2 upward and so justifies
# none. A preview big enough to look sufficient gets planned from, and a
# planner choosing from a positionally-truncated subset is relevance decided
# by omission. So: big enough to recognise a domain, deliberately too small
# to plan from. `page.recovery` says where the whole list comes from.
#
# Worst case is therefore arithmetic a reader can check rather than an
# adjective, and it has to count what a domain entry actually carries rather
# than what this constant governs. Per domain:
#
#     5 x INNER_LIMIT   `concepts`, `documents`, `approved_source_refs`,
#                       `owner_refs`, `evidence_refs`
#   + prohibitions      `prohibited_source_refs` is exempt (`UNBOUNDED_LISTS`)
#                       and grows with the manifest
#   + <= 8              `node_kinds`, a closed vocabulary
#   + <= 7              `relationships`, a closed vocabulary
#
# So 65 + prohibitions per domain, times `limit` domains, plus `observed` at
# no more than `limit`: 3,300 entries at the default 50 and 33,000 at the cap
# 500, in both cases before prohibitions. Prohibitions are the one unbounded
# term and that is deliberate -- a safety list that disappears to fit a limit
# is the failure this bound exists to avoid, not an oversight in this sum.
#
# `SEED_ONLY_LISTS` changes the shape of `evidence_refs` rather than its
# contribution: it is served whole under the bound and withheld past it, so
# never ten of two hundred, and its most is still `INNER_LIMIT`.
#
# Working default, not a validated one (hy-nr9): the first real corpora reach
# this code with the evaluator work, and that is where the number gets
# checked.
INNER_LIMIT = 10

# Lists a bound may never cut, whatever bound is added next. A caveat or a
# prohibition that disappears to fit a limit is the failure this project
# already refuses on the `context_budget` path, and prohibitions are small by
# nature, so exempting them costs nothing.
UNBOUNDED_LISTS = frozenset({"prohibited_source_refs"})

# Lists served whole or not at all, never in part. `evidence_refs` is the one
# list here whose only use is to seed a directive's `asset_refs` -- choosing
# where to look runs on the domain name, its title and its concepts. A partial
# list of seeds is therefore a list whose sole purpose is the one use it is
# unfit for: refs picked from the survivors of a positional cut are relevance
# decided by omission, and resolving with them returns only those. Asking the
# caller not to use it in prose is the asserted-invariant shape this project
# rejects; withholding it is the structural one, and costs the same.
SEED_ONLY_LISTS = frozenset({"evidence_refs"})

# Why an entry appears in `page.truncated`. One name carried both states after
# hy-74k -- cut to `INNER_LIMIT`, or withheld entirely -- distinguishable only
# by whether the key was present in the domain, which is structure asking the
# payload to be inspected before it can be read. They are separate reasons
# because a caller acts on them differently: a cut list has usable entries and
# more behind them, a withheld one has none here at all.
CUT = "cut"
WITHHELD = "withheld"

_RECOVERY = (
    f"page with 'offset' over domains (max {MAX_LIMIT} per page), or resolve the domain -- "
    f"resolve_analytics_context serves its governed lists whole, with authority and "
    f"provenance attached. A truncated 'evidence_refs' is withheld rather than cut, so "
    f"resolve without 'asset_refs' to get them all"
)


class CatalogBoundError(ValueError):
    """A `limit` or `offset` this catalog will not serve.

    Refused rather than clamped, and refused here rather than only at the
    transports: an in-process caller -- a script, another transport, a planner
    calling the service directly -- must get the same answer an agent gets,
    not a silently different page. `key`, `minimum` and `maximum` carry the
    rule so a caller can rebuild the message without parsing it.
    """

    def __init__(self, key: str, value: int, *, minimum: int, maximum: int | None = None):
        self.key = key
        self.value = value
        self.minimum = minimum
        self.maximum = maximum
        super().__init__(f"{key!r} must be {self.allowed()} or omitted, got {value!r}")

    def allowed(self) -> str:
        if self.maximum is None:
            return f"an integer >= {self.minimum}"
        return f"an integer between {self.minimum} and {self.maximum}"


@dataclass(frozen=True)
class ContextCatalog:
    """What exists, in enough shape to choose a starting point."""

    domains: list[dict]
    observed: list[dict]
    page: dict
    generated_at: datetime
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at.isoformat(),
            "page": self.page,
            "domains": self.domains,
            "observed": self.observed,
        }


def list_context_catalog(
    *, session_factory, limit: int = DEFAULT_LIMIT, offset: int = 0, workspace: str | None = None
) -> ContextCatalog:
    """List the governed domains and the observed source kinds. Read-only.

    `limit` caps how many domains a page carries and `offset` pages through
    them, both positionally. The lists inside each domain are capped at
    `INNER_LIMIT`, which the caller does not control, so one knob never
    governs two axes. What was cut is named in `page.truncated` with the full
    count in each domain's `counts`, so a partial catalog can never be read
    as a complete one.
    """
    if not MIN_LIMIT <= limit <= MAX_LIMIT:
        raise CatalogBoundError("limit", limit, minimum=MIN_LIMIT, maximum=MAX_LIMIT)
    if offset < MIN_OFFSET:
        raise CatalogBoundError("offset", offset, minimum=MIN_OFFSET)
    truncated: list[dict] = []

    sources = sorted(
        (
            source
            for source in PostgresContextRepository(session_factory).list_sources(
                workspace=workspace
            )
            if source.enabled and source.current_snapshot is not None
        ),
        key=lambda item: item.current_snapshot.domain,
    )
    page_sources = sources[offset : offset + limit]
    domains = [_domain(source, truncated=truncated) for source in page_sources]

    observed = _observed(session_factory, workspace)
    if len(observed) > limit:
        truncated.append({"list": "observed", "reason": CUT})
        observed = observed[:limit]

    served = offset + len(page_sources)
    return ContextCatalog(
        domains=domains,
        observed=observed,
        page={
            "limit": limit,
            "offset": offset,
            "domain_count": len(sources),
            # An HONEST aggregate (hy-gh-287): how many of the corpus's domains
            # declared no eval bank, over ALL domains rather than this page, so a
            # reader aggregating coverage never counts an unevaluated domain as a
            # measured one. Kept separate from `domain_count` for the same reason
            # `ref_awaiting_sync` is kept apart from `ref_not_observed` -- an
            # unmeasured domain is neither passing nor failing, and folding it in
            # either direction would launder that. `domain_count - this` is the
            # count that has an eval bank.
            "unevaluated_domain_count": sum(
                1 for source in sources if is_unevaluated(source.current_snapshot.normalized)
            ),
            # Where the next call starts, or null when this page was the
            # last one. A caller never has to compute it or guess whether
            # there is more.
            "next_offset": served if served < len(sources) else None,
            "truncated": truncated,
            "recovery": _RECOVERY if (truncated or served < len(sources)) else None,
        },
        generated_at=utcnow(),
    )


def _domain(source, *, truncated: list[dict]) -> dict:
    snapshot = source.current_snapshot
    instructions = git_instructions(snapshot.normalized)
    documents = [
        {"kind": kind, "path": document.get("path")}
        for kind, document in sorted(snapshot.normalized.get("documents", {}).items())
    ]
    # Terms and refs only. What each one means is governed context, and
    # governed context is served with its authority attached.
    lists = {
        # Shared with the coverage check a directive's 'concepts' is verified
        # against (hy-9lct): the terms a planner is shown here are the exact
        # terms it may claim, and one expression keeps that true.
        "concepts": concept_terms(instructions),
        "documents": documents,
        "approved_source_refs": [item["ref"] for item in instructions["approved_sources"]],
        "prohibited_source_refs": [item["ref"] for item in instructions["prohibited_sources"]],
        # The refs a directive can seed with: the ones the Git commit declared
        # AND a connector has observed. A declared ref with no observation
        # behind it is on the snapshot as a finding (ADR 0017) and seeding
        # with it would return nothing, so it is not offered here. The bundle
        # is where that gap is carried, as `linked_evidence.uncorroborated`.
        "evidence_refs": sorted(ref["ref"] for ref in snapshot.evidence_refs),
        # Any list drawn from the manifest belongs in here, not in the literal
        # below: cap, count and disclosure all follow from this dict, and a
        # list added outside it is served unbounded, uncounted and unable to
        # appear in `page.truncated` -- which is what happened to `owner_refs`
        # (hy-c7f). `projection_summary` adds two more lists further down that
        # inherit none of that, and they are safe only because both are closed
        # vocabularies; anything corpus-sized must come through here.
        "owner_refs": [owner["ref"] for owner in snapshot.owner_refs],
    }
    entry = {
        "domain": snapshot.domain,
        "title": snapshot.title,
        # Enough identity to see which commit the listing came from: a
        # planner that saw a concept here and cannot find it in the bundle is
        # looking at a domain that moved, not at a bug.
        "context_authority": {
            "type": "git",
            "repository": source.repository,
            "ref": source.ref,
            "path": source.path,
            "commit_sha": snapshot.commit_sha,
            "context_snapshot_id": snapshot.id,
        },
        # The full size of each list, whether or not this response carries
        # all of it: a count is cheap and a caller that sees 4 of 200 refs
        # knows to resolve the domain instead of planning from the 4.
        "counts": {name: len(values) for name, values in lists.items()}
        | {
            # `evidence_refs` is the one list narrowed by something other than
            # a bound, so its count is the one that has to be taken from
            # somewhere other than the served list: what the commit DECLARED.
            # Counting the survivors made the count agree with the omission,
            # so three declared refs and four with one uncorroborated were the
            # same response and nothing in it revealed the difference
            # (hy-bcim). The list stays filtered -- an uncorroborated ref is
            # still a seed that returns nothing -- and the gap between the two
            # is the disclosure.
            "evidence_refs": len(_declared_refs(snapshot)),
            # Eval coverage as a stated count, not an absence (hy-gh-287): a
            # domain that declared no eval bank shows `eval_cases: 0`, visibly
            # unevaluated rather than silently missing. The count is here, in
            # `counts`, for the same reason every other list size is -- a planner
            # that sees zero knows the domain is unmeasured, not that coverage
            # was omitted from the response.
            "eval_cases": len(snapshot.normalized.get("evals", {}).get("cases", [])),
        },
    }
    for name, values in lists.items():
        if name in UNBOUNDED_LISTS or len(values) <= INNER_LIMIT:
            entry[name] = values
            continue
        reason = WITHHELD if name in SEED_ONLY_LISTS else CUT
        truncated.append({"list": f"{snapshot.domain}.{name}", "reason": reason})
        if name in SEED_ONLY_LISTS:
            # Omitted rather than cut: a caller cannot seed from a list it was
            # not given, and `counts` plus `page.truncated` still say it
            # exists and how big it is.
            continue
        entry[name] = values[:INNER_LIMIT]
    entry.update(projection_summary(snapshot, instructions))
    return entry


def _declared_refs(snapshot) -> set[str]:
    """Every evidence ref the pinned commit names, corroborated or not.

    Two stored lists because ADR 0017 splits them by what resolution found:
    `evidence_refs` is declared AND observed, `evidence_findings` is declared
    and not. Their union is what the customer wrote.
    """
    return {ref["ref"] for ref in snapshot.evidence_refs} | {
        finding["ref"] for finding in snapshot.evidence_findings
    }


def _observed(session_factory, workspace: str | None = None) -> list[dict]:
    """Source kinds and how much of each, never the assets themselves. SCOPED to
    `workspace` (hq-t6nx): both the connection list and the asset counts are confined
    to the tenant, so one tenant's catalog never carries another's connection id,
    display name, connector, or count. A count keyed on a connection outside the
    workspace is DROPPED, never surfaced as `unknown` (which would still leak the id
    and count)."""
    scope = resolve_workspace_scope(workspace)
    connections = {
        connection.id: connection
        for connection in PostgresConnectionRepository(session_factory).list(workspace=scope)
    }
    entries = []
    for connection_id, asset_type, count in PostgresObservedAssetRepository(
        session_factory
    ).count_by_type(workspace=scope):
        connection = connections.get(connection_id)
        if connection is None:
            # A count for a connection not in this workspace belongs to another tenant
            # (or the count scope and connection scope momentarily disagree); never emit
            # a foreign connection id/count under this tenant's catalog.
            continue
        entries.append(
            {
                "connector": connection.connector_type,
                "connection_id": connection_id,
                "display_name": connection.display_name,
                "asset_type": asset_type,
                "live_count": count,
            }
        )
    return sorted(entries, key=lambda entry: (entry["connector"], entry["asset_type"]))
