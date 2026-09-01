#!/usr/bin/env python3
"""Generate authoritative hy-gh-28 evidence from the running Superset 6.1.0 source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import requests
import secret_scan
from demo_bootstrap import (
    BASELINE_METRIC_EXPRESSION,
    DATABASE_NAME,
    DATABASE_UUID,
    DATASETS,
    DRIFT_METRIC_EXPRESSION,
    RECOGNIZED_REVENUE_METRIC_UUID,
    _session,
)

SUPERSET_URL = os.environ["SUPERSET_URL"].rstrip("/")
OUTPUT_DIR = Path(os.environ.get("EVIDENCE_OUTPUT_DIR", "/evidence"))
PINNED_VERSION = os.environ["SUPERSET_PINNED_VERSION"]
PINNED_IMAGE = "apache/superset:6.1.0"
PINNED_IMAGE_DIGEST = "sha256:fb3464528ec7076f91195f0ff7835755aa023e281f1bb78a84782ce7a36b3705"
PROPOSAL_PATH = "tests/fixtures/superset/6.1.0/revenue/proposal.yaml"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(value: object) -> str:
    return _sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _get_raw(session: requests.Session, path: str, *, params: dict | None = None) -> bytes:
    response = session.get(f"{SUPERSET_URL}{path}", params=params, timeout=30)
    response.raise_for_status()
    return response.content


def _result(data: bytes) -> object:
    return json.loads(data)["result"]


def _asset_records(session: requests.Session) -> tuple[dict, dict[str, dict]]:
    database_list = _result(
        _get_raw(
            session,
            "/api/v1/database/",
            params={"q": f"(filters:!((col:database_name,opr:eq,value:{DATABASE_NAME})))"},
        )
    )
    databases = [item for item in database_list if str(item.get("uuid")) == DATABASE_UUID]
    if len(databases) != 1:
        raise RuntimeError(f"expected exactly one database UUID {DATABASE_UUID}, found {databases}")
    database = databases[0]

    dataset_list = _result(
        _get_raw(session, "/api/v1/dataset/", params={"q": "(page:0,page_size:100)"})
    )
    expected = {seed.uuid: seed for seed in DATASETS}
    datasets = {
        str(item.get("uuid")): item for item in dataset_list if str(item.get("uuid")) in expected
    }
    missing = set(expected) - set(datasets)
    if missing:
        raise RuntimeError(f"canonical dataset UUIDs missing from Superset: {sorted(missing)}")
    return database, datasets


def _dataset_detail(session: requests.Session, dataset_id: int) -> dict:
    return _result(_get_raw(session, f"/api/v1/dataset/{dataset_id}"))


def _metric(detail: dict) -> dict:
    matches = [
        item
        for item in detail["metrics"]
        if str(item.get("uuid")) == RECOGNIZED_REVENUE_METRIC_UUID
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected metric UUID {RECOGNIZED_REVENUE_METRIC_UUID}, found {matches}"
        )
    return matches[0]


def _set_expression(session: requests.Session, expression: str) -> dict:
    _, datasets = _asset_records(session)
    approved = datasets[DATASETS[0].uuid]
    detail = _dataset_detail(session, approved["id"])
    metric = _metric(detail)
    updated_metric = {
        key: value
        for key, value in metric.items()
        if key
        in {
            "id",
            "metric_name",
            "description",
            "extra",
            "metric_type",
            "d3format",
            "currency",
            "verbose_name",
            "warning_text",
            "uuid",
        }
    }
    updated_metric["expression"] = expression
    response = session.put(
        f"{SUPERSET_URL}/api/v1/dataset/{approved['id']}",
        json={"metrics": [updated_metric]},
        timeout=30,
    )
    response.raise_for_status()
    return _metric(_dataset_detail(session, approved["id"]))


def _write_raw(path: Path, data: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path.relative_to(OUTPUT_DIR)), "sha256": _sha256(data), "bytes": len(data)}


def _capture(label: str, session: requests.Session) -> dict:
    snapshot_dir = OUTPUT_DIR / label
    database, datasets = _asset_records(session)
    records: dict[str, dict] = {}

    database_list = _get_raw(session, "/api/v1/database/", params={"q": "(page:0,page_size:100)"})
    records["database_list"] = _write_raw(
        snapshot_dir / "rest" / "database-list.json", database_list
    )
    records["database_detail"] = _write_raw(
        snapshot_dir / "rest" / "database-detail.json",
        _get_raw(session, f"/api/v1/database/{database['id']}"),
    )

    dataset_list = _get_raw(session, "/api/v1/dataset/", params={"q": "(page:0,page_size:100)"})
    records["dataset_list"] = _write_raw(snapshot_dir / "rest" / "dataset-list.json", dataset_list)
    details: dict[str, dict] = {}
    for seed in DATASETS:
        item = datasets[seed.uuid]
        raw = _get_raw(session, f"/api/v1/dataset/{item['id']}")
        records[f"dataset_detail_{seed.table_name}"] = _write_raw(
            snapshot_dir / "rest" / f"dataset-{seed.table_name}-detail.json", raw
        )
        details[seed.table_name] = _result(raw)

    ids = ",".join(str(datasets[seed.uuid]["id"]) for seed in DATASETS)
    export_response = session.get(
        f"{SUPERSET_URL}/api/v1/dataset/export/", params={"q": f"!({ids})"}, timeout=60
    )
    export_response.raise_for_status()
    records["official_export"] = _write_raw(
        snapshot_dir / "official-export.zip", export_response.content
    )

    metric = _metric(details[DATASETS[0].table_name])
    return {
        "capture_command": (
            f"make demo-capture-{label}"
            if label in {"baseline", "drift"}
            else "make demo-generate-evidence"
        ),
        "transport": ["official_export_zip", "rest_list", "rest_detail"],
        "records": records,
        "recognized_revenue_metric": metric,
        "controlled_property_hash": _canonical_hash(
            {"uuid": str(metric["uuid"]), "expression": metric["expression"]}
        ),
    }


def _manifest(captures: dict[str, dict], assets: tuple[dict, dict[str, dict]]) -> dict:
    database, datasets = assets
    scan_record = secret_scan.write(OUTPUT_DIR, os.environ)

    dataset_assets = []
    for seed in DATASETS:
        item = datasets[seed.uuid]
        dataset_assets.append(
            {
                "role": (
                    "approved_candidate"
                    if seed.table_name == "finance_orders_daily"
                    else "prohibited_alternative"
                    if seed.table_name == "raw_payments"
                    else "required_dimension"
                ),
                "source_kind": "dataset",
                "native_id": item["id"],
                "native_uuid": seed.uuid,
                "display_name": seed.table_name,
                "product_family_key_inputs": {
                    "source_instance": "local-superset-6.1.0",
                    "source_kind": "dataset",
                    "source_native_identity": seed.uuid,
                },
            }
        )

    return {
        "schema_version": 1,
        "fixture_id": "canonical-recognized-revenue-by-region-v1",
        "canonical_question": (
            "Which source and rules should an analyst use for recognized revenue by region?"
        ),
        "domain": "revenue",
        "business_answer": {
            "approved_source": "finance_orders_daily",
            "metric": "recognized_revenue",
            "definition": (
                "Sum gross amount less tax for completed orders, attributed by order date."
            ),
            "required_grain": "order_date by customer_dim.region",
            "required_filters": [
                "finance_orders_daily.status = 'completed'",
                "customer_dim.is_test = false",
            ],
            "required_join": "finance_orders_daily.customer_id = customer_dim.customer_id",
            "caveats": [
                "Fixture recognition is not GAAP ASC 606 accounting guidance.",
                "Do not substitute payment-event dates for order dates.",
            ],
            "validations": [
                "recognized_revenue is non-negative",
                "monthly totals reconcile within 1% of the fixture close value",
            ],
            "owner": "team:finance-data",
            "review_by": "2026-10-25",
        },
        "source_contract": {
            "source_system": "apache-superset",
            "source_version": PINNED_VERSION,
            "image": PINNED_IMAGE,
            "image_digest": PINNED_IMAGE_DIGEST,
            "config_identity": {
                "profile": "demo",
                "metadata_database": "postgres:16",
                "bootstrap": "docker/superset/demo_bootstrap.py",
            },
            "database": {
                "native_id": database["id"],
                "native_uuid": DATABASE_UUID,
                "display_name": DATABASE_NAME,
            },
            "datasets": dataset_assets,
        },
        "expected_observed_behavior": {
            "stable_identity": "native_uuid",
            "baseline_first_sync": "one immutable observed version per captured source asset",
            "repeat_baseline_sync": "no new material version",
            "drift_sync": "new immutable version for the approved-candidate dataset",
        },
        "controlled_drift": {
            "command": "make demo-generate-evidence",
            "manual_steps": [
                "make demo-drift-apply",
                "make demo-capture-drift",
                "make demo-drift-restore",
            ],
            "source_asset_uuid": DATASETS[0].uuid,
            "metric_uuid": RECOGNIZED_REVENUE_METRIC_UUID,
            "property": "metrics[recognized_revenue].expression",
            "baseline_value": BASELINE_METRIC_EXPRESSION,
            "drift_value": DRIFT_METRIC_EXPRESSION,
            "expected_connector_change_category": "updated",
            "expected_processor_finding_type": "metric_expression_drift",
            "affected_product_family": DATASETS[0].uuid,
            "baseline_property_hash": captures["baseline"]["controlled_property_hash"],
            "drift_property_hash": captures["drift"]["controlled_property_hash"],
            "restored_property_hash": captures["restored"]["controlled_property_hash"],
        },
        "governed_context_proposal": {
            "path": PROPOSAL_PATH,
            "lifecycle": "candidate",
            "review_state": "pending_human_review",
            "approval_decision": None,
            "expected_reviewer_edit": None,
            "observed_source_refs": [
                {"native_uuid": DATASETS[0].uuid, "role": "approved_candidate"},
                {"native_uuid": DATASETS[1].uuid, "role": "required_dimension"},
                {"native_uuid": DATASETS[2].uuid, "role": "prohibited_alternative"},
            ],
        },
        "expected_approved_outcome": {
            "resolution_status": "governed",
            "approved_source_uuid": DATASETS[0].uuid,
            "prohibited_source_uuids": [DATASETS[2].uuid],
            "required_disclosures": [
                "exact observed version",
                "exact governed version",
                "human review decision",
                "freshness",
                "prohibited source",
                "caveat",
                "validation",
                "Hyperset did not execute or validate SQL/results",
            ],
            "required_provenance_references": [
                "source_snapshot",
                "observed_asset_version",
                "review_decision",
                "governed_context_version",
            ],
        },
        "captures": captures,
        "secret_scan": {
            **scan_record,
            "evidence_path": secret_scan.RECORD_NAME,
            "evidence_sha256": _sha256((OUTPUT_DIR / secret_scan.RECORD_NAME).read_bytes()),
        },
    }


def _verify(captures: dict[str, dict]) -> None:
    baseline = captures["baseline"]["recognized_revenue_metric"]
    drift = captures["drift"]["recognized_revenue_metric"]
    restored = captures["restored"]["recognized_revenue_metric"]
    if baseline["expression"] != BASELINE_METRIC_EXPRESSION:
        raise RuntimeError(f"unexpected baseline expression: {baseline['expression']!r}")
    if drift["expression"] != DRIFT_METRIC_EXPRESSION:
        raise RuntimeError(f"unexpected drift expression: {drift['expression']!r}")
    if restored["expression"] != BASELINE_METRIC_EXPRESSION:
        raise RuntimeError(f"restore failed: {restored['expression']!r}")
    if (
        captures["baseline"]["controlled_property_hash"]
        != captures["restored"]["controlled_property_hash"]
    ):
        raise RuntimeError("restore did not return the controlled property to its baseline hash")
    if (
        captures["baseline"]["controlled_property_hash"]
        == captures["drift"]["controlled_property_hash"]
    ):
        raise RuntimeError("drift did not change the controlled property hash")


def generate() -> None:
    session, _ = _session()
    _set_expression(session, BASELINE_METRIC_EXPRESSION)
    captures = {"baseline": _capture("baseline", session)}
    _set_expression(session, DRIFT_METRIC_EXPRESSION)
    captures["drift"] = _capture("drift", session)
    _set_expression(session, BASELINE_METRIC_EXPRESSION)
    captures["restored"] = _capture("restored", session)
    _verify(captures)
    manifest = _manifest(captures, _asset_records(session))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"generated authoritative evidence at {OUTPUT_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("generate", "apply", "restore", "capture-baseline", "capture-drift")
    )
    args = parser.parse_args()
    session, _ = _session()
    if args.action == "generate":
        generate()
    elif args.action == "apply":
        _set_expression(session, DRIFT_METRIC_EXPRESSION)
        print(f"applied metric expression drift: {DRIFT_METRIC_EXPRESSION}")
    elif args.action == "restore":
        _set_expression(session, BASELINE_METRIC_EXPRESSION)
        print(f"restored metric expression: {BASELINE_METRIC_EXPRESSION}")
    elif args.action == "capture-baseline":
        _capture("baseline", session)
    elif args.action == "capture-drift":
        _capture("drift", session)
    return 0


if __name__ == "__main__":
    sys.exit(main())
