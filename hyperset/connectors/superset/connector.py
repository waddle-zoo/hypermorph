"""Read-only, lossless observation of Superset, over an export bundle or
the live REST API.

Export and REST are separate upstream contracts (ADR 0003) and are never
presented as equivalent: each snapshot names its `transport`, the asset
types it actually covered, and what the transport failed to disclose. Both
modes now read all four asset types (hy-rt4v).

What ADR 0003 forbids is presenting one contract's payload as the other's,
and nothing here does: the payloads stay whole and unconverted, and the two
transports meet only at the point where one SOURCE FACT is spelled under two
names -- `dataset_uuid`/`datasource_uuid` for the dataset a chart queries,
`position`/`position_json` for the layout naming the charts a dashboard
contains. Reading one name only is what left live REST reading half the
estate, and it would have failed silently: a chart with no link looks exactly
like a chart that references nothing. `tests/fixtures/superset/6.1.0/usage`
pins both spellings from the real instance (hy-vzk8).
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hyperset.connectors.errors import ConnectorError
from hyperset.connectors.superset.bundle import load_export_bundle
from hyperset.connectors.superset.rest import SupersetRestClient
from hyperset.connectors.types import (
    ConnectionTest,
    ConnectorSnapshot,
    ObservedAssetInput,
    UnresolvedLink,
)
from hyperset.repositories.hash_basis import DROP_KEY_SUFFIXES

_SUPPORTED_ASSET_DIRS = ("databases", "datasets", "charts", "dashboards")

# Server-rendered relative times ("now", "10 minutes ago") that Superset
# recomputes per request: real fields, kept in the stored payload, but
# excluded from change detection so an unchanged resync stays a no-op.
_VOLATILE_KEY_SUFFIX = "_humanized"

# Declared once, stored on every version row it produces, and applied by the
# repository -- so what a REST asset's `content_hash` covers can be replayed
# from stored state instead of from this module (hy-y8g).
_HASH_BASIS = {DROP_KEY_SUFFIXES: [_VOLATILE_KEY_SUFFIX]}


def _unsupported_asset_dirs(bundle_path: Path) -> list[str]:
    """Export directories this connector doesn't map to an ObservedAsset
    type yet (e.g. `queries/` for SQL Lab saved queries,
    `annotation_layers/`) -- surfaced as a warning per snapshot rather than
    silently dropped, per hy-gh-27's "capability/limitation metadata"
    requirement. `docs/architecture.md`'s "Explicitly out of scope" list
    already names SQL Lab/annotations as deferred; this is where that
    deferral becomes visible to a caller instead of implicit.

    Mirrors `load_export_bundle`'s own matching (any path component, not
    just the first) rather than checking only `parts[0]` -- a real
    Superset export wraps every file in a `<type>_export_<timestamp>/`
    directory (confirmed against a real 6.1.0 instance), so `parts[0]` is
    that wrapper, never the asset-type directory. `metadata.yaml` (the
    export manifest) is never itself a directory to report.
    """
    unsupported: set[str] = set()
    with zipfile.ZipFile(bundle_path) as archive:
        for name in archive.namelist():
            if name.endswith("/") or not name.endswith(".yaml"):
                continue
            parts = Path(name).parts[:-1]
            if any(p in _SUPPORTED_ASSET_DIRS for p in parts):
                continue
            if Path(name).name == "metadata.yaml":
                continue
            if len(parts) >= 2:
                # parts[0] is the export's own wrapper directory; the
                # asset-type-like directory is the next component in.
                unsupported.add(parts[1])
            elif len(parts) == 1:
                unsupported.add(parts[0])
    return sorted(unsupported)


def _owner_identity(owner) -> str:
    """Return a readable source owner without creating governed identity."""
    if isinstance(owner, dict):
        return (
            owner.get("username")
            or owner.get("email")
            or " ".join(filter(None, [owner.get("first_name"), owner.get("last_name")]))
            or str(owner.get("id", "unknown"))
        )
    return str(owner)


def _chart_refs_from_position(position) -> list[str]:
    """Pull stable chart UUIDs from Superset 6.1.0's layout tree."""
    if not isinstance(position, dict):
        return []
    refs = []
    for component in position.values():
        if isinstance(component, dict) and component.get("type") == "CHART":
            meta = component.get("meta") or {}
            ref = meta.get("uuid")
            if ref:
                refs.append(str(ref))
    return refs


