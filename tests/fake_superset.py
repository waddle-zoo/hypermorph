"""Deterministic stand-in for the pinned Superset 6.1.0 REST API.

Serves the *recorded real* payloads under
`tests/fixtures/superset/6.1.0/revenue/<capture>/rest/`, generated from the
pinned Docker instance by `make demo-generate-evidence` (hy-gh-28). Tests
therefore exercise real 6.1.0 response shapes -- including fields nobody
hand-wrote -- with no network, while the live compose test proves the same
code against the running instance.

`humanized` re-renders every `*_humanized` value, which is exactly what a
real Superset does between two requests: it is the false-change trap that
idempotent resync has to survive.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BASE_URL = "http://superset.test:8088"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "superset" / "6.1.0" / "revenue"
# Superset's own shape for a collection with nothing in it, which is what a
# list endpoint returns rather than a 404.
_EMPTY_COLLECTION = {"count": 0, "ids": [], "result": []}


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


def _rehumanize(payload: Any, value: str) -> Any:
    if isinstance(payload, dict):
        return {
            key: value if key.endswith("_humanized") else _rehumanize(item, value)
            for key, item in payload.items()
        }
    if isinstance(payload, list):
        return [_rehumanize(item, value) for item in payload]
    return payload


class FakeSupersetSession:
    """`requests.Session`'s narrow read-only surface, backed by fixtures.

    `references` names a second capture directory that supplies the chart and
    dashboard bodies (`usage`, from the same pinned instance -- its seed adds
    charts and a dashboard over the datasets `revenue/` already captured and
    creates no dataset of its own). Two directories rather than one because
    they are two captures of one instance, and copying either set into the
    other would make a fixture that no `make demo-generate-*-evidence` run
    produces.

    Without it those endpoints answer an empty collection, which is what the
    `revenue/` captures recorded: no chart or dashboard body exists under any
    of them. Empty rather than an assertion error, because "the source serves
    this type and has none of it" is a real state a connector must handle --
    it is the state that makes every dashboard-deleted-upstream deletion pass
    run (`hyperset/connectors/sync.py`, the covered-type pre-seed).
    """

    def __init__(
        self,
        capture: str = "baseline",
        *,
        references: str | None = None,
        humanized: str = "now",
        status_overrides: dict[str, int] | None = None,
        login_status: int = 200,
    ) -> None:
        self.headers: dict[str, str] = {}
        self.capture = capture
        self.references = references
        self.humanized = humanized
        self.status_overrides = status_overrides or {}
        self.login_status = login_status
        self.login_bodies: list[dict] = []
        self.requested_paths: list[str] = []

    # -- fixture access ---------------------------------------------------

    def _rest(self, name: str) -> Any:
        path = _FIXTURES / self.capture / "rest" / name
        return _rehumanize(json.loads(path.read_text()), self.humanized)

    def _reference_rest(self, name: str) -> Any | None:
        """A chart or dashboard body, or `None` when this session was not
        given a capture that holds them."""
        if self.references is None:
            return None
        path = _FIXTURES.parent / self.references / "rest" / name
        return _rehumanize(json.loads(path.read_text()), self.humanized)

    def _dataset_ids(self) -> dict[str, str]:
        listing = json.loads((_FIXTURES / self.capture / "rest" / "dataset-list.json").read_text())
        return {str(row["id"]): row["table_name"] for row in listing["result"]}

    def _chart_slugs(self) -> dict[str, str]:
        listing = self._reference_rest("chart-list.json") or {"result": []}
        return {
            str(row["id"]): row["slice_name"].lower().replace(" ", "_") for row in listing["result"]
        }

    # -- requests.Session surface -----------------------------------------

    def post(self, url: str, *, json: dict | None = None, timeout: int | None = None):
        assert url == f"{BASE_URL}/api/v1/security/login", url
        self.login_bodies.append(json or {})
        if self.login_status >= 400:
            return FakeResponse(self.login_status, {"message": "Not authorized"})
        return FakeResponse(200, {"access_token": "test-access-token"})

    def get(self, url: str, *, params: dict | None = None, timeout: int | None = None):
        assert url.startswith(BASE_URL), url
        path = url[len(BASE_URL) :]
        self.requested_paths.append(path)
        assert self.headers.get("Authorization", "").startswith("Bearer "), (
            f"unauthenticated GET {path}"
        )

        override = self.status_overrides.get(path)
        if override is not None:
            return FakeResponse(override, {"message": "denied"})

        if path == "/api/v1/_openapi":
            return FakeResponse(200, {"info": {"title": "Superset", "version": "v1"}})
        if path == "/api/v1/database/":
            return FakeResponse(200, self._rest("database-list.json"))
        if path == "/api/v1/dataset/":
            return FakeResponse(200, self._rest("dataset-list.json"))
        if path.startswith("/api/v1/database/"):
            return FakeResponse(200, self._rest("database-detail.json"))
        if path.startswith("/api/v1/dataset/"):
            native_id = path.rsplit("/", 1)[-1]
            table = self._dataset_ids()[native_id]
            return FakeResponse(200, self._rest(f"dataset-{table}-detail.json"))
        if path == "/api/v1/chart/":
            return FakeResponse(200, self._reference_rest("chart-list.json") or _EMPTY_COLLECTION)
        if path == "/api/v1/dashboard/":
            return FakeResponse(
                200, self._reference_rest("dashboard-list.json") or _EMPTY_COLLECTION
            )
        if path.startswith("/api/v1/chart/"):
            slug = self._chart_slugs()[path.rsplit("/", 1)[-1]]
            return FakeResponse(200, self._reference_rest(f"chart-{slug}-detail.json"))
        if path.startswith("/api/v1/dashboard/"):
            return FakeResponse(200, self._reference_rest("dashboard-detail.json"))
        raise AssertionError(f"unexpected path {path}")
