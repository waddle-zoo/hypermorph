"""Live read-only Superset REST transport (hy-gh-27 Phase C).

The export ZIP and the REST API are two separate upstream contracts
(ADR 0003), and within REST, list rows and detail bodies are two more
(`docs/research/superset-api-v1.md` "Connector implications for Hyperset v0"
item 1). This client keeps them separate: list endpoints are discovery
only, detail endpoints produce the payload an observed version stores.

Only GETs are issued. Superset's CSRF protection applies to
state-changing requests, so a read-only connector needs the JWT bearer
token alone -- no CSRF token, no cookie session. Nothing here writes back
to Superset.

Verified against the pinned local `apache/superset:6.1.0` instance on
2026-07-26:

- `POST /api/v1/security/login` with `{username, password, provider, refresh}`
  returns `access_token`;
- `GET /api/v1/_openapi` reports `info.version == "v1"` -- the *API*
  version. The build does not disclose its application version over REST,
  so `source_version` stays `None` rather than guessing;
- `GET /api/v1/database/_info` returns HTTP 500 (`'encrypted_extra'`) on
  this build, so filter/order discovery through `_info` is not depended on.
"""

from __future__ import annotations

from typing import Any

from hyperset.connectors.errors import ConnectorAuthError, ConnectorError

# docs/research/superset-api-v1.md item 6: never assume a page size above the
# instance's configured maximum was honored -- page until the collected
# count matches the reported `count`.
_PAGE_SIZE = 100
_MAX_PAGES = 1000


def _requests_session():
    try:
        import requests
    except ModuleNotFoundError as exc:  # pragma: no cover - packaging guard
        raise ConnectorError(
            "live Superset mode needs the 'requests' dependency; install the hyperset package"
        ) from exc
    return requests.Session()


