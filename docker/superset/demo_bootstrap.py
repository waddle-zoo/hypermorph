#!/usr/bin/env python3
"""Idempotently seed the canonical hy-gh-28 revenue assets through Superset REST."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from uuid import UUID

import requests

SUPERSET_URL = os.environ["SUPERSET_URL"].rstrip("/")
ADMIN_USERNAME = os.environ.get("SUPERSET_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["SUPERSET_ADMIN_PASSWORD"]
ANALYTICS_DB_URI = os.environ["ANALYTICS_DATABASE_URI"]

DATABASE_NAME = "analytics"
DATABASE_UUID = "191e8838-4a5c-5f3f-9d53-71f52f56f7f8"
RECOGNIZED_REVENUE_METRIC_UUID = "cb35591d-82e2-54cf-959a-c22e79e5d468"
RAW_PAYMENT_AMOUNT_METRIC_UUID = "49afcc3e-b1b4-53aa-b27a-b6bdcf32fa42"
BASELINE_METRIC_EXPRESSION = "SUM(gross_amount - tax_amount)"
DRIFT_METRIC_EXPRESSION = "SUM(gross_amount)"


@dataclass(frozen=True)
class DatasetSeed:
    table_name: str
    uuid: str
    description: str
    metric_name: str | None = None
    metric_uuid: str | None = None
    metric_expression: str | None = None


DATASETS = (
    DatasetSeed(
        table_name="finance_orders_daily",
        uuid="ae48881d-334f-54a7-94e8-1ffcc73866e2",
        description=(
            "Approved-candidate order source for recognized revenue. Source metadata is "
            "observed evidence only; approval requires a Hyperset ReviewDecision."
        ),
        metric_name="recognized_revenue",
        metric_uuid=RECOGNIZED_REVENUE_METRIC_UUID,
        metric_expression=BASELINE_METRIC_EXPRESSION,
    ),
    DatasetSeed(
        table_name="customer_dim",
        uuid="5bcf01e3-3f70-50d2-bb31-562b627b09b8",
        description="Customer region and test-account flags required by the revenue proposal.",
    ),
    DatasetSeed(
        table_name="raw_payments",
        uuid="6f4976c2-25ea-5d98-b714-9ca8e6c9b7e4",
        description=(
            "Plausible but prohibited revenue alternative: gateway events include duplicate "
            "authorizations, partial captures, and sandbox traffic."
        ),
        metric_name="raw_payment_amount",
        metric_uuid=RAW_PAYMENT_AMOUNT_METRIC_UUID,
        metric_expression="SUM(amount)",
    ),
)


def _session() -> tuple[requests.Session, dict]:
    session = requests.Session()
    login = session.post(
        f"{SUPERSET_URL}/api/v1/security/login",
        json={
            "username": ADMIN_USERNAME,
            "password": ADMIN_PASSWORD,
            "provider": "db",
            "refresh": True,
        },
        timeout=30,
    )
    login.raise_for_status()
    access_token = login.json()["access_token"]
    session.headers["Authorization"] = f"Bearer {access_token}"

    csrf = session.get(f"{SUPERSET_URL}/api/v1/security/csrf_token/", timeout=30)
    csrf.raise_for_status()
    session.headers["X-CSRFToken"] = csrf.json()["result"]
    return session, {"Referer": SUPERSET_URL}


def _find_database(session: requests.Session, headers: dict) -> dict | None:
    resp = session.get(
        f"{SUPERSET_URL}/api/v1/database/",
        params={"q": f"(filters:!((col:database_name,opr:eq,value:{DATABASE_NAME})))"},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()["result"]
    return results[0] if results else None


def _ensure_database(session: requests.Session, headers: dict) -> int:
    existing = _find_database(session, headers)
    if existing is not None:
        if existing.get("uuid") and str(existing["uuid"]) != DATABASE_UUID:
            raise RuntimeError(
                f"database {DATABASE_NAME!r} has unexpected UUID {existing['uuid']}; "
                "run the explicit demo reset before claiming deterministic identity"
            )
        print(f"database {DATABASE_NAME!r} already exists (id={existing['id']})")
        return existing["id"]

    resp = session.post(
        f"{SUPERSET_URL}/api/v1/database/",
        json={
            "database_name": DATABASE_NAME,
            "sqlalchemy_uri": ANALYTICS_DB_URI,
            "expose_in_sqllab": True,
            "uuid": DATABASE_UUID,
        },
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    database_id = resp.json()["id"]
    print(f"created database {DATABASE_NAME!r} (id={database_id})")
    return database_id


def _find_dataset(session: requests.Session, headers: dict, seed: DatasetSeed) -> dict | None:
    resp = session.get(
        f"{SUPERSET_URL}/api/v1/dataset/",
        params={"q": "(page:0,page_size:100)"},
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()["result"]
    identity_match = next((item for item in results if str(item.get("uuid")) == seed.uuid), None)
    if identity_match is not None:
        if identity_match["table_name"] != seed.table_name:
            raise RuntimeError(
                f"dataset UUID {seed.uuid} resolves to {identity_match['table_name']!r}, "
                f"expected {seed.table_name!r}"
            )
        return identity_match
    name_conflicts = [item for item in results if item["table_name"] == seed.table_name]
    if name_conflicts:
        raise RuntimeError(
            f"dataset name {seed.table_name!r} exists without canonical UUID {seed.uuid}; "
            "names are display locators, not stable identity"
        )
    return None


def _dataset_detail(session: requests.Session, headers: dict, dataset_id: int) -> dict:
    resp = session.get(f"{SUPERSET_URL}/api/v1/dataset/{dataset_id}", headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()["result"]


def _ensure_dataset(
    session: requests.Session, headers: dict, database_id: int, seed: DatasetSeed
) -> int:
    existing = _find_dataset(session, headers, seed)
    if existing is None:
        resp = session.post(
            f"{SUPERSET_URL}/api/v1/dataset/",
            json={
                "database": database_id,
                "table_name": seed.table_name,
                "schema": "public",
                "uuid": seed.uuid,
            },
            headers=headers,
            timeout=30,
        )
        if resp.status_code >= 400:
            print(
                f"failed to create dataset {seed.table_name!r}: {resp.status_code} {resp.text}",
                file=sys.stderr,
            )
            resp.raise_for_status()
        dataset_id = resp.json()["id"]
        print(f"created dataset {seed.table_name!r} (id={dataset_id})")
    else:
        dataset_id = existing["id"]

    detail = _dataset_detail(session, headers, dataset_id)
    if str(detail["uuid"]) != seed.uuid:
        raise RuntimeError(
            f"dataset {seed.table_name!r} has unexpected UUID {detail['uuid']}; "
            "run the explicit demo reset before claiming deterministic identity"
        )

    metrics = detail.get("metrics", [])
    update_payload = {"description": seed.description, "uuid": seed.uuid}
    if seed.metric_name:
        matching = next(
            (metric for metric in metrics if metric["metric_name"] == seed.metric_name), None
        )
        metric = {
            "metric_name": seed.metric_name,
            "expression": seed.metric_expression,
            "description": (
                "Observed Superset metric for the canonical revenue fixture. "
                "It is not approved governed meaning."
            ),
            "uuid": seed.metric_uuid,
        }
        if matching is not None:
            metric["id"] = matching["id"]
            metric["uuid"] = matching.get("uuid") or seed.metric_uuid
        update_payload["metrics"] = [metric]

    resp = session.put(
        f"{SUPERSET_URL}/api/v1/dataset/{dataset_id}",
        json=update_payload,
        headers=headers,
        timeout=30,
    )
    if resp.status_code >= 400:
        print(
            f"failed to normalize dataset {seed.table_name!r}: {resp.status_code} {resp.text}",
            file=sys.stderr,
        )
        resp.raise_for_status()
    print(f"normalized dataset {seed.table_name!r} (id={dataset_id}, uuid={seed.uuid})")
    return dataset_id


def main() -> int:
    session, headers = _session()
    database_id = _ensure_database(session, headers)
    UUID(DATABASE_UUID)
    for seed in DATASETS:
        UUID(seed.uuid)
        _ensure_dataset(session, headers, database_id, seed)
    print("demo bootstrap complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
