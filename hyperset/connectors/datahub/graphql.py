"""Live read-only DataHub GraphQL transport (hy-gh-17).

GraphQL is not REST-with-a-different-URL: the server returns exactly the
fields the query asked for, so "lossless" can only ever mean "lossless with
respect to this query document". Every projection this client sends is
therefore a named constant in `hyperset.connectors.datahub.connector`, and
the connector fingerprints them onto each snapshot -- a narrowed projection
is visible as a checkpoint change instead of looking like the source
dropped metadata.

Only queries are issued. GMS exposes mutations on the same endpoint, so
"read-only" here is a property of what this module sends, and there is no
method that builds a mutation.

Verified against the pinned local `acryldata/datahub-gms:v1.6.0` instance
on 2026-07-27:

- `POST /api/graphql` serves the whole schema; `scrollAcrossEntities` and
  the per-type root fields (`dataset`, `domain`, `corpUser`,
  `glossaryTerm`) all exist on this build;
- `{ appConfig { appVersion } }` returns `"v1.6.0"` -- unlike Superset,
  DataHub *does* disclose its application version, so `source_version` is
  real evidence rather than `None`;
- `CorpUser.status` and `DatasetProperties.created` are leaf types on this
  build (a subselection on either is a `ValidationError`), which is why the
  projections select them bare.
"""

from __future__ import annotations

from typing import Any

from hyperset.connectors.errors import ConnectorAuthError, ConnectorError

# `scrollAcrossEntities` is cursor-paged: DataHub hands back a `nextScrollId`
# and a null cursor means "no more pages". The page size is ours to choose;
# the guard exists so a server that keeps returning a cursor can never spin
# forever.
_PAGE_SIZE = 100
_MAX_PAGES = 1000

_APP_VERSION_QUERY = "query hypersetAppVersion { appConfig { appVersion } }"

_SCROLL_QUERY = """
query hypersetScrollEntities($types: [EntityType!], $count: Int!, $scrollId: String) {
  scrollAcrossEntities(input: {types: $types, query: "*", count: $count, scrollId: $scrollId}) {
    nextScrollId
    count
    searchResults {
      entity {
        urn
        type
      }
    }
  }
}
"""


def _requests_session():
    try:
        import requests
    except ModuleNotFoundError as exc:  # pragma: no cover - packaging guard
        raise ConnectorError(
            "live DataHub mode needs the 'requests' dependency; install the hyperset package"
        ) from exc
    return requests.Session()


