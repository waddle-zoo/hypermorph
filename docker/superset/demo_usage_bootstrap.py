#!/usr/bin/env python3
"""Idempotently seed charts and a dashboard onto the canonical revenue datasets.

`demo_bootstrap.py` seeds one database and three datasets -- nothing that makes
a dataset's *use* observable. The two references that do, chart --queries-->
dataset and dashboard --contains--> chart, need real charts and a real
dashboard on the same pinned instance (hy-vzk8).

This seeds them onto the existing revenue datasets rather than new ones. New
datasets would appear in the revenue capture's own `dataset-list.json`, whose
sha256 is pinned; charts and dashboards appear in no endpoint that capture
reads.

Chart and dashboard identity is asserted with an explicit `uuid` on create and
again on update, and every lookup here matches on that UUID: 6.1.0's chart and
dashboard list endpoints both disclose `uuid`, so a display name never has to
stand in for identity (the same rule `demo_bootstrap.py` applies to datasets).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from uuid import UUID

import requests
from demo_bootstrap import DATASETS, _session

SUPERSET_URL = os.environ["SUPERSET_URL"].rstrip("/")

DASHBOARD_TITLE = "Revenue review"
DASHBOARD_UUID = "655abff1-3c4f-5921-96ba-b67616cda208"

_ORDERS = DATASETS[0].uuid
_PAYMENTS = DATASETS[2].uuid


@dataclass(frozen=True)
class ChartSeed:
    slice_name: str
    uuid: str
    dataset_uuid: str
    description: str
    viz_type: str
    groupby: tuple[str, ...]
    metric: str


# Two charts on the approved candidate and one on the prohibited alternative:
# a reference count over this seed distinguishes the datasets instead of
# tying, and customer_dim stays at zero so "no chart references it" is also
# observable.
CHARTS = (
    ChartSeed(
        slice_name="Recognized revenue by status",
        uuid="6f0af5cf-146c-56c7-a57d-0912550108f9",
        dataset_uuid=_ORDERS,
        description="Observed Superset chart over the approved-candidate order source.",
        viz_type="table",
        groupby=("status",),
        metric="recognized_revenue",
    ),
    ChartSeed(
        slice_name="Recognized revenue by order date",
        uuid="4d82dc9e-fe4b-5989-b5cb-3f20eb7c1643",
        dataset_uuid=_ORDERS,
        description="Second observed chart on the same dataset, so reference counts can differ.",
        viz_type="table",
        groupby=("order_date",),
        metric="recognized_revenue",
    ),
    ChartSeed(
        slice_name="Raw payment volume by gateway",
        uuid="73995395-7d07-58ea-90b7-65330d4f3b22",
        dataset_uuid=_PAYMENTS,
        description="Observed chart over the prohibited revenue alternative.",
        viz_type="table",
        groupby=("gateway",),
        metric="raw_payment_amount",
    ),
)


def _dataset_ids(session: requests.Session, headers: dict) -> dict[str, int]:
    resp = session.get(
        f"{SUPERSET_URL}/api/v1/dataset/",
        params={"q": "(page:0,page_size:100)"},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    ids = {str(item.get("uuid")): item["id"] for item in resp.json()["result"]}
    missing = [seed.uuid for seed in DATASETS if seed.uuid not in ids]
    if missing:
        raise RuntimeError(
            f"canonical dataset UUIDs missing from Superset: {missing}; "
            "run the revenue bootstrap before seeding charts onto it"
        )
    return ids


def _params(seed: ChartSeed, dataset_id: int) -> str:
    return json.dumps(
        {
            "datasource": f"{dataset_id}__table",
            "viz_type": seed.viz_type,
            "query_mode": "aggregate",
            "groupby": list(seed.groupby),
            "metrics": [seed.metric],
            "row_limit": 1000,
        },
        sort_keys=True,
    )


def _find_chart(session: requests.Session, headers: dict, seed: ChartSeed) -> dict | None:
    """Locate a seeded chart by UUID, never by display name -- names are display
    locators, not identity, and the chart list discloses `uuid`."""
    resp = session.get(
        f"{SUPERSET_URL}/api/v1/chart/",
        params={"q": "(page:0,page_size:100)"},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()["result"]
    identity_match = next((item for item in results if str(item.get("uuid")) == seed.uuid), None)
    if identity_match is not None:
        return identity_match
    name_conflicts = [item for item in results if item["slice_name"] == seed.slice_name]
    if name_conflicts:
        raise RuntimeError(
            f"chart name {seed.slice_name!r} exists without canonical UUID {seed.uuid}; "
            "names are display locators, not stable identity"
        )
    return None


def _ensure_chart(
    session: requests.Session, headers: dict, dataset_ids: dict[str, int], seed: ChartSeed
) -> int:
    dataset_id = dataset_ids[seed.dataset_uuid]
    payload = {
        "slice_name": seed.slice_name,
        "description": seed.description,
        "viz_type": seed.viz_type,
        "datasource_id": dataset_id,
        "datasource_type": "table",
        "params": _params(seed, dataset_id),
        "uuid": seed.uuid,
    }
    existing = _find_chart(session, headers, seed)
    if existing is None:
        resp = session.post(
            f"{SUPERSET_URL}/api/v1/chart/", json=payload, headers=headers, timeout=30
        )
        if resp.status_code >= 400:
            print(
                f"failed to create chart {seed.slice_name!r}: {resp.status_code} {resp.text}",
                file=sys.stderr,
            )
            resp.raise_for_status()
        chart_id = resp.json()["id"]
        print(f"created chart {seed.slice_name!r} (id={chart_id}, uuid={seed.uuid})")
        return chart_id

    chart_id = existing["id"]
    resp = session.put(
        f"{SUPERSET_URL}/api/v1/chart/{chart_id}", json=payload, headers=headers, timeout=30
    )
    if resp.status_code >= 400:
        print(
            f"failed to normalize chart {seed.slice_name!r}: {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        resp.raise_for_status()
    print(f"normalized chart {seed.slice_name!r} (id={chart_id}, uuid={seed.uuid})")
    return chart_id


def _position(chart_ids: dict[str, int]) -> str:
    """The 6.1.0 layout tree a UI-built dashboard stores, written explicitly.

    Superset's exporter appends any chart the position omits into a row with a
    random component id (`append_charts`), which would make the captured bytes
    differ per export. Declaring the position keeps the capture reproducible.
    """
    chart_components = {
        f"CHART-usage-{index}": {
            "children": [],
            "id": f"CHART-usage-{index}",
            "meta": {
                "chartId": chart_ids[seed.uuid],
                "height": 50,
                "sliceName": seed.slice_name,
                "uuid": seed.uuid,
                "width": 4,
            },
            "parents": ["ROOT_ID", "GRID_ID", "ROW-usage-1"],
            "type": "CHART",
        }
        for index, seed in enumerate(CHARTS, start=1)
    }
    position = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"children": ["GRID_ID"], "id": "ROOT_ID", "type": "ROOT"},
        "GRID_ID": {
            "children": ["ROW-usage-1"],
            "id": "GRID_ID",
            "parents": ["ROOT_ID"],
            "type": "GRID",
        },
        "HEADER_ID": {"id": "HEADER_ID", "meta": {"text": DASHBOARD_TITLE}, "type": "HEADER"},
        "ROW-usage-1": {
            "children": list(chart_components),
            "id": "ROW-usage-1",
            "meta": {"0": "ROOT_ID", "background": "BACKGROUND_TRANSPARENT"},
            "parents": ["ROOT_ID", "GRID_ID"],
            "type": "ROW",
        },
        **chart_components,
    }
    return json.dumps(position, sort_keys=True)


def _find_dashboard(session: requests.Session, headers: dict) -> dict | None:
    resp = session.get(
        f"{SUPERSET_URL}/api/v1/dashboard/",
        params={"q": "(page:0,page_size:100)"},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()["result"]
    identity_match = next(
        (item for item in results if str(item.get("uuid")) == DASHBOARD_UUID), None
    )
    if identity_match is not None:
        return identity_match
    title_conflicts = [item for item in results if item["dashboard_title"] == DASHBOARD_TITLE]
    if title_conflicts:
        raise RuntimeError(
            f"dashboard title {DASHBOARD_TITLE!r} exists without canonical UUID "
            f"{DASHBOARD_UUID}; titles are display locators, not stable identity"
        )
    return None


def _ensure_dashboard(session: requests.Session, headers: dict, chart_ids: dict[str, int]) -> int:
    payload = {
        "dashboard_title": DASHBOARD_TITLE,
        "published": True,
        "position_json": _position(chart_ids),
        "uuid": DASHBOARD_UUID,
    }
    existing = _find_dashboard(session, headers)
    if existing is None:
        resp = session.post(
            f"{SUPERSET_URL}/api/v1/dashboard/", json=payload, headers=headers, timeout=30
        )
        if resp.status_code >= 400:
            print(
                f"failed to create dashboard {DASHBOARD_TITLE!r}: {resp.status_code} {resp.text}",
                file=sys.stderr,
            )
            resp.raise_for_status()
        dashboard_id = resp.json()["id"]
        print(f"created dashboard {DASHBOARD_TITLE!r} (id={dashboard_id}, uuid={DASHBOARD_UUID})")
        return dashboard_id

    dashboard_id = existing["id"]
    resp = session.put(
        f"{SUPERSET_URL}/api/v1/dashboard/{dashboard_id}",
        json=payload,
        headers=headers,
        timeout=30,
    )
    if resp.status_code >= 400:
        print(
            f"failed to normalize dashboard {DASHBOARD_TITLE!r}: {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        resp.raise_for_status()
    print(f"normalized dashboard {DASHBOARD_TITLE!r} (id={dashboard_id})")
    return dashboard_id


def _attach_charts(
    session: requests.Session, headers: dict, dashboard_id: int, chart_ids: dict[str, int]
) -> None:
    """Put every seeded chart on the dashboard.

    `position_json` alone does not do this: Superset stores the layout and the
    dashboard/chart association separately, and `ExportDashboardsCommand`
    exports `model.slices` -- so a dashboard whose charts are only named in its
    position exports as a single YAML with no charts, datasets or database in
    the archive. The association is settable from the chart side only, which is
    why it runs after the dashboard exists.
    """
    for seed in CHARTS:
        chart_id = chart_ids[seed.uuid]
        resp = session.put(
            f"{SUPERSET_URL}/api/v1/chart/{chart_id}",
            json={"dashboards": [dashboard_id]},
            headers=headers,
            timeout=30,
        )
        if resp.status_code >= 400:
            print(
                f"failed to attach chart {seed.slice_name!r} to dashboard {dashboard_id}: "
                f"{resp.status_code} {resp.text}",
                file=sys.stderr,
            )
            resp.raise_for_status()
        print(f"attached chart {seed.slice_name!r} to dashboard (id={dashboard_id})")


def main() -> int:
    session, headers = _session()
    dataset_ids = _dataset_ids(session, headers)
    UUID(DASHBOARD_UUID)
    chart_ids: dict[str, int] = {}
    for seed in CHARTS:
        UUID(seed.uuid)
        chart_ids[seed.uuid] = _ensure_chart(session, headers, dataset_ids, seed)
    dashboard_id = _ensure_dashboard(session, headers, chart_ids)
    _attach_charts(session, headers, dashboard_id, chart_ids)
    print("usage bootstrap complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
