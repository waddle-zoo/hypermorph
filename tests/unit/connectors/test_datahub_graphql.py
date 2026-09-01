"""DataHub GraphQL transport and normalization unit tests (hy-gh-17).

Payloads come from the recorded pinned-v1.6.0 evidence via
`tests.fake_datahub`, so these assert against real response shapes rather
than hand-written GraphQL that nothing served.
"""

from __future__ import annotations

import pytest

from hyperset.connectors import Connector, ConnectorAuthError, ConnectorError
from hyperset.connectors.datahub import DataHubConnector, DataHubGraphQLClient
from hyperset.connectors.datahub.connector import (
    _HASH_BASIS,
    _normalize_dataset,
    _source_modified_at,
    projection_fingerprint,
    projections,
)
from hyperset.connectors.superset import SupersetConnector
from hyperset.repositories.hash_basis import apply_hash_basis
from tests.fake_datahub import BASE_URL, FakeDataHubSession, FakeResponse, manifest

_MANIFEST = manifest("baseline")
_WAREHOUSE_URN = next(
    urn for urn in _MANIFEST["urns_by_asset_type"]["dataset"] if "dataPlatform:postgres" in urn
)


def _hashed(payload: dict) -> dict:
    """What this connector's declared hash basis leaves for the content hash.
    Applied through the repository's replay function, not a connector-private
    projection, so these assertions cover the rule that is actually stored."""
    return apply_hash_basis(payload, _HASH_BASIS)


def _connector(session=None, **kwargs) -> DataHubConnector:
    return DataHubConnector(base_url=BASE_URL, session=session or FakeDataHubSession(), **kwargs)


# -- the seam the two real connectors proved they share ----------------------


@pytest.mark.parametrize(
    "connector",
    [
        DataHubConnector(base_url=BASE_URL, session=FakeDataHubSession()),
        SupersetConnector(base_url="http://superset.test:8088", username="u", password="p"),
    ],
    ids=["datahub", "superset"],
)
def test_both_v0_connectors_satisfy_the_connector_protocol(connector):
    # `Connector` is a Protocol, so nothing checks this at import time.
    assert isinstance(connector, Connector)
    assert connector.transport in {"graphql", "rest", "export_bundle"}


def test_datahub_has_exactly_one_transport():
    assert _connector().transport == "graphql"


def test_a_base_url_is_required():
    with pytest.raises(ConnectorError, match="requires base_url"):
        DataHubConnector(base_url="")


# -- transport --------------------------------------------------------------


def test_a_token_is_sent_as_a_bearer_header_and_never_stored_elsewhere():
    session = FakeDataHubSession()
    DataHubGraphQLClient(base_url=BASE_URL, token="s3cret", session=session)

    assert session.headers["Authorization"] == "Bearer s3cret"


def test_no_authorization_header_is_sent_when_no_token_is_configured():
    session = FakeDataHubSession()
    DataHubGraphQLClient(base_url=BASE_URL, session=session)

    assert "Authorization" not in session.headers


@pytest.mark.parametrize("status", [401, 403])
def test_an_unauthorized_read_raises_rather_than_reading_as_absence(status):
    session = FakeDataHubSession(status_overrides={"hypersetAppVersion": status})
    with pytest.raises(ConnectorAuthError, match=str(status)):
        _connector(session).snapshot()


def test_http_200_with_a_graphql_errors_array_is_still_a_failure():
    session = FakeDataHubSession(graphql_errors={"hypersetScrollEntities": "index unavailable"})
    with pytest.raises(ConnectorError, match="index unavailable"):
        _connector(session).snapshot()


def test_test_connection_reports_the_version_the_instance_disclosed():
    result = _connector().test_connection()

    assert result.ok is True
    assert "v1.6.0" in result.detail


def test_test_connection_fails_without_raising_when_the_instance_refuses():
    session = FakeDataHubSession(status_overrides={"hypersetAppVersion": 500})
    result = _connector(session).test_connection()

    assert result.ok is False


