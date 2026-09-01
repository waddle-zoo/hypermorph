"""Deterministic stand-in for the pinned DataHub OSS v1.6.0 GraphQL API.

Serves the *recorded real* response bodies under
`tests/fixtures/datahub/v1.6.0/revenue/<capture>/graphql/`, produced from the
pinned instance by `docker/datahub/capture_evidence.py`. Tests therefore
exercise real v1.6.0 shapes -- including the nulls and enum leaves nobody
hand-wrote -- with no network, while
`tests/compose/test_datahub_live_sync.py` proves the same code against the
running instance.

Only two captures exist, `baseline` and `drift`, because restoring the
drifted glossary definition upstream produced a response byte-identical to
the baseline: DataHub returned no volatile per-request field to re-render
(contrast `tests/fake_superset.py`, where `*_humanized` values change between
two identical reads). So a test that needs the restored state replays
`baseline`, and a third duplicate capture would have asserted nothing.

Requests are dispatched on GraphQL operation name, which is how a GraphQL
endpoint is actually addressed -- there is no URL path per entity type to
match on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BASE_URL = "http://datahub.test:8080"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "datahub" / "v1.6.0" / "revenue"

_OPERATION = re.compile(r"query\s+(\w+)")


def manifest(capture: str = "baseline") -> dict:
    return json.loads((FIXTURES / capture / "manifest.json").read_text())


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeDataHubSession:
    """`requests.Session`'s narrow read-only surface, backed by fixtures.

    `status_overrides` maps a GraphQL operation name to an HTTP status, and
    `graphql_errors` maps one to a GraphQL-level `errors` body -- the two
    distinct ways a real GMS refuses a read, both of which must fail a sync
    rather than read as absent metadata.
    """

    def __init__(
        self,
        capture: str = "baseline",
        *,
        status_overrides: dict[str, int] | None = None,
        graphql_errors: dict[str, str] | None = None,
        missing_urns: frozenset[str] = frozenset(),
    ) -> None:
        self.headers: dict[str, str] = {}
        self.capture = capture
        self.status_overrides = status_overrides or {}
        self.graphql_errors = graphql_errors or {}
        self.missing_urns = missing_urns
        self.operations: list[str] = []

    # -- fixture access ---------------------------------------------------

    def _graphql(self, *parts: str) -> Any:
        return json.loads((FIXTURES / self.capture / "graphql" / Path(*parts)).read_text())

    def _entity(self, urn: str) -> Any:
        slug = manifest(self.capture)["entity_file_slugs"][urn]
        return self._graphql("entities", f"{slug}.json")

    # -- requests.Session surface -----------------------------------------

    def post(self, url: str, *, json: dict | None = None, timeout: int | None = None):
        assert url == f"{BASE_URL}/api/graphql", url
        body = json or {}
        query = body.get("query", "")
        match = _OPERATION.search(query)
        assert match, f"no named operation in query: {query[:80]}"
        operation = match.group(1)
        self.operations.append(operation)

        override = self.status_overrides.get(operation)
        if override is not None:
            return FakeResponse(override, {"message": "denied"})
        error = self.graphql_errors.get(operation)
        if error is not None:
            return FakeResponse(200, {"data": None, "errors": [{"message": error}]})

        if operation == "hypersetAppVersion":
            return FakeResponse(200, self._graphql("app-config.json"))
        if operation == "hypersetScrollEntities":
            return FakeResponse(200, self._graphql("scroll.json"))

        urn = (body.get("variables") or {}).get("urn")
        assert urn, f"{operation} sent no urn variable"
        if urn in self.missing_urns:
            # Discovered by the search index, then not served by GMS: real
            # absence, distinct from a failed read.
            root_field = operation.replace("hyperset", "")
            root_field = root_field[0].lower() + root_field[1:]
            return FakeResponse(200, {"data": {root_field: None}})
        return FakeResponse(200, self._entity(urn))
