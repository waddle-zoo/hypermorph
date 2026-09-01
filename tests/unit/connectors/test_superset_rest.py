"""Live REST transport unit tests (hy-gh-27 Phase C).

Payloads come from the recorded pinned-6.1.0 evidence via
`tests.fake_superset`, so these assert against real response shapes.
"""

import pytest
import requests

from hyperset.connectors.errors import ConnectorAuthError, ConnectorError
from hyperset.connectors.superset.rest import SupersetRestClient
from tests.fake_superset import BASE_URL, FakeResponse, FakeSupersetSession


def _client(session, **kwargs) -> SupersetRestClient:
    return SupersetRestClient(
        base_url=BASE_URL, username="admin", password="s3cret", session=session, **kwargs
    )


def test_login_sends_documented_body_and_sets_bearer_header():
    session = FakeSupersetSession()
    client = _client(session)

    client.login()

    assert session.login_bodies == [
        {"username": "admin", "password": "s3cret", "provider": "db", "refresh": True}
    ]
    assert session.headers["Authorization"] == "Bearer test-access-token"


def test_failed_login_raises_without_echoing_the_credential():
    session = FakeSupersetSession(login_status=401)
    # Both the password AND the username must stay out of the error:
    # connections.superset.username is Ref-typed (a resolved secret, hy-py62a), so a
    # credential-shaped username would otherwise be served in the failed-login message.
    client = SupersetRestClient(
        base_url=BASE_URL,
        username="AKIAV3RYS3CRETUSER",
        password="s3cret",
        session=session,
    )

    with pytest.raises(ConnectorAuthError) as excinfo:
        client.login()

    message = str(excinfo.value)
    assert "401" in message
    assert "s3cret" not in message
    assert "AKIAV3RYS3CRETUSER" not in message


def test_get_authenticates_on_first_use():
    session = FakeSupersetSession()
    client = _client(session)

    client.get_json("/api/v1/_openapi")

    assert len(session.login_bodies) == 1
    client.get_json("/api/v1/_openapi")
    assert len(session.login_bodies) == 1  # token reused, not re-fetched


def test_authorization_failure_is_never_read_as_absence():
    session = FakeSupersetSession(status_overrides={"/api/v1/dataset/": 403})
    client = _client(session)

    with pytest.raises(ConnectorAuthError, match="403"):
        client.list_resource("dataset")


def test_server_error_raises_connector_error():
    session = FakeSupersetSession(status_overrides={"/api/v1/dataset/": 500})
    client = _client(session)

    with pytest.raises(ConnectorError, match="500"):
        client.list_resource("dataset")


def test_list_resource_returns_every_recorded_dataset():
    session = FakeSupersetSession()

    rows = _client(session).list_resource("dataset")

    assert sorted(row["table_name"] for row in rows) == [
        "customer_dim",
        "finance_orders_daily",
        "raw_payments",
    ]


def test_list_resource_pages_until_the_reported_count_is_collected():
    pages = {
        "0": {"count": 3, "result": [{"id": 1}, {"id": 2}]},
        "1": {"count": 3, "result": [{"id": 3}]},
    }

    class PagingSession(FakeSupersetSession):
        def get(self, url, *, params=None, timeout=None):
            page = params["q"].split("page:")[1].split(",")[0]
            return FakeResponse(200, pages[page])

    session = PagingSession()
    client = _client(session)
    client.login()

    assert [row["id"] for row in client.list_resource("dataset")] == [1, 2, 3]


# --- a short list on HTTP 200 is refused, not returned (hy-3187) -----------
#
# `hyperset/connectors/sync.py` pre-seeds one seen-set per covered type, so a
# list that comes back short is indistinguishable from "these assets are gone"
# and the deletion pass soft-deletes the difference. The transport's existing
# protection is `get_json`: every status at or above 400, plus any response
# whose body is not an object. A well-formed object that is short passes it, at
# 200 or at any other status below 400. Firing arms rather than "nothing was
# deleted": a negative
# arm passes against a client that does nothing at all.


class _ListSession(FakeSupersetSession):
    """One canned 200 for the collection endpoint, whatever page is asked for."""

    def __init__(self, body: dict) -> None:
        super().__init__()
        self.body = body
        self.calls: list[str] = []

    def get(self, url, *, params=None, timeout=None):
        self.calls.append(params["q"] if params else "")
        return FakeResponse(200, self.body)


def test_an_empty_page_before_the_reported_count_is_refused():
    """The mass-delete path. HTTP 200, `count` says three rows exist, the page
    carries none: returning `[]` here tells the sync every chart is gone."""
    session = _ListSession({"count": 3, "result": []})
    client = _client(session)
    client.login()

    with pytest.raises(ConnectorError) as excinfo:
        client.list_resource("chart")

    assert "chart" in str(excinfo.value)
    assert "0 of 3" in str(excinfo.value)


