"""Persisted `asset_relationships` rows project into `domain_graph` as OBSERVED
edges (#230 slice 7, hy-c6vx).

The resolver reads each in-scope observed asset's outgoing relationships and
emits `derived_from` as an observed `lineage_to` edge and `has_glossary_term` as
an observed `has_glossary_term` edge, `evidence: "observation"`, distinct from
every governed (`evidence: "git"`) edge. Neither observed name reuses a governed
string (ADR-0034 Decision 2): the retired governed `evidenced_by` is never
emitted. Only those two connector relations project; the rest are the connector's
own references and stay out of the graph.
"""

from __future__ import annotations

import pytest

from hyperset.bundle import ContextDirective, resolve_analytics_context
from hyperset.repositories.postgres import (
    PostgresConnectionRepository,
    PostgresObservedAssetRepository,
    PostgresSyncRepository,
)
from hyperset.repositories.scope import ALL_WORKSPACES
from tests.postgres.conftest import APPROVED_DATASET, GLOSSARY_TERM

APPROVED_REF = f"superset:dataset:{APPROVED_DATASET}"
APPROVED_SOURCE = "table:postgres:analytics.public.finance_orders_daily"
UPSTREAM_URN = "urn:li:dataset:(urn:li:dataPlatform:postgres,raw.orders,PROD)"


def _resolve(session_factory, **kwargs):
    return resolve_analytics_context(
        query="revenue by region",
        directive=ContextDirective(**kwargs),
        session_factory=session_factory,
    )


def _observe_upstream_dataset(session_factory):
    """One DataHub-observed upstream dataset to be the lineage target."""
    connections = PostgresConnectionRepository(session_factory)
    datahub = connections.create_or_update(connector_type="datahub", display_name="DataHub").id
    syncs = PostgresSyncRepository(session_factory)
    run = syncs.begin_run(datahub, mode="full")
    record, _ = PostgresObservedAssetRepository(session_factory).upsert(
        connection_id=datahub,
        external_id=UPSTREAM_URN,
        asset_type="dataset",
        sync_run_id=run.id,
        raw_payload={"urn": UPSTREAM_URN},
    )
    syncs.finish_run(run.id, counters={"created": 1})
    return record.id


def _asset_id(session_factory, *, connection_id, external_id, asset_type):
    return (
        PostgresObservedAssetRepository(session_factory)
        .get_by_external_id(
            connection_id=connection_id, external_id=external_id, asset_type=asset_type
        )
        .id
    )


@pytest.mark.postgres
def test_persisted_relationships_project_as_observed_edges(session_factory, revenue_slice):
    repo = PostgresObservedAssetRepository(session_factory)
    superset_connection = revenue_slice["connection_id"]
    datahub_connection = next(
        connection.id
        for connection in PostgresConnectionRepository(session_factory).list(
            workspace=ALL_WORKSPACES
        )
        if connection.connector_type == "datahub"
    )

    from_id = _asset_id(
        session_factory,
        connection_id=superset_connection,
        external_id=APPROVED_DATASET,
        asset_type="dataset",
    )
    term_id = _asset_id(
        session_factory,
        connection_id=datahub_connection,
        external_id=GLOSSARY_TERM,
        asset_type="glossary_term",
    )
    upstream_id = _observe_upstream_dataset(session_factory)

    repo.replace_relationships(
        declared={
            from_id: [
                ("derived_from", upstream_id),
                ("has_glossary_term", term_id),
                # An unmapped connector reference that must NOT project.
                ("queries", upstream_id),
            ]
        }
    )

    bundle = _resolve(
        session_factory,
        domains=["revenue"],
        concepts=["recognized_revenue"],
        asset_refs=[APPROVED_REF],
    )
    edges = bundle.domain_graph["edges"]

    lineage = [edge for edge in edges if edge["relation"] == "lineage_to"]
    glossary = [edge for edge in edges if edge["relation"] == "has_glossary_term"]
    assert lineage == [
        {
            "from": f"source:{APPROVED_SOURCE}",
            "to": f"source:datahub:dataset:{UPSTREAM_URN}",
            "relation": "lineage_to",
            "evidence": "observation",
        }
    ]
    assert glossary == [
        {
            "from": f"source:{APPROVED_SOURCE}",
            "to": f"source:datahub:glossary_term:{GLOSSARY_TERM}",
            "relation": "has_glossary_term",
            "evidence": "observation",
        }
    ]
    # Distinct from governed: neither observed relation is ever a git edge, and the
    # retired governed `evidenced_by` string is never emitted (ADR-0034 Decision 2).
    git_edges = [edge for edge in edges if edge["evidence"] == "git"]
    assert git_edges, "the governed projection is still present"
    assert all(edge["relation"] not in {"lineage_to", "has_glossary_term"} for edge in git_edges)
    assert not [edge for edge in edges if edge["relation"] == "evidenced_by"]
    # The unmapped `queries` reference did not project.
    assert not [edge for edge in edges if edge["relation"] == "queries"]
    # Each target became exactly one observed_source node.
    for target in (
        f"source:datahub:dataset:{UPSTREAM_URN}",
        f"source:datahub:glossary_term:{GLOSSARY_TERM}",
    ):
        matching = [node for node in bundle.domain_graph["nodes"] if node["id"] == target]
        assert len(matching) == 1 and matching[0]["kind"] == "observed_source"


@pytest.mark.postgres
def test_no_relationships_emits_no_observed_relationship_edges(session_factory, revenue_slice):
    bundle = _resolve(
        session_factory,
        domains=["revenue"],
        concepts=["recognized_revenue"],
        asset_refs=[APPROVED_REF],
    )
    edges = bundle.domain_graph["edges"]
    assert not [edge for edge in edges if edge["relation"] in {"lineage_to", "has_glossary_term"}]
