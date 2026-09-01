"""Gather the observed sources relevant to a miss, ranked, and say why (hy-1f9h).

Flywheel step 2. Step 1 records that a question resolved to nothing; this
gathers the observed sources a later step could author a candidate definition
from. It is the assist half of the resolver's refusal path (ADR 0022, hy-xq55)
lifted into a producer a flywheel step can call directly -- keyed on a miss's
semantic inputs (the domain and the concepts Git does not declare), not on a
`_Selection` reachable only down the synchronous `no_match` path.

It PROPOSES and never writes. Per ADR 0024 decision 4 it calls no writer:
- it never creates, deletes, or re-relates an observation
  (`PostgresObservedAssetRepository.upsert` / `mark_missing_deleted` /
  `replace_relationships` are `run_sync`'s alone),
- it never approves (`ReviewRepository.approve`) or proposes a governed version,
- it fabricates no deletion warrant (`ConnectorSnapshot.established_denominators`),
- it runs no warehouse SQL and does not read an asset body: every ranking signal
  is already in the store, so gathering references needs no live lookup (that is
  a READ deferred to when a later step needs the body, ADR 0024 decision 2).

The ranking itself is `hyperset.bundle.discovery.candidate_sources`, which takes
values and no session and already produces the assist-class shape ADR 0024
decision 3 names: `governance="observed"`, attributed `produced_by`, one
source-native `ref`, no slot a declared ref could occupy, and an `assist_id`
that is never folded into `bundle_id`.
"""

from __future__ import annotations

from hyperset.bundle.discovery import GovernedFacts, ObservedSource, candidate_sources
from hyperset.bundle.instructions import _source_refs, git_instructions
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresContextRepository,
    PostgresObservedAssetRepository,
    PostgresProcessorRepository,
)
from hyperset.repositories.scope import resolve_workspace_scope

# What a candidate source is drawn from. A plan names sources in
# `AnalyticsPlan.source_refs` and a dataset is what such a ref points at, so
# ranking databases, dashboards, users, or glossary terms alongside them would
# offer the caller things it cannot put in a plan. The bound is stated here
# rather than discovered from an empty result.
CANDIDATE_ASSET_TYPE = "dataset"


def gather(
    *, domain: str, undeclared: list[str], session_factory, workspace: str | None = None
) -> dict | None:
    """Rank the observed estate for one miss, or `None` when nothing is observed.

    `domain` is the miss's subject and `undeclared` the concepts Git does not
    cover for it. `domain` is configured when a source's snapshot declares it --
    the same distinction the resolver draws between its `uncovered` refusal (a
    real domain that does not declare the terms) and its `ungoverned` one (no
    source declares the domain at all, hy-xq55); a configured domain also has
    field expressions to read, an unconfigured one does not, which
    `candidate_sources` is told directly rather than inferring from an empty
    tuple.
    """
    sources = [
        source
        for source in PostgresContextRepository(session_factory).list_sources(workspace=workspace)
        if source.enabled and source.current_snapshot is not None
    ]
    named = next((source for source in sources if source.current_snapshot.domain == domain), None)
    observed = _observed_sources(session_factory, workspace=workspace)
    if not observed:
        return None
    return candidate_sources(
        domain=domain,
        undeclared=undeclared,
        sources=observed,
        governed=_governed_facts(sources, named=named),
        domain_is_configured=named is not None,
    )