def test_a_page_of_unusable_rows_before_the_reported_count_is_refused():
    """Same defect wearing a non-empty `result`: the rows are not objects, so
    nothing is collected and `result` being truthy is not evidence that
    anything was read.

    This one was already refused, by `_MAX_PAGES` after a thousand identical
    requests -- the only arm here that was green before the first fix. Kept,
    and kept in this shape, because the refusal now names the row on the first
    page: the assertion is what a caller sees, and a thousand round trips to
    reach the same verdict is a defect of its own.
    """
    session = _ListSession({"count": 2, "result": ["chart-1", "chart-2"]})
    client = _client(session)
    client.login()

    with pytest.raises(ConnectorError, match="cannot identify"):
        client.list_resource("chart")


def test_a_row_with_no_usable_id_is_refused_here_rather_than_downstream():
    """`hyperset/connectors/superset/connector.py` subscripts `row["id"]` bare
    to fetch each detail, so a row without one leaves the sync today as a
    `KeyError` -- an exception the sync's own handlers do not name, which is
    the verbatim reason #179 already gave for refusing a non-integer `count`.

    Under an id-denominated completeness check it is worse than untidy: the
    row is UNCOUNTABLE. It cannot enter the denominator, so passing it on
    would either inflate coverage with a row that names no asset or leave the
    guard silently short. One guard, at the only place holding both the row
    and the total.
    """
    session = _ListSession({"count": 2, "result": [{"id": 1}, {"slice_name": "no id here"}]})
    client = _client(session)
    client.login()

    with pytest.raises(ConnectorError) as excinfo:
        client.list_resource("chart")

    assert "chart" in str(excinfo.value)
    assert "no usable `id`" in str(excinfo.value)
    assert "no id here" in str(excinfo.value)


def test_a_repeating_page_is_progress_in_rows_and_no_progress_in_assets():
    """The defect the row-denominated guard let through, and the reason this
    check counts unique ids instead.

    `sync.py` adds `external_id` to a SET and soft-deletes every covered asset
    absent from it, so the consumer's unit is identities. Against a page that
    repeats, a row tally reads four rows for `count: 3` and returns two
    assets as the whole collection -- past an empty-page check, past a missing
    `count` check, and past a progress check whose before and after are both
    row counts.

    Reachable without a server bug: `list_resource` sends
    `q=(page:N,page_size:100)` with no `order_column`, so consecutive pages
    are two LIMIT/OFFSET windows over a result the backend never promised to
    order stably.
    """
    session = _ListSession({"count": 3, "result": [{"id": 1}, {"id": 2}]})
    client = _client(session)
    client.login()

    with pytest.raises(ConnectorError) as excinfo:
        client.list_resource("chart")

    assert "2 of 3" in str(excinfo.value)
    assert "had not already seen" in str(excinfo.value)
    # The row tally this replaces: four rows for a count of three, returned as
    # complete.
    assert len(session.calls) == 2


def test_a_listing_that_omits_the_count_is_refused_by_name():
    """Completeness a client cannot establish is not completeness. Without
    `count` there is no page that proves the collection ended, so one page of
    rows was being returned as the whole of it."""
    session = _ListSession({"result": [{"id": 1}]})
    client = _client(session)
    client.login()

    with pytest.raises(ConnectorError) as excinfo:
        client.list_resource("dashboard")

    assert "dashboard" in str(excinfo.value)
    assert "count" in str(excinfo.value)


def test_a_count_that_is_present_but_not_a_number_is_refused_like_a_missing_one():
    """The third 200 path, and the one that did not even fail as a connector
    error: a `count` this client cannot compare against went into
    `len(collected) >= count` and left `list_resource` as a `TypeError`. Same
    defect as an omitted `count` -- there is no total -- so it gets the same
    refusal rather than an exception the sync's own handlers do not name.
    """
    session = _ListSession({"count": "3", "result": [{"id": 1}]})
    client = _client(session)
    client.login()

    with pytest.raises(ConnectorError) as excinfo:
        client.list_resource("chart")

    assert "chart" in str(excinfo.value)
    assert "count" in str(excinfo.value)


def test_a_negative_count_is_refused_like_a_count_of_the_wrong_type():
    """The type check alone stopped one step short, and the step past it is the
    mass-delete shape this whole guard exists for: `len(seen) >= -1` is true
    before a row is read, so an EMPTY page left here as a complete listing of a
    collection with assets in it, and the deletion pass soft-deletes every one
    of them. `count: -1` is completeness the client cannot establish exactly as
    `count: "3"` and `count: true` are, so it gets the one refusal.

    Refused before the first request is even paged past, and the value is
    carried so a reader sees which shape arrived.
    """
    session = _ListSession({"count": -1, "result": []})
    client = _client(session)
    client.login()

    with pytest.raises(ConnectorError) as excinfo:
        client.list_resource("chart")

    assert "chart" in str(excinfo.value)
    assert "count" in str(excinfo.value)
    assert "-1" in str(excinfo.value)