class _PagedScrollSession(FakeDataHubSession):
    """A scroll served over two pages, with one URN appearing on both.

    Not a hypothetical: `scrollAcrossEntities` reads a live search index, and
    a document reindexed between two cursor fetches can be served under both.
    The recorded fixture is a single page, so no recorded capture can exercise
    the boundary -- this stands in for the index's behaviour, not for a
    payload shape, and every entity it names is one the fixture already
    serves so the projections that follow are still recorded evidence.
    """

    def __init__(self, pages: list[list[str | tuple[str, str]]], **kwargs) -> None:
        super().__init__(**kwargs)
        # A row is a urn, or a (urn, type) pair where the type matters.
        self._pages = [
            [row if isinstance(row, tuple) else (row, "DATASET") for row in page] for page in pages
        ]
        self.scroll_ids: list[str | None] = []

    def post(self, url, *, json=None, timeout=None):
        body = json or {}
        if "hypersetScrollEntities" not in body.get("query", ""):
            return super().post(url, json=body, timeout=timeout)
        variables = body.get("variables") or {}
        self.scroll_ids.append(variables.get("scrollId"))
        index = len(self.scroll_ids) - 1
        self.operations.append("hypersetScrollEntities")
        return FakeResponse(
            200,
            {
                "data": {
                    "scrollAcrossEntities": {
                        "nextScrollId": (
                            f"cursor-{index + 1}" if index + 1 < len(self._pages) else None
                        ),
                        "count": len(self._pages[index]),
                        "searchResults": [
                            {"entity": {"urn": urn, "type": entity_type}}
                            for urn, entity_type in self._pages[index]
                        ],
                    }
                }
            },
        )


def test_a_paged_scroll_serving_one_urn_twice_yields_it_once():
    """Discovery is a set of entities, not a list of sightings (hy-dtpj).

    The duplicate is not a crash and not a wrong payload, which is why it
    survived: `snapshot()` calls `entity()` once per occurrence, so the same
    asset is fetched twice -- two reads whose payloads can differ if the
    source changed between them -- and `SyncResult.record_outcome` counts it
    twice, reporting five assets where four were read.
    """
    other = "urn:li:dataset:(urn:li:dataPlatform:postgres,other,PROD)"
    session = _PagedScrollSession([[_WAREHOUSE_URN, other], [_WAREHOUSE_URN]])
    client = DataHubGraphQLClient(base_url=BASE_URL, session=session)

    entities = client.scroll_entities()

    assert [entity["urn"] for entity in entities] == [_WAREHOUSE_URN, other]
    # And the second page was really fetched: a scroll that stopped early
    # would satisfy the assertion above without deduplicating anything.
    assert session.scroll_ids == [None, "cursor-1"]


def test_a_urn_repeated_inside_one_page_is_collapsed_too():
    """The guard is global to the scroll, not per page.

    The docstring's stated reason -- a document reindexed between two cursor
    fetches -- is narrower than what the code does, and a reader who took the
    reason for the rule would think a within-page repeat still yields twice.
    """
    session = _PagedScrollSession([[_WAREHOUSE_URN, _WAREHOUSE_URN]])

    entities = DataHubGraphQLClient(base_url=BASE_URL, session=session).scroll_entities()

    assert [entity["urn"] for entity in entities] == [_WAREHOUSE_URN]


def test_the_first_sightings_type_wins_when_a_later_one_disagrees():
    """What the discard actually does, stated as an assertion (hy-96zh).

    In DataHub's scheme the type is a function of the URN, so a divergence is
    the source contradicting itself and keeping the last sighting would be no
    more defensible than keeping the first. That makes this a decision rather
    than an accident, and it is worth pinning because the cost is real: a
    discarded PROJECTED type means the URN never enters that type's bucket, so
    it is missing from the per-type seen-set `run_sync` pre-seeds and a
    governed asset of that type is deletion-checked as gone. Somebody
    "fixing" this by keeping the last sighting would move that cost rather
    than remove it, and this test is what they would have to argue with.
    """
    session = _PagedScrollSession([[(_WAREHOUSE_URN, "DATASET")], [(_WAREHOUSE_URN, "CHART")]])

    entities = DataHubGraphQLClient(base_url=BASE_URL, session=session).scroll_entities()

    assert entities == [{"urn": _WAREHOUSE_URN, "type": "DATASET"}]


def test_a_row_with_no_usable_type_does_not_consume_the_urn():
    """The skip must not spend the identity it refused to record.

    A falsy `urn` or `type` is dropped before the seen set, so a later
    well-formed sighting of the same URN still lands. Had the skip added the
    URN anyway, one malformed row would erase a real entity from discovery --
    the failure mode this whole guard exists to prevent, arriving through the
    guard itself.
    """
    session = _PagedScrollSession([[(_WAREHOUSE_URN, "")], [(_WAREHOUSE_URN, "DATASET")]])

    entities = DataHubGraphQLClient(base_url=BASE_URL, session=session).scroll_entities()

    assert entities == [{"urn": _WAREHOUSE_URN, "type": "DATASET"}]


# -- snapshot disclosure ----------------------------------------------------


def test_snapshot_discloses_the_projection_as_the_boundary_of_losslessness():
    snapshot = _connector().snapshot()

    assert snapshot.transport == "graphql"
    assert snapshot.source_version == "v1.6.0"
    assert snapshot.source_capabilities["projection_fingerprint"] == projection_fingerprint()
    assert any(
        "lossless with respect to this connector's projections" in w for w in snapshot.warnings
    )