def _observed_sources(session_factory, *, workspace: str | None = None) -> list[ObservedSource]:
    """Every observed dataset, flattened to the facts a rank is read from. SCOPED to
    `workspace` (hq-t6nx): the connector map and the asset scan are both confined to the
    tenant, so a gathered candidate never names another tenant's observed asset. An asset
    whose connection is outside the workspace is dropped (its connector resolves to None,
    the same rule already applied to an unknown connection)."""
    scope = resolve_workspace_scope(workspace)
    connectors = {
        connection.id: connection.connector_type
        for connection in PostgresConnectionRepository(session_factory).list(workspace=scope)
    }
    findings: dict[str, list[dict]] = {}
    for finding in PostgresProcessorRepository(session_factory).list_findings(state="current"):
        if finding.affected_asset_id is None:
            continue
        findings.setdefault(finding.affected_asset_id, []).append(
            {
                "finding_id": finding.id,
                "finding_type": finding.finding_type,
                "severity": finding.severity,
            }
        )

    assets = PostgresObservedAssetRepository(session_factory).list_all(
        asset_type=CANDIDATE_ASSET_TYPE, workspace=scope
    )
    referenced_by = _live_references(session_factory, assets, connectors)

    observed = []
    for asset in assets:
        connector = connectors.get(asset.connection_id)
        if connector is None:
            continue
        version = asset.current_version
        normalized = version.normalized if version else {}
        observed.append(
            ObservedSource(
                ref=f"{connector}:{asset.asset_type}:{asset.external_id}",
                connector=connector,
                asset_type=asset.asset_type,
                external_id=asset.external_id,
                asset_id=asset.id,
                connection_id=asset.connection_id,
                observed_version_id=version.id if version else None,
                source_modified_at=asset.source_modified_at,
                deleted_at=asset.deleted_at,
                metric_expressions=tuple(
                    str(metric.get("expression") or "")
                    for metric in normalized.get("metrics", [])
                    if metric.get("expression")
                ),
                findings=tuple(
                    sorted(findings.get(asset.id, []), key=lambda row: row["finding_id"])
                ),
                referenced_by=tuple(referenced_by.get(asset.id, ())),
            )
        )
    return observed


def _live_references(session_factory, assets, connectors: dict[str, str]) -> dict[str, list[dict]]:
    """What currently points at each candidate, keyed by asset id.

    One statement for the whole candidate set rather than one per candidate:
    `list_live_references` exists for exactly this read, so the docstring above
    keeps saying two bounded reads and means it.

    A referring asset whose connection is unknown is dropped, the same rule the
    candidates themselves are subject to -- a ref cannot be built without the
    connector type, and a reference the reader cannot look up is not evidence.
    """
    references: dict[str, list[dict]] = {}
    rows = PostgresObservedAssetRepository(session_factory).list_live_references(
        to_asset_ids=[asset.id for asset in assets]
    )
    for row in rows:
        connector = connectors.get(row.from_connection_id)
        if connector is None:
            continue
        references.setdefault(row.to_asset_id, []).append(
            {
                "ref": f"{connector}:{row.from_asset_type}:{row.from_external_id}",
                "asset_type": row.from_asset_type,
                "relation": row.relation,
            }
        )
    return references


def _governed_facts(sources, *, named) -> GovernedFacts:
    """What the whole configured corpus already says about these refs.

    Every configured domain contributes, not just the one the caller named:
    "some other domain already approves this source" is exactly the kind of
    partial agreement with Git that discovery exists to surface. The declared
    field expressions are the named domain's alone -- another domain's
    expression says nothing about the question asked here.

    `named` is None when the corpus could not resolve the caller's domain at
    all (hy-xq55), and then there is no domain to read field expressions from.
    The corpus-wide half still computes and is still worth serving; what it may
    not do there is carry a proposal, which `candidate_sources` is told about
    separately rather than inferring from an empty tuple -- a domain that
    declares no fields would look identical.
    """
    approved: dict[str, list[dict]] = {}
    evidence: dict[str, list[dict]] = {}
    prohibited: dict[str, list[dict]] = {}
    for source in sources:
        snapshot = source.current_snapshot
        instructions = git_instructions(snapshot.normalized)
        for entry in instructions["approved_sources"]:
            for ref in _source_refs(entry):
                approved.setdefault(ref, []).append(
                    {"domain": snapshot.domain, "role": entry["role"]}
                )
        for entry in instructions["prohibited_sources"]:
            for ref in _source_refs(entry):
                prohibited.setdefault(ref, []).append(
                    {"domain": snapshot.domain, "reason": entry["reason"]}
                )
    return GovernedFacts(
        approved={ref: tuple(entries) for ref, entries in approved.items()},
        evidence={ref: tuple(entries) for ref, entries in evidence.items()},
        prohibited={ref: tuple(entries) for ref, entries in prohibited.items()},
        field_expressions=(
            ()
            if named is None
            else tuple(git_instructions(named.current_snapshot.normalized)["fields"])
        ),
    )