class SupersetRestClient:
    """Minimal read-only Superset REST client.

    `session` is any object exposing `requests.Session`'s `headers`,
    `get(url, params=..., timeout=...)` and `post(url, json=..., timeout=...)`
    -- injected by tests so REST normalization can be exercised against
    recorded real payloads without a network or a mocking library.
    """

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        provider: str = "db",
        timeout: int = 30,
        session: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._username = username
        # Held in memory for the life of one sync only: never logged, never
        # written to a payload, never persisted (hy-gh-27 "credentials are
        # references/configuration only").
        self._password = password
        self._provider = provider
        self._timeout = timeout
        self._session = session if session is not None else _requests_session()
        self._authenticated = False

    # -- authentication ---------------------------------------------------

    def login(self) -> None:
        response = self._session.post(
            f"{self.base_url}/api/v1/security/login",
            json={
                "username": self._username,
                "password": self._password,
                "provider": self._provider,
                "refresh": True,
            },
            timeout=self._timeout,
        )
        if response.status_code >= 400:
            # Status ONLY -- names NO credential. The username is Ref-typed (a resolved
            # secret, hy-py62a), so echoing it here would serve a credential-shaped value in
            # the error to a log or caller; the password already stayed out for the same reason.
            raise ConnectorAuthError(f"Superset login failed with HTTP {response.status_code}")
        token = response.json().get("access_token")
        if not token:
            raise ConnectorAuthError("Superset login response contained no access_token")
        self._session.headers["Authorization"] = f"Bearer {token}"
        self._authenticated = True

    # -- reads ------------------------------------------------------------

    def get_json(self, path: str, *, params: dict | None = None) -> dict:
        if not self._authenticated:
            self.login()
        response = self._session.get(f"{self.base_url}{path}", params=params, timeout=self._timeout)
        status = response.status_code
        if status in (401, 403):
            # research item 4: permission filters and partial access produce
            # absence without deletion. Raising keeps the sync from mistaking
            # an authorization failure for "the asset is gone".
            raise ConnectorAuthError(f"Superset returned HTTP {status} for GET {path}")
        if status >= 400:
            raise ConnectorError(f"Superset returned HTTP {status} for GET {path}")
        try:
            body = response.json()
        except ValueError as exc:
            # A sub-400 status whose body will not parse -- an empty-bodied 204
            # is the shape that reaches here. Left unhandled it leaves this
            # method as a `JSONDecodeError`, which no caller names:
            # `_test_rest_connection` catches `ConnectorError` only, so a
            # connection test CRASHED instead of returning `ok=False` (hy-ozhz).
            #
            # Refused rather than answered with `{}`, because on the pinned
            # 6.1.0 build no read this client makes can produce it: all four
            # listings, the detail read, `_openapi`, and a page past the end of
            # a collection return a non-empty JSON object, an absent id is a
            # 404, and of the 276 operations in the instance's own OpenAPI spec
            # exactly two declare a 2xx with no content -- both POSTs, neither
            # on a path reachable from here. Returning `{}` would invent a
            # successful empty answer for a shape the source never sends, which
            # is how a read that failed becomes a collection that is empty.
            raise ConnectorError(
                f"GET {path} returned HTTP {status} with a body that is not JSON, "
                "so this read produced no answer at all"
            ) from exc
        if not isinstance(body, dict):
            raise ConnectorError(f"GET {path} returned {type(body).__name__}, expected an object")
        return body

    def list_resource(self, resource: str) -> list[dict]:
        """Every visible row of one collection, paged to completion.

        A SHORT read is refused rather than returned (hy-3187). The caller
        feeds this list into a per-type seen-set and `connectors/sync.py`
        soft-deletes every covered asset absent from it, so a list that came
        back short is indistinguishable from "these assets are gone". Two HTTP
        200 paths used to return short and raise nothing: an EMPTY page while
        `count` said more remain, and a response omitting `count` altogether,
        which returned page 0 as the whole collection. A page that carries rows
        of which none are readable is a third shape and not one of those two:
        it did raise, at `_MAX_PAGES`, after a thousand pointless requests, so
        it is refused here BY NAME rather than refused for the first time.
        `get_json` sits above all of this and refuses every status at or above
        400, plus any response whose body is not an object; what it cannot
        reach is a well-formed object that is short -- at 200 or at any other
        status below 400. A `count` that is present but not a non-negative
        integer is the same defect wearing a value, and used to leave this
        method by raising `TypeError` out of a comparison, or -- for a
        negative count -- by returning the empty page as a complete read.

        Completeness is counted in UNIQUE `id`s, not in rows, because that is
        the unit the consumer measures in: `sync.py` adds `external_id` to a
        SET and soft-deletes what is missing from it. A row tally answers "did
        I receive `count` rows", and the only question downstream is "did I
        cover `count` assets". The two come apart with no server bug at all --
        the query below carries no `order_column`, so consecutive pages are two
        LIMIT/OFFSET windows over a result the backend need not order stably,
        and a repeated row is both a row of progress and no new coverage.

        The denominator is `id` and the consumer's unit is `uuid`, which is a
        PROXY, and what makes it valid is a raise: `_required_uuid` in
        `connector.py` raises `ConnectorError` when a detail payload has no
        uuid, and nothing on the snapshot path swallows it, so no listed id can
        end up silently absent from the seen-set. Turn that raise into a
        skip-and-continue and this guard goes on passing while the skipped
        asset is soft-deleted;
        `tests/unit/connectors/test_superset_identity_bridge.py` pins it.

        Refusing beats deleting: a sync that dies is rerun, a mass soft-delete
        is a restore.
        """
        collected: list[dict] = []
        # Identity, not position: this is the denominator the refusals below
        # compare against.
        seen: set[int | str] = set()
        page = 0
        while True:
            body = self.get_json(
                f"/api/v1/{resource}/", params={"q": f"(page:{page},page_size:{_PAGE_SIZE})"}
            )
            batch = body.get("result") or []
            count = body.get("count")
            # Absent OR unusable, because the failure is the same one: with no
            # total this client can compare against, no page proves the
            # collection ended, and "the rows I happened to receive" would be
            # passed on as "the rows that exist". Completeness the client
            # cannot establish is not completeness. `bool` is excluded
            # explicitly -- it is an `int` subclass, so `count: true` would
            # otherwise read as a total of one. A NEGATIVE count is the same
            # defect one step past the type boundary, and the worst-behaved
            # one: `len(seen) >= count` is already true before a row is read,
            # so an empty page leaves here as a complete listing of nothing.
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ConnectorError(
                    f"{resource} listing carried no usable `count` (got {count!r}), so a "
                    f"complete read cannot be told from a truncated one; "
                    f"{len(seen)} assets had been collected"
                )
            before = len(seen)
            for item in batch:
                native_id = item.get("id") if isinstance(item, dict) else None
                # One guard, not two. A row with no usable id is UNCOUNTABLE
                # once completeness is denominated in ids -- it cannot enter
                # the denominator, so this is the place that has to rule on it
                # rather than pass it down to `connector.py`, where `row["id"]`
                # is subscripted bare and such a row leaves the sync as a
                # `KeyError` its own handlers do not name. `bool` is excluded
                # for the same reason as in `count`: it is an `int` subclass.
                if (
                    not isinstance(native_id, int | str)
                    or isinstance(native_id, bool)
                    or native_id == ""
                ):
                    raise ConnectorError(
                        f"{resource} listing returned a row this read cannot identify "
                        f"(no usable `id`: {item!r}); completeness here is counted in unique "
                        f"ids, so a row without one can neither be covered nor counted"
                    )
                if native_id in seen:
                    continue
                seen.add(native_id)
                collected.append(item)
            # Ordering is load-bearing, and one arm depends on it alone: an
            # instance that really has none of a collection answers `count: 0`
            # with an empty page, which adds no identity and IS a complete
            # read. Test the total first and a page that added nothing second,
            # or the empty collection every deletion pass depends on reading
            # becomes a refusal.
            if len(seen) >= count:
                return collected
            if len(seen) == before:
                # A page that adds no IDENTITY is not progress, whether it came
                # back empty or repeated rows an earlier page already carried.
                # Refused here rather than left to `_MAX_PAGES`, which reaches
                # the same answer after a thousand pointless requests.
                raise ConnectorError(
                    f"{resource} listing stopped short: page {page} added no asset this read "
                    f"had not already seen, and {len(seen)} of {count} were collected"
                )
            page += 1
            if page >= _MAX_PAGES:
                raise ConnectorError(
                    f"{resource} listing did not terminate: collected {len(seen)} of "
                    f"{count} assets after {page} pages"
                )

    def detail(self, resource: str, native_id: int | str) -> dict:
        body = self.get_json(f"/api/v1/{resource}/{native_id}")
        result = body.get("result")
        if not isinstance(result, dict):
            raise ConnectorError(f"{resource} {native_id} detail response has no result object")
        return result

    def api_version(self) -> str | None:
        """`info.version` from the generated OpenAPI spec.

        This is the REST API version (`"v1"` on the pinned build), not the
        Superset application version -- recorded as capability metadata, never
        as `ConnectorSnapshot.source_version`.
        """
        try:
            body = self.get_json("/api/v1/_openapi")
        except ConnectorError:
            return None
        info = body.get("info")
        version = info.get("version") if isinstance(info, dict) else None
        return str(version) if version else None