def test_the_fingerprint_covers_the_query_text_not_just_the_type_names(monkeypatch):
    from hyperset.connectors.datahub import connector as datahub_connector

    baseline = projection_fingerprint()
    assert {entity_type for entity_type, _, _, _ in projections()} == {
        "DATASET",
        "DOMAIN",
        "CORP_USER",
        "GLOSSARY_TERM",
    }

    # Narrow one projection by a single field. No entity type changed, so only
    # a digest over the query text itself can notice.
    spec = datahub_connector._SPECS["DOMAIN"]
    monkeypatch.setattr(spec, "query", spec.query.replace("description", ""))
    assert projection_fingerprint() != baseline


def test_an_entity_type_the_connector_does_not_project_is_reported_not_dropped(monkeypatch):
    session = FakeDataHubSession()
    original = session.post

    def post(url, *, json=None, timeout=None):
        response = original(url, json=json, timeout=timeout)
        body = response.json()
        if isinstance(body, dict) and "scrollAcrossEntities" in str(body.get("data")):
            results = body["data"]["scrollAcrossEntities"]["searchResults"]
            results.append({"entity": {"urn": "urn:li:chart:(superset,1)", "type": "CHART"}})
            # An instance holding one more entity reports one more: `count` is
            # min(requested, total) on the pinned build, measured (hy-6nit).
            # Left at the recording's 6 this double would serve a page no live
            # build produces -- 6 counted, 7 served.
            body["data"]["scrollAcrossEntities"]["count"] = len(results)
        return response

    monkeypatch.setattr(session, "post", post)
    snapshot = _connector(session).snapshot()

    assert any("does not project (CHART=1)" in w for w in snapshot.warnings)
    assert snapshot.checkpoint["unprojected_entity_types"] == {"CHART": 1}
    # And it is not in the covered set, so `run_sync` never deletion-checks it.
    assert "chart" not in snapshot.covered_asset_types


# -- normalization ----------------------------------------------------------


def test_the_urn_is_the_identity_and_the_payload_is_kept_whole():
    snapshot = _connector().snapshot()
    items = {item.external_id: item for item in _connector().normalize(snapshot)}

    warehouse = items[_WAREHOUSE_URN]
    assert warehouse.asset_type == "dataset"
    assert warehouse.raw_payload["urn"] == _WAREHOUSE_URN


def test_a_dataset_with_no_relationships_yields_no_links():
    item = _normalize_dataset({"urn": "urn:li:dataset:(x,y,PROD)"})

    assert item.links == []
    assert item.normalized["domain_urn"] is None
    assert item.normalized["owner_urns"] == []


def test_a_missing_urn_is_an_error_not_a_synthesized_key():
    with pytest.raises(ConnectorError, match="no urn"):
        _normalize_dataset({"properties": {"name": "orders"}})


def test_only_dataset_lineage_edges_become_derived_from_links():
    item = _normalize_dataset(
        {
            "urn": "urn:li:dataset:(x,y,PROD)",
            "upstreams": {
                "relationships": [
                    {
                        "type": "DownstreamOf",
                        "entity": {"urn": "urn:li:dataset:(a,b,PROD)", "type": "DATASET"},
                    },
                    {
                        "type": "DownstreamOf",
                        "entity": {"urn": "urn:li:dataJob:(c,d)", "type": "DATA_JOB"},
                    },
                ]
            },
        }
    )

    assert item.normalized["upstream_dataset_urns"] == ["urn:li:dataset:(a,b,PROD)"]
    assert [link.relation for link in item.links] == ["derived_from"]


@pytest.mark.parametrize(
    ("time_ms", "expected_year"),
    [(0, None), (-1, None), (None, None), (1753600000000, 2025)],
)
def test_a_zero_audit_stamp_never_becomes_a_modification_time(time_ms, expected_year):
    result = _source_modified_at({"properties": {"lastModified": {"time": time_ms}}})

    assert (result.year if result else None) == expected_year


def test_ingestion_bookkeeping_is_excluded_from_the_hash_basis_at_any_depth():
    payload = {"urn": "u", "lastIngested": 1, "nested": [{"lastIngested": 2, "keep": "yes"}]}

    assert _hashed(payload) == {"urn": "u", "nested": [{"keep": "yes"}]}


def test_reordered_custom_properties_are_not_a_change():
    # Observed against the pinned instance: re-seeding identical content
    # returned the same entries in a different order.
    served_one = {
        "properties": {"customProperties": [{"key": "b", "value": "2"}, {"key": "a", "value": "1"}]}
    }
    served_two = {
        "properties": {"customProperties": [{"key": "a", "value": "1"}, {"key": "b", "value": "2"}]}
    }

    assert _hashed(served_one) == _hashed(served_two)


