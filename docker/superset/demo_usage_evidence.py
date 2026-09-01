#!/usr/bin/env python3
"""Capture the chart and dashboard references from the running Superset 6.1.0 source.

The revenue capture (`demo_evidence.py`) covers one database and three
datasets, which is everything the revenue question needs and nothing that makes
a dataset's use observable. This captures the seeded charts and dashboard from
the same pinned instance so that chart --queries--> dataset and
dashboard --contains--> chart rest on real evidence instead of an export bundle
assembled inside a test (hy-vzk8).

Both transports are captured, and they are captured for different reasons:

- `official-export.zip` is the evidence the connector reads today. A dashboard
  export carries the dashboard, its charts, their datasets and the database, so
  one archive holds both reference kinds.
- the REST bodies pin what live REST *does* disclose about these types, which
  turned out to be more than this connector assumed. 6.1.0's chart detail
  carries `uuid` and `datasource_uuid`, and its dashboard detail carries `uuid`
  and `position_json` -- so both references are readable over REST too, under
  different field names and with the layout as a JSON string. That is a
  separate connector slice; this fixture is the evidence it needs, and the
  assertions below fail if a future pin stops disclosing any of it.

Reuses the revenue generator's hashing and writing helpers, and the shared
`secret_scan` module -- the manifest discipline is the same discipline, and
`EVIDENCE_OUTPUT_DIR` is what chooses which fixture directory it applies to.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from io import BytesIO

import requests
import secret_scan
import yaml
from demo_bootstrap import DATABASE_NAME, DATABASE_UUID, DATASETS, _session
from demo_evidence import (
    OUTPUT_DIR,
    PINNED_IMAGE,
    PINNED_IMAGE_DIGEST,
    PINNED_VERSION,
    SUPERSET_URL,
    _get_raw,
    _sha256,
    _write_raw,
)
from demo_usage_bootstrap import CHARTS, DASHBOARD_TITLE, DASHBOARD_UUID


def _result(data: bytes) -> object:
    return json.loads(data)["result"]


def _charts(session: requests.Session) -> tuple[bytes, list[dict]]:
    raw = _get_raw(session, "/api/v1/chart/", params={"q": "(page:0,page_size:100)"})
    by_uuid = {str(row.get("uuid")): row for row in _result(raw)}
    missing = [seed.uuid for seed in CHARTS if seed.uuid not in by_uuid]
    if missing:
        raise RuntimeError(
            f"seeded chart UUIDs missing from Superset: {missing}; "
            "run the usage bootstrap before capturing it"
        )
    return raw, [by_uuid[seed.uuid] for seed in CHARTS]


def _dashboard(session: requests.Session) -> tuple[bytes, dict]:
    raw = _get_raw(session, "/api/v1/dashboard/", params={"q": "(page:0,page_size:100)"})
    matches = [row for row in _result(raw) if str(row.get("uuid")) == DASHBOARD_UUID]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one dashboard with UUID {DASHBOARD_UUID}, found {len(matches)}"
        )
    return raw, matches[0]


def _export_identities(data: bytes) -> dict[str, list[dict]]:
    """The identities the export archive actually declares, read back from the
    bytes that were captured.

    Read from the archive rather than from the seed script, so what the manifest
    claims about the captured evidence comes from the evidence.
    """
    identities: dict[str, list[dict]] = {"charts": [], "dashboards": [], "datasets": []}
    with zipfile.ZipFile(BytesIO(data)) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/") or not name.endswith(".yaml"):
                continue
            parts = name.split("/")
            if "charts" in parts:
                payload = yaml.safe_load(archive.read(name))
                identities["charts"].append(
                    {
                        "display_name": payload["slice_name"],
                        "native_uuid": str(payload["uuid"]),
                        "queries_dataset_uuid": str(payload["dataset_uuid"]),
                    }
                )
            elif "dashboards" in parts:
                payload = yaml.safe_load(archive.read(name))
                identities["dashboards"].append(
                    {
                        "display_name": payload["dashboard_title"],
                        "native_uuid": str(payload["uuid"]),
                        "contains_chart_uuids": sorted(
                            str(component["meta"]["uuid"])
                            for component in payload["position"].values()
                            if isinstance(component, dict) and component.get("type") == "CHART"
                        ),
                    }
                )
            elif "datasets" in parts:
                payload = yaml.safe_load(archive.read(name))
                identities["datasets"].append(
                    {
                        "display_name": payload["table_name"],
                        "native_uuid": str(payload["uuid"]),
                    }
                )
    return identities


def _verify(identities: dict[str, list[dict]]) -> None:
    seeded_charts = {seed.uuid: seed for seed in CHARTS}
    captured_charts = {chart["native_uuid"]: chart for chart in identities["charts"]}
    if set(captured_charts) != set(seeded_charts):
        raise RuntimeError(
            "captured chart UUIDs do not match the seed: "
            f"captured {sorted(captured_charts)}, seeded {sorted(seeded_charts)}"
        )
    for uuid_value, seed in seeded_charts.items():
        captured_target = captured_charts[uuid_value]["queries_dataset_uuid"]
        if captured_target != seed.dataset_uuid:
            raise RuntimeError(
                f"chart {seed.slice_name!r} references dataset {captured_target}, "
                f"expected {seed.dataset_uuid}"
            )
    if [dashboard["native_uuid"] for dashboard in identities["dashboards"]] != [DASHBOARD_UUID]:
        raise RuntimeError(
            f"expected exactly the seeded dashboard {DASHBOARD_UUID}, "
            f"captured {[d['native_uuid'] for d in identities['dashboards']]}"
        )
    contained = identities["dashboards"][0]["contains_chart_uuids"]
    if contained != sorted(seeded_charts):
        raise RuntimeError(
            f"dashboard contains {contained}, expected every seeded chart {sorted(seeded_charts)}"
        )


def _rest_disclosure(chart_details: list[dict], dashboard_detail: dict) -> dict:
    """What the captured REST bodies disclose about the two references.

    Asserted, not described. Every field named here is one a REST snapshot of
    charts and dashboards would have to read, and the field names differ from
    the export bundle's (`datasource_uuid` for `dataset_uuid`, a `position_json`
    string for a `position` mapping) -- which is exactly why reading them is a
    connector slice of its own and not part of this capture.
    """
    for detail in chart_details:
        for field in ("uuid", "datasource_uuid"):
            if not detail.get(field):
                raise RuntimeError(
                    f"chart detail for {detail.get('slice_name')!r} discloses no {field!r}; "
                    "the captured reference evidence would be unidentifiable"
                )
    for field in ("uuid", "position_json"):
        if not dashboard_detail.get(field):
            raise RuntimeError(f"dashboard detail discloses no {field!r}")

    position = json.loads(dashboard_detail["position_json"])
    rest_contained = sorted(
        str(component["meta"]["uuid"])
        for component in position.values()
        if isinstance(component, dict) and component.get("type") == "CHART"
    )
    if rest_contained != sorted(seed.uuid for seed in CHARTS):
        raise RuntimeError(
            f"dashboard REST position names charts {rest_contained}, "
            f"expected every seeded chart {sorted(seed.uuid for seed in CHARTS)}"
        )

    return {
        "endpoints_checked": [
            "/api/v1/chart/",
            "/api/v1/chart/{id}",
            "/api/v1/dashboard/",
            "/api/v1/dashboard/{id}",
        ],
        "chart_identity_field": "uuid",
        "chart_reference_field": "datasource_uuid",
        "dashboard_identity_field": "uuid",
        "dashboard_reference_field": "position_json",
        "dashboard_reference_shape": (
            "a JSON string holding the same layout tree the export carries as a mapping; "
            "CHART components name the chart by meta.uuid in both"
        ),
        "rest_contains_chart_uuids": rest_contained,
        "consequence": (
            "live REST can identify and relate both types, under different field names than "
            "the export transport; the connector reads both spellings, so a chart's dataset "
            "and a dashboard's charts are observed on either transport (hy-rt4v)"
        ),
    }


def _capture(session: requests.Session) -> dict:
    records: dict[str, dict] = {}

    chart_list, chart_rows = _charts(session)
    records["chart_list"] = _write_raw(OUTPUT_DIR / "rest" / "chart-list.json", chart_list)
    chart_details = []
    for seed, row in zip(CHARTS, chart_rows, strict=True):
        raw = _get_raw(session, f"/api/v1/chart/{row['id']}")
        slug = seed.slice_name.lower().replace(" ", "_")
        records[f"chart_detail_{slug}"] = _write_raw(
            OUTPUT_DIR / "rest" / f"chart-{slug}-detail.json", raw
        )
        chart_details.append(_result(raw))

    dashboard_list, dashboard = _dashboard(session)
    records["dashboard_list"] = _write_raw(
        OUTPUT_DIR / "rest" / "dashboard-list.json", dashboard_list
    )
    dashboard_detail_raw = _get_raw(session, f"/api/v1/dashboard/{dashboard['id']}")
    records["dashboard_detail"] = _write_raw(
        OUTPUT_DIR / "rest" / "dashboard-detail.json", dashboard_detail_raw
    )

    export_response = session.get(
        f"{SUPERSET_URL}/api/v1/dashboard/export/",
        params={"q": f"!({dashboard['id']})"},
        timeout=60,
    )
    export_response.raise_for_status()
    records["official_export"] = _write_raw(
        OUTPUT_DIR / "official-export.zip", export_response.content
    )

    identities = _export_identities(export_response.content)
    _verify(identities)
    return {
        "capture_command": "make demo-generate-usage-evidence",
        "transport": ["official_export_zip", "rest_list", "rest_detail"],
        "records": records,
        "export_identities": identities,
        "rest_disclosure": _rest_disclosure(chart_details, _result(dashboard_detail_raw)),
        "native_ids": {
            "dashboard": dashboard["id"],
            "charts": {
                seed.slice_name: row["id"] for seed, row in zip(CHARTS, chart_rows, strict=True)
            },
        },
    }


def _manifest(capture: dict) -> dict:
    scan_record = secret_scan.write(OUTPUT_DIR, os.environ)

    charts = {chart["native_uuid"]: chart for chart in capture["export_identities"]["charts"]}
    reference_counts: dict[str, int] = {seed.uuid: 0 for seed in DATASETS}
    for chart in charts.values():
        reference_counts[chart["queries_dataset_uuid"]] += 1

    return {
        "schema_version": 1,
        "fixture_id": "canonical-revenue-review-usage-v1",
        "purpose": (
            "Real evidence that the two references a dataset's use is countable from -- "
            "chart --queries--> dataset and dashboard --contains--> chart -- exist in the "
            "pinned source and survive a sync as persisted relationships."
        ),
        "domain": "revenue",
        "extends_fixture": "canonical-recognized-revenue-by-region-v1",
        "source_contract": {
            "source_system": "apache-superset",
            "source_version": PINNED_VERSION,
            "image": PINNED_IMAGE,
            "image_digest": PINNED_IMAGE_DIGEST,
            "config_identity": {
                "profile": "demo",
                "metadata_database": "postgres:16",
                "bootstrap": "docker/superset/demo_usage_bootstrap.py",
                "seeded_onto": "docker/superset/demo_bootstrap.py",
            },
            "database": {"native_uuid": DATABASE_UUID, "display_name": DATABASE_NAME},
            "charts": [
                {
                    "native_uuid": seed.uuid,
                    "display_name": seed.slice_name,
                    "queries_dataset_uuid": seed.dataset_uuid,
                }
                for seed in CHARTS
            ],
            "dashboards": [
                {
                    "native_uuid": DASHBOARD_UUID,
                    "display_name": DASHBOARD_TITLE,
                    "contains_chart_uuids": sorted(charts),
                }
            ],
        },
        "expected_observed_references": {
            "chart_queries_dataset": {
                chart["native_uuid"]: chart["queries_dataset_uuid"] for chart in charts.values()
            },
            "dashboard_contains_chart": {DASHBOARD_UUID: sorted(charts)},
            "dataset_reference_counts": reference_counts,
            "uncovered_by_this_capture": {
                "dataset_uuids": [
                    seed.uuid for seed in DATASETS if reference_counts[seed.uuid] == 0
                ],
                "reason": (
                    "no seeded chart queries them, so a dashboard export of the seeded "
                    "dashboard carries neither the dataset nor a reference to it"
                ),
            },
        },
        "rest_disclosure": capture["rest_disclosure"],
        "captures": {"seeded": capture},
        "secret_scan": {
            **scan_record,
            "evidence_path": secret_scan.RECORD_NAME,
            "evidence_sha256": _sha256((OUTPUT_DIR / secret_scan.RECORD_NAME).read_bytes()),
        },
    }


def generate() -> None:
    session, _ = _session()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(_capture(session))
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"generated usage evidence at {OUTPUT_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("generate",))
    parser.parse_args()
    generate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