class DataHubGraphQLClient:
    """Minimal read-only DataHub GraphQL client.

    `session` is any object exposing `requests.Session`'s `headers` and
    `post(url, json=..., timeout=...)` -- injected by tests so normalization
    can be exercised against recorded real responses without a network or a
    mocking library.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str | None = None,
        timeout: int = 30,
        session: Any | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session if session is not None else _requests_session()
        if token:
            # Held in memory for the life of one sync only: never logged,
            # never written to a payload, never persisted on the connection.
            self._session.headers["Authorization"] = f"Bearer {token}"

    # -- reads ------------------------------------------------------------

    def execute(self, query: str, variables: dict | None = None) -> dict:
        """One GraphQL query. Returns the `data` object.

        A GraphQL endpoint answers a partial failure with HTTP 200 plus an
        `errors` array, so status alone is not enough: an errored response is
        raised rather than returned, because a sync that accepted `null` here
        would record "the source has no such metadata" when what actually
        happened was a failed read (ADR 0010: absence is never inferred).
        """
        response = self._session.post(
            f"{self.base_url}/api/graphql",
            json={"query": query, "variables": variables or {}},
            timeout=self._timeout,
        )
        status = response.status_code
        if status in (401, 403):
            # Token auth disabled/enabled is deployment configuration; either
            # way an authorization failure must stop the sync, so a
            # permission-filtered read can never look like deletion.
            raise ConnectorAuthError(f"DataHub returned HTTP {status} for POST /api/graphql")
        if status >= 400:
            raise ConnectorError(f"DataHub returned HTTP {status} for POST /api/graphql")
        body = response.json()
        if not isinstance(body, dict):
            raise ConnectorError(
                f"DataHub GraphQL returned {type(body).__name__}, expected an object"
            )
        errors = body.get("errors")
        if errors:
            messages = "; ".join(
                str(error.get("message", error)) if isinstance(error, dict) else str(error)
                for error in errors
            )
            raise ConnectorError(f"DataHub GraphQL reported errors: {messages}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise ConnectorError("DataHub GraphQL response carried no data object")
        return data

    def app_version(self) -> str | None:
        """DataHub's own application version, e.g. `"v1.6.0"`.

        Unlike the Superset connector's `api_version`, this *is* the source
        product version and is recorded as `ConnectorSnapshot.source_version`.
        A build that declines to answer yields `None` rather than a guess.
        """
        config = self.execute(_APP_VERSION_QUERY).get("appConfig")
        version = config.get("appVersion") if isinstance(config, dict) else None
        return str(version) if version else None

    def scroll_entities(self, entity_types: tuple[str, ...] | None = None) -> list[dict]:
        """Every `{urn, type}` the instance exposes, or only `entity_types`.

        Discovery only. The search index behind this decides *which* entities
        exist; the per-URN projections decide what is observed about them --
        the same list/detail separation the Superset REST client keeps, for
        the same reason (ADR 0003: two contracts, never merged into one).

        `entity_types=None` sends `types: null`, which the pinned build reads
        as "every entity type". The connector asks for that deliberately: it
        can then report the types it found but does not project, instead of
        asserting completeness against a hardcoded list of what it assumed
        the instance would hold.

        Each entity is yielded once, in the order the scroll first served it.
        The pages come from a live search index, which can serve one URN on
        two of them -- a document reindexed between two cursor fetches is the
        ordinary way -- and a URN is an identity, so a second sighting is the
        same entity rather than another one (hy-dtpj). Without this the
        caller pays a second `entity()` round trip for an asset it already
        read, whose payload can differ if the source moved in between, and
        counts it twice. The dedupe is global to the scroll, not per page, so
        a URN repeated inside ONE page is collapsed as well.

        THE FIRST SIGHTING'S TYPE WINS, and a later sighting that disagrees
        about the type is discarded whole -- not merged, not preferred. That
        is defensible only because in DataHub's scheme the type is a FUNCTION
        of the URN: `urn:li:dataset:...` is `DATASET`, `urn:li:chart:...` is
        `CHART`, `urn:li:dataJob:...` is `DATA_JOB`, one-to-one across every
        pair this repository's recorded fixture contains. A divergence is
        therefore the source contradicting itself, and keeping the last
        sighting would be no more defensible than keeping the first
        (hy-96zh).

        WHAT THE DISCARD COSTS, so nobody has to run it to find out. The
        second sighting's type is never read, and where that lands depends on
        it: a projected type means the URN never enters that type's bucket in
        `snapshot()`, so it is absent from the per-type seen-set `run_sync`
        pre-seeds from `covered_asset_types` -- and a governed asset of that
        type, absent from the set, is deletion-checked as gone. An unprojected
        type means the `unprojected_entity_types` count loses it, which is the
        one disclosure whose whole job is to report a type found but not
        projected; if that URN was the only sighting of the type, the type
        disappears from the snapshot's warnings entirely. This connector's
        `covered_asset_types` is the static `_COVERED_ASSET_TYPES` and is not
        derived from discovery, so coverage itself cannot lose a type here.

        A row with a falsy `urn` or `type` is skipped WITHOUT entering the
        seen set, so a later well-formed sighting of the same URN still
        lands.

        `count` COMES BACK AND IS NOT A COMPLETENESS SIGNAL, and this is
        measured rather than assumed. Against the pinned v1.6.0 build, with
        `count` and `total` both selected over a six-entity estate:

            page_size=1    pages 0-5 count=1 rows=1; terminal count=1 rows=0
            page_size=2    pages 0-2 count=2 rows=2; terminal count=2 rows=0
            page_size=100  one page  count=6 rows=6, no `nextScrollId`

        So it is not an echo of the request (100 asked, 6 returned) and it is
        not the rows this page served (the terminal pages report 1 and 2 while
        serving none). Every walk is consistent with `min(requested, total)` --
        a function of the REQUEST and the collection, never of the page. The
        trap is that at a page size above the estate, `count`, rows-served and
        `total` are all the same number, so every reading of the field agrees
        and a small fixture can never disagree with whichever meaning a reader
        arrived with. `total` is refused separately: it caps at 10000 and the
        cap is invisible (hy-6nit).

        What the loop does with it is therefore NOT to pick a reading. It
        refuses the page whose `count` is neither admissible reading -- neither
        the rows served nor the size requested -- and says both readings in the
        error, because a build returning a collection total on a partial page
        is exactly where silently reading it as a page size would be wrong.
        """
        collected: list[dict] = []
        seen: set[str] = set()
        scroll_id: str | None = None
        for _page in range(_MAX_PAGES):
            data = self.execute(
                _SCROLL_QUERY,
                {
                    "types": list(entity_types) if entity_types is not None else None,
                    "count": _PAGE_SIZE,
                    "scrollId": scroll_id,
                },
            )
            result = data.get("scrollAcrossEntities") or {}
            rows = result.get("searchResults") or []
            count = result.get("count")
            # `bool` excluded for the usual reason: it is an `int` subclass, so
            # `count: true` would otherwise read as a page of one.
            if (
                not isinstance(count, int)
                or isinstance(count, bool)
                or count not in (len(rows), _PAGE_SIZE)
            ):
                raise ConnectorError(
                    f"DataHub scroll page {_page} reported count={count!r}, which is neither "
                    f"the {len(rows)} row(s) it served nor the {_PAGE_SIZE} requested, so what "
                    "the field counts on this build is unnamed. Refused rather than read as "
                    "either: a collection total taken for a page size ends a scroll early, and "
                    "a short discovery soft-deletes every asset it never served (hy-6nit)."
                )
            for row in rows:
                entity = row.get("entity") if isinstance(row, dict) else None
                if isinstance(entity, dict) and entity.get("urn") and entity.get("type"):
                    urn = str(entity["urn"])
                    if urn in seen:
                        continue
                    seen.add(urn)
                    collected.append({"urn": urn, "type": str(entity["type"])})
            scroll_id = result.get("nextScrollId")
            if not scroll_id:
                return collected
        raise ConnectorError(
            f"DataHub entity scroll did not terminate: {len(collected)} entities after "
            f"{_MAX_PAGES} pages"
        )

    def entity(self, query: str, root_field: str, urn: str) -> dict | None:
        """One entity's projected detail, or `None` when GMS answers `null`.

        `None` means the instance genuinely has no such entity at that URN --
        an errored or unauthorized read raises in `execute` instead, so the
        caller can tell "absent" from "unreadable".
        """
        value = self.execute(query, {"urn": urn}).get(root_field)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ConnectorError(f"{root_field}({urn}) returned {type(value).__name__}")
        return value