def _volatile_keys(payload: Any) -> set[str]:
    """Which `*_humanized` keys this payload actually carried -- disclosed as
    a snapshot warning instead of silently narrowing change detection."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.endswith(_VOLATILE_KEY_SUFFIX):
                found.add(key)
            found |= _volatile_keys(value)
    elif isinstance(payload, list):
        for item in payload:
            found |= _volatile_keys(item)
    return found


def _source_modified_at(payload: dict) -> datetime | None:
    """Superset REST detail bodies carry `changed_on` as a naive UTC ISO
    timestamp -- confirmed against the pinned instance, where a dataset's
    detail `changed_on` equals the list row's `changed_on_utc` with a
    `+0000` offset. An unparseable value yields `None` rather than a guess.
    """
    value = payload.get("changed_on")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _required_uuid(payload: dict, asset_type: str) -> str:
    value = payload.get("uuid")
    if not value:
        raise ConnectorError(f"{asset_type} payload has no stable uuid")
    return str(value)


def _normalize_database(payload: dict) -> ObservedAssetInput:
    name = payload.get("database_name") or payload.get("verbose_name") or "<unknown>"
    return ObservedAssetInput(
        external_id=_required_uuid(payload, "database"),
        asset_type="database",
        raw_payload=payload,
        normalized={"name": name},
    )


def _dataset_database_ref(payload: dict) -> str | None:
    """The parent database's UUID, whichever contract emitted the dataset.

    Export YAML names it `database_uuid`; the REST detail body nests it as
    `database.uuid` (both verified on the pinned 6.1.0 instance). Neither
    shape is normalized into the other -- this only reads whichever is
    present, and returns `None` when the payload proves no relationship.
    """
    database_uuid = payload.get("database_uuid")
    if database_uuid:
        return str(database_uuid)
    nested = payload.get("database")
    if isinstance(nested, dict) and nested.get("uuid"):
        return str(nested["uuid"])
    return None


def _normalize_dataset(payload: dict) -> ObservedAssetInput:
    table_name = payload.get("table_name", "<unknown>")
    links = []
    database_uuid = _dataset_database_ref(payload)
    if database_uuid:
        links.append(
            UnresolvedLink(
                kind="database", target_external_id=str(database_uuid), relation="belongs_to"
            )
        )
    normalized = {
        "name": table_name,
        "schema": payload.get("schema"),
        "description": payload.get("description") or "",
        "column_names": [
            c.get("column_name") for c in payload.get("columns", []) if c.get("column_name")
        ],
        # Name *and* expression: hy-gh-38's drift rule compares what Git
        # approves against what the source now computes, and a bare list of
        # names cannot answer that. The connector reads the source shape --
        # `metrics[].metric_name`/`expression` in both the export YAML and
        # the REST detail body -- so the processor never parses Superset.
        "metrics": [
            {"name": m["metric_name"], "expression": str(m.get("expression") or "")}
            for m in payload.get("metrics", [])
            if m.get("metric_name")
        ],
    }
    return ObservedAssetInput(
        external_id=_required_uuid(payload, "dataset"),
        asset_type="dataset",
        raw_payload=payload,
        normalized=normalized,
        links=links,
    )


def _chart_dataset_ref(payload: dict) -> str | None:
    """The queried dataset's UUID, whichever contract emitted the chart.

    Export YAML names it `dataset_uuid`; the REST detail body names it
    `datasource_uuid` and says which kind of datasource it is in
    `datasource_type` (both pinned in tests/fixtures/superset/6.1.0/usage).
    Same shape as `_dataset_database_ref` and for the same reason: read
    whichever name is present, convert neither payload into the other, and
    return `None` when the payload proves no relationship.

    `datasource_type` is checked where present, because a chart can be built
    on a saved query rather than a dataset, and a `dataset` link to something
    that is not one puts a reference in the projection that no dataset can
    resolve. The reference count is a ranking input (hy-g1y8), so a wrong
    reference costs more than a missing one. Absence still means dataset: the
    export contract carries no such field at all, and treating absence as
    unknown would drop every export chart's link.
    """
    kind = payload.get("datasource_type")
    if kind is not None and kind != "table":
        return None
    ref = payload.get("dataset_uuid") or payload.get("datasource_uuid")
    return str(ref) if ref else None


def _dashboard_position(payload: dict) -> dict | None:
    """The layout tree, whichever contract emitted the dashboard.

    Export YAML carries it as a mapping under `position`; REST carries the
    same tree as a JSON string under `position_json`, and both name a chart by
    `meta.uuid`. A string that will not parse yields `None`, which reads as no
    charts -- indistinguishable from a dashboard that genuinely contains none,
    which is why `_unparseable_layout_disclosure` names it at snapshot time
    rather than letting the projection carry the ambiguity silently. Empty is
    the one case that is not ambiguous: Superset itself reads a falsy
    `position_json` as `"{}"`, so no charts is what it means, and the
    disclosure deliberately stays quiet about it.
    """
    position = payload.get("position")
    if isinstance(position, dict):
        return position
    raw = payload.get("position_json")
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _unparseable_layout_disclosure(dashboards: Iterable[dict]) -> list[str]:
    """One warning naming every dashboard whose layout could not be read, or
    nothing when they all could. Emitted for the same reason as
    `_volatile_disclosure`: what the read could not establish is the
    connector's to say, and no downstream reader can recover it from an empty
    link set.

    An empty `position_json` is a dashboard that was never laid out, not one
    whose layout is unreadable, and Superset 6.1.0 is the authority on that:
    `superset/utils/json.py:179` exempts a falsy value from `validate_json`
    entirely, so the API accepts `""` on POST and PUT without complaint; every
    read site in the shipped source guards on truth rather than on `None`
    (`superset/models/dashboard.py:295`, `commands/report/base.py:111` and
    `mcp_service/.../add_chart_to_existing_dashboard.py:432` all say
    `position_json or "{}"`); and `commands/dashboard/importers/v0.py:239`
    says it outright -- "position_json can be empty for dashboards". Calling
    that unreadable would name a routine dashboard to the reviewer as the very
    ambiguity this disclosure exists to remove, inverted: a warning that fires
    on the ordinary case is what teaches a reviewer to ignore the warning.
    """
    unreadable = sorted(
        str(payload.get("uuid", "<no uuid>"))
        for payload in dashboards
        if payload.get("position_json") and _dashboard_position(payload) is None
    )
    if not unreadable:
        return []
    return [
        "dashboard layout could not be parsed, so the charts these dashboards contain are "
        f"recorded as none rather than as unknown: {', '.join(unreadable)}"
    ]


def _normalize_chart(payload: dict) -> ObservedAssetInput:
    name = payload.get("slice_name", "<unknown>")
    links = []
    dataset_ref = _chart_dataset_ref(payload)
    if dataset_ref:
        links.append(
            UnresolvedLink(kind="dataset", target_external_id=str(dataset_ref), relation="queries")
        )
    normalized = {
        "name": name,
        "description": payload.get("description") or "",
        "viz_type": payload.get("viz_type"),
        "owners": [_owner_identity(o) for o in payload.get("owners", [])],
        "certified": bool(payload.get("certified_by")),
    }
    return ObservedAssetInput(
        external_id=_required_uuid(payload, "chart"),
        asset_type="chart",
        raw_payload=payload,
        normalized=normalized,
        links=links,
    )


def _normalize_dashboard(payload: dict) -> ObservedAssetInput:
    title = payload.get("dashboard_title", "<unknown>")
    links = [
        UnresolvedLink(kind="chart", target_external_id=ref, relation="contains")
        for ref in _chart_refs_from_position(_dashboard_position(payload))
    ]
    normalized = {
        "title": title,
        "description": payload.get("description") or "",
        "owners": [_owner_identity(o) for o in payload.get("owners", [])],
        "published": bool(payload.get("published")),
        "certified": bool(payload.get("certified_by")),
    }
    return ObservedAssetInput(
        external_id=_required_uuid(payload, "dashboard"),
        asset_type="dashboard",
        raw_payload=payload,
        normalized=normalized,
        links=links,
    )


def _volatile_disclosure(payloads: Iterable[dict]) -> list[str]:
    """One warning naming every `*_humanized` key these payloads carried, or
    nothing when they carried none. Emitted by both snapshot paths, because
    both narrow change detection by the same rule."""
    volatile: set[str] = set()
    for payload in payloads:
        volatile |= _volatile_keys(payload)
    if not volatile:
        return []
    return [
        "excluded from change detection as server-rendered relative times (retained in "
        f"the stored payload): {', '.join(sorted(volatile))}"
    ]


def _with_change_detection(item: ObservedAssetInput, payload: dict) -> ObservedAssetInput:
    """The change-detection inputs, applied identically whichever transport
    read the payload: the declared hash basis, and the source's own
    modification time when the payload disclosed one.

    Which read mode produced a payload decides what was read, never how the
    read is hashed. One connection carries a second read mode the moment
    hy-gh-48 lands, and a basis that differed by transport would append an
    immutable version on every switch -- produced by the rule changing rather
    than by the source changing (hy-y8g.3).

    Export YAML carries neither a `*_humanized` key nor a `changed_on`, so
    this is a no-op there today. The point is that it stays a no-op by the
    rule instead of by which branch ran.
    """
    item.hash_basis = _HASH_BASIS
    item.source_modified_at = _source_modified_at(payload)
    return item


class SupersetConnector:
    """hy-gh-27's connector contract:

    test_connection(self) -> ConnectionTest
    snapshot(self, checkpoint) -> ConnectorSnapshot
    normalize(self, snapshot) -> Iterable[ObservedAssetInput]

    Pass `bundle_path` for export mode or `base_url` plus credentials for
    live REST mode; the two transports are mutually exclusive per connector
    instance so a snapshot can never mix them.
    """

    def __init__(
        self,
        *,
        bundle_path: str | Path | None = None,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        provider: str = "db",
        timeout: int = 30,
        session: Any | None = None,
    ):
        if bundle_path is None and base_url is None:
            raise ConnectorError("SupersetConnector requires bundle_path or base_url")
        if bundle_path is not None and base_url is not None:
            raise ConnectorError(
                "SupersetConnector takes bundle_path or base_url, not both: an export bundle "
                "and the REST API are separate upstream contracts (ADR 0003)"
            )
        self.bundle_path = Path(bundle_path) if bundle_path is not None else None
        self._client: SupersetRestClient | None = None
        if base_url is not None:
            if not username or not password:
                raise ConnectorError(
                    "live Superset mode requires username and password; credentials stay "
                    "caller-supplied configuration and are never persisted by the connector"
                )
            self._client = SupersetRestClient(
                base_url=base_url,
                username=username,
                password=password,
                provider=provider,
                timeout=timeout,
                session=session,
            )

    @property
    def transport(self) -> str:
        return "export_bundle" if self._client is None else "rest"

    def test_connection(self) -> ConnectionTest:
        if self._client is not None:
            return self._test_rest_connection()
        if not self.bundle_path.exists():
            return ConnectionTest(ok=False, detail=f"{self.bundle_path} does not exist")
        try:
            bundle = load_export_bundle(self.bundle_path)
        except Exception as exc:  # noqa: BLE001 -- any parse failure is a connection-test failure
            return ConnectionTest(ok=False, detail=f"failed to parse export bundle: {exc}")
        if not any(bundle.values()):
            return ConnectionTest(
                ok=False, detail="export bundle contains no recognized asset types"
            )
        return ConnectionTest(
            ok=True, detail=f"{sum(len(v) for v in bundle.values())} asset(s) found"
        )

    def _test_rest_connection(self) -> ConnectionTest:
        client = self._client
        try:
            client.login()
            databases = client.list_resource("database")
        except ConnectorError as exc:
            return ConnectionTest(ok=False, detail=str(exc))
        return ConnectionTest(
            ok=True, detail=f"authenticated against {client.base_url}, {len(databases)} database(s)"
        )

    def snapshot(self, checkpoint: dict | None = None) -> ConnectorSnapshot:
        """One read of the source. `checkpoint` is the last persisted
        checkpoint for this connection, if any."""
        if self._client is not None:
            return self._rest_snapshot(checkpoint)
        return self._bundle_snapshot()

    def _rest_snapshot(self, checkpoint: dict | None) -> ConnectorSnapshot:
        """Live read: list endpoints discover ids, detail endpoints produce
        the payloads observed versions store (`docs/research/superset-api-v1.md`
        item 1). A full refresh every run -- v0 does not pretend to resume
        incrementally, so the checkpoint is a recorded watermark, not a
        cursor that skips reads.

        ALL FOUR TYPES ARE READ (hy-rt4v), which is what makes the reference
        graph exist on this transport at all: chart --queries--> dataset and
        dashboard --contains--> chart are the only two references either
        source declares, so reading databases and datasets alone left every
        dataset with a live reference count of zero.

        COVERAGE IS NECESSARY AND NO LONGER SUFFICIENT for deletion checking.
        Since hy-6nit a full sync soft-deletes only the types whose snapshot
        carries an `EstablishedDenominator` and declines the rest out loud
        (`hyperset/connectors/sync.py`), and this connector sets none, so
        nothing here is deletion-checked today. Covering a type still has to
        mean the whole type was looked at, because the day a denominator is
        established a partial look would soft-delete live assets. It cannot be
        partial: every call below raises `ConnectorError` on a non-200, which
        fails the whole run before any deletion pass. Coverage is claimed for
        what was looked at, and a partial look is not a look.
        """
        client = self._client
        api_version = client.api_version()
        databases = [
            client.detail("database", row["id"]) for row in client.list_resource("database")
        ]
        datasets = [client.detail("dataset", row["id"]) for row in client.list_resource("dataset")]
        charts = [client.detail("chart", row["id"]) for row in client.list_resource("chart")]
        dashboards = [
            client.detail("dashboard", row["id"]) for row in client.list_resource("dashboard")
        ]

        warnings = [
            "Superset 6.1.0 does not disclose its application version over REST "
            f"(/api/v1/_openapi reports API version {api_version!r}); source_version stays "
            "unknown until the pinned real-source contract suite proves the build",
            "REST list rows were used for discovery only; each observed version stores the "
            "detail payload, a separate upstream contract from the list row",
            "REST chart detail bodies disclose no `changed_on`, so charts carry no source "
            "modification time on this transport; change detection falls back to the payload "
            "hash, which is what it does for every export-read asset",
        ]
        warnings.extend(_volatile_disclosure((*databases, *datasets, *charts, *dashboards)))
        warnings.extend(_unparseable_layout_disclosure(dashboards))

        # Every payload that discloses one, not datasets alone: dashboards
        # carry `changed_on` too, and a watermark that ignored them would
        # understate when the estate last moved the moment a dashboard was the
        # thing that moved.
        watermark = max(
            (
                payload["changed_on"]
                for payload in (*databases, *datasets, *charts, *dashboards)
                if isinstance(payload.get("changed_on"), str)
            ),
            default=None,
        )
        return ConnectorSnapshot(
            source_version=None,
            bundle={
                "databases": databases,
                "datasets": datasets,
                "charts": charts,
                "dashboards": dashboards,
            },
            warnings=warnings,
            transport="rest",
            covered_asset_types=("database", "dataset", "chart", "dashboard"),
            source_capabilities={
                "base_url": client.base_url,
                "api_version": api_version,
                "read_transport": "rest_list_and_detail",
                "application_version_disclosed": False,
            },
            checkpoint={
                "transport": "rest",
                "read_mode": "full_refresh",
                "api_version": api_version,
                "source_version": None,
                "asset_counts": {
                    "database": len(databases),
                    "dataset": len(datasets),
                    "chart": len(charts),
                    "dashboard": len(dashboards),
                },
                "high_watermark_changed_on": watermark,
                "resumed_from_high_watermark": (checkpoint or {}).get("high_watermark_changed_on"),
            },
        )

    def _bundle_snapshot(self) -> ConnectorSnapshot:
        bundle = load_export_bundle(self.bundle_path)
        warnings: list[str] = [
            f"unsupported asset type {name!r} present in bundle, skipped"
            for name in _unsupported_asset_dirs(self.bundle_path)
        ]
        warnings.append(
            "Superset export metadata does not disclose the application version; "
            "support requires the pinned real-source contract suite"
        )
        # Disclosed here too, though export YAML has never carried one: the
        # rule is the connector's, so what it excluded is reported by whichever
        # transport did the reading.
        warnings.extend(
            _volatile_disclosure(payload for payloads in bundle.values() for payload in payloads)
        )
        return ConnectorSnapshot(source_version=None, bundle=bundle, warnings=warnings)

    def normalize(self, snapshot: ConnectorSnapshot) -> Iterator[ObservedAssetInput]:
        """Transport-independent: `snapshot.transport` says what was read, and
        never how it is hashed (see `_with_change_detection`)."""
        for payload in snapshot.bundle.get("databases", []):
            yield _with_change_detection(_normalize_database(payload), payload)
        for payload in snapshot.bundle.get("datasets", []):
            yield _with_change_detection(_normalize_dataset(payload), payload)
        # Reached from either transport since hy-rt4v. The field names differ
        # between them and the normalizers read both, so which one produced a
        # payload does not decide what is observed about it.
        for payload in snapshot.bundle.get("charts", []):
            yield _with_change_detection(_normalize_chart(payload), payload)
        for payload in snapshot.bundle.get("dashboards", []):
            yield _with_change_detection(_normalize_dashboard(payload), payload)