def test_an_edited_custom_property_value_is_still_a_change():
    before = {"properties": {"customProperties": [{"key": "a", "value": "1"}]}}
    after = {"properties": {"customProperties": [{"key": "a", "value": "2"}]}}

    assert _hashed(before) != _hashed(after)


def test_column_order_remains_significant():
    # Sorting every list would have hidden this: schema field order is real
    # information, not an unordered map.
    one = {"schemaMetadata": {"fields": [{"fieldPath": "b"}, {"fieldPath": "a"}]}}
    two = {"schemaMetadata": {"fields": [{"fieldPath": "a"}, {"fieldPath": "b"}]}}

    assert _hashed(one) != _hashed(two)


def test_unstable_map_ordering_is_disclosed_on_the_snapshot():
    snapshot = _connector().snapshot()

    assert any("without a stable entry order" in w for w in snapshot.warnings)


def test_datasets_reference_glossary_terms_by_urn_without_their_definitions():
    snapshot = _connector().snapshot()
    items = {item.external_id: item for item in _connector().normalize(snapshot)}

    # Why it matters: a definition edited upstream must be one changed asset,
    # not every dataset that carries the term.
    warehouse_payload = str(items[_WAREHOUSE_URN].raw_payload)
    assert "non-refunded orders" not in warehouse_payload


# --- the scroll's `count` is named, not read (hy-6nit) ----------------------
#
# `_SCROLL_QUERY` asks for `count` and the loop discarded it. Measured against
# the pinned v1.6.0 build over a six-entity estate, with `count` and `total`
# both selected:
#
#   page_size=1    pages 0-5 count=1 rows=1; terminal count=1 rows=0
#   page_size=2    pages 0-2 count=2 rows=2; terminal count=2 rows=0
#   page_size=100  one page  count=6 rows=6, no nextScrollId
#
# Not an echo of the request, and not the rows served. Every walk fits
# min(requested, total) -- the request and the collection, never the page. So
# the client refuses a page whose `count` is neither admissible reading rather
# than picking one, and the arms below hold BOTH the refusal and the shapes
# the live build really produces.


class _CountingScrollSession(_PagedScrollSession):
    """One page, with `count` set to whatever the arm is about."""

    def __init__(self, count, rows: int = 2, **kwargs) -> None:
        super().__init__(
            [[f"urn:li:dataset:(urn:li:dataPlatform:postgres,t{i},PROD)" for i in range(rows)]],
            **kwargs,
        )
        self._count = count

    def post(self, url, *, json=None, timeout=None):
        response = super().post(url, json=json, timeout=timeout)
        payload = response.json()
        scroll = (payload.get("data") or {}).get("scrollAcrossEntities")
        if scroll is not None:
            scroll["count"] = self._count
        return FakeResponse(200, payload)


def test_a_count_that_is_neither_the_page_nor_the_request_is_refused_by_name():
    """The build that would break a scroll silently: a collection total on a
    partial page. Read as a page size it ends the scroll early, and a short
    discovery soft-deletes every asset it never served.
    """
    client = DataHubGraphQLClient(base_url=BASE_URL, session=_CountingScrollSession(7, rows=2))

    with pytest.raises(ConnectorError) as excinfo:
        client.scroll_entities()

    message = str(excinfo.value)
    # Both readings named, and the value carried, so the reader is not sent
    # back to the source to find out which one was assumed.
    assert "7" in message
    assert "2 row(s)" in message
    assert "100 requested" in message


def test_a_count_that_is_a_boolean_is_refused_like_any_other_unnamed_value():
    """`True` is an `int` subclass and equals 1, so a one-row page would have
    accepted it as the rows served.
    """
    client = DataHubGraphQLClient(base_url=BASE_URL, session=_CountingScrollSession(True, rows=1))

    with pytest.raises(ConnectorError, match="count=True"):
        client.scroll_entities()


def test_the_terminal_page_the_live_build_really_serves_is_accepted():
    """The measurement, kept as an arm. The live walks show the terminal page
    reporting the requested size while serving zero rows -- count=1 rows=0 at
    page_size=1, count=2 rows=0 at page_size=2 -- which at this client's
    `_PAGE_SIZE` of 100 is count=100 rows=0, the shape any estate holding a
    multiple of 100 entities ends on. A guard demanding count == rows served
    would refuse the real build, so this arm is what stops the refusal above
    from being tightened into a false one.
    """
    client = DataHubGraphQLClient(base_url=BASE_URL, session=_CountingScrollSession(100, rows=0))

    assert client.scroll_entities() == []