def test_a_collection_the_instance_really_has_none_of_is_still_a_complete_read():
    """The refusal is about a count that was not reached, not about emptiness.
    Superset answers an empty collection with `count: 0`, and that page proves
    the collection ended -- the state every deletion pass for a covered type
    depends on being readable.

    This arm pins an ORDERING, and it is the only one that does: `count: 0`
    with an empty page satisfies "the total was reached" AND "this page added
    no identity" at the same time, so the total has to be tested first. Swap
    the two checks in `list_resource` and every other arm here stays green
    while an empty collection starts refusing.
    """
    session = _ListSession({"count": 0, "ids": [], "result": []})
    client = _client(session)
    client.login()

    assert client.list_resource("chart") == []


def test_an_asset_two_pages_both_carry_is_collected_once_and_counted_once():
    """The other side of the repeating-page refusal, so the guard is a
    judgement and not a reflex: overlapping windows that between them still
    cover the whole collection are a COMPLETE read, and the caller gets one
    row per asset rather than the duplicate detail fetch a row tally would
    have handed it."""
    pages = {
        "0": {"count": 3, "result": [{"id": 1}, {"id": 2}]},
        "1": {"count": 3, "result": [{"id": 2}, {"id": 3}]},
    }

    class OverlappingSession(FakeSupersetSession):
        def get(self, url, *, params=None, timeout=None):
            page = params["q"].split("page:")[1].split(",")[0]
            return FakeResponse(200, pages[page])

    client = _client(OverlappingSession())
    client.login()

    assert [row["id"] for row in client.list_resource("dataset")] == [1, 2, 3]


def test_detail_returns_the_result_object_unmodified():
    session = FakeSupersetSession()

    detail = _client(session).detail("dataset", 1)

    assert detail["uuid"] == "ae48881d-334f-54a7-94e8-1ffcc73866e2"
    assert detail["database"]["uuid"] == "191e8838-4a5c-5f3f-9d53-71f52f56f7f8"
    # A field no hand-written fixture would have invented.
    assert "always_filter_main_dttm" in detail


def test_api_version_is_the_rest_api_version_not_the_application_version():
    session = FakeSupersetSession()

    assert _client(session).api_version() == "v1"


# --- a sub-400 response whose body will not parse is a refusal (hy-ozhz) ---
#
# `get_json` called `response.json()` on any status below 400. An empty-bodied
# 204 satisfies that guard and leaves the method as a `JSONDecodeError` --
# which `_test_rest_connection` does not catch, so a connection test crashed
# instead of returning `ok=False`.
#
# The double serves a REAL `requests.Response`, so the raise under test is the
# parser this client actually runs against a live instance rather than a
# hand-written stand-in agreeing with itself.


class EmptyBodySession(FakeSupersetSession):
    """A real `requests.Response` carrying `status` and no body at all."""

    def __init__(self, status: int = 204) -> None:
        super().__init__()
        self.status = status

    def get(self, url, *, params=None, timeout=None):
        response = requests.Response()
        response.status_code = self.status
        response._content = b""
        response.url = url
        return response


def test_the_double_really_does_raise_the_parser_error_this_arm_is_about():
    """The oracle, stated separately: without it the arms below could pass
    against a client that raises `ConnectorError` for some unrelated reason.
    """
    response = EmptyBodySession().get(f"{BASE_URL}/api/v1/database/")

    with pytest.raises(ValueError):
        response.json()


def test_an_empty_bodied_sub_400_response_is_refused_by_name():
    """204 is the shape, but the guard is about the body, not the status: the
    status guard already covers 400 and up, and every read this client makes
    against pinned 6.1.0 answers with a non-empty JSON object.
    """
    client = _client(EmptyBodySession(204))
    client.login()

    with pytest.raises(ConnectorError) as excinfo:
        client.get_json("/api/v1/database/")

    message = str(excinfo.value)
    assert "204" in message
    assert "/api/v1/database/" in message
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_the_refusal_is_not_a_returned_empty_object():
    """Refusing beats answering. `{}` would be a successful read of nothing,
    and `list_resource` would hand that to the seen-set as a complete listing.
    """
    client = _client(EmptyBodySession(204))
    client.login()

    with pytest.raises(ConnectorError):
        client.list_resource("database")


def test_get_json_returns_a_dict_on_the_path_that_does_parse():
    """hy-djml: no type checker runs anywhere in this repository, so
    `get_json`'s `-> dict` is a comment. All three call sites subscript or
    `.get()` the result, so the contract is load-bearing and has to be
    asserted rather than annotated.
    """
    body = _client(FakeSupersetSession()).get_json("/api/v1/_openapi")

    assert isinstance(body, dict)
    assert body["info"]["version"] == "v1"
