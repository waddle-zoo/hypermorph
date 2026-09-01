#!/usr/bin/env python3
"""Seed the canonical revenue evidence into the pinned DataHub OSS v1.6.0 instance.

Only what ADR 0010 asks DataHub to supply for the shared revenue scenario:
catalog identity, domain membership, ownership, a glossary term, and one
explicit lineage edge to the Superset-backed warehouse table. Nothing here
is approved business truth -- it is source evidence Hyperset observes.

Writes go through GMS's OpenAPI v3 batch entity endpoints
(`POST /openapi/v3/entity/<entity>`), which is what the pinned build
actually serves; `/openapi/v3/api-docs/openapi-v3` on the running instance
is the contract this script was written against.

Idempotent: re-running writes the same aspect values, so an unchanged
re-seed leaves DataHub's own `lastObserved` alone in content terms and
Hyperset's resync stays a no-op.

Subcommands:
    seed      write the baseline revenue evidence
    apply     apply the one controlled metadata drift
    restore   restore the baseline value
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests

from hyperset.config.connection_settings import datahub_token

GMS_URL = os.environ.get("DATAHUB_GMS_URL", "http://datahub-gms:8080").rstrip("/")
# Through the settings accessor (hy-py62a) so this shipped Compose entrypoint reads the DataHub
# token the same way the connectors do; standalone (no apply_startup_config), it is the exact
# prior env read. `or None` preserves the original present-empty -> None (auth off).
TOKEN = datahub_token() or None

# --- URNs: DataHub's native identity, used verbatim as Hyperset's external id ---

PLATFORM_WAREHOUSE = "urn:li:dataPlatform:postgres"
PLATFORM_BI = "urn:li:dataPlatform:superset"

WAREHOUSE_TABLE = "analytics.public.finance_orders_daily"
WAREHOUSE_DATASET_URN = f"urn:li:dataset:({PLATFORM_WAREHOUSE},{WAREHOUSE_TABLE},PROD)"

# The Superset-side dataset is keyed by Superset's own native UUID, taken
# from tests/fixtures/superset/6.1.0/revenue/manifest.json. That is what
# makes the cross-source link an explicit identifier match rather than a
# display-name guess: the same UUID appears in Superset's REST payload.
SUPERSET_DATASET_UUID = "ae48881d-334f-54a7-94e8-1ffcc73866e2"
SUPERSET_DATASET_ID = 1
SUPERSET_INSTANCE = "local-superset-6.1.0"
BI_DATASET_URN = f"urn:li:dataset:({PLATFORM_BI},{SUPERSET_DATASET_UUID},PROD)"

DOMAIN_URN = "urn:li:domain:revenue"
OWNER_URN = "urn:li:corpuser:revenue_owner"
GLOSSARY_TERM_URN = "urn:li:glossaryTerm:recognized_revenue"

BASELINE_TERM_DEFINITION = (
    "Revenue recognized on completed, non-refunded orders, excluding tax and test accounts."
)
DRIFT_TERM_DEFINITION = (
    "Revenue recognized on all orders, including refunded orders, excluding tax."
)

WAREHOUSE_COLUMNS = [
    ("order_id", "string"),
    ("customer_id", "string"),
    ("order_date", "date"),
    ("status", "string"),
    ("gross_amount", "number"),
    ("tax_amount", "number"),
    ("currency", "string"),
]

_TYPE_CLASS = {
    "string": "com.linkedin.schema.StringType",
    "number": "com.linkedin.schema.NumberType",
    "date": "com.linkedin.schema.DateType",
}


def _session() -> requests.Session:
    session = requests.Session()
    session.headers["Content-Type"] = "application/json"
    if TOKEN:
        session.headers["Authorization"] = f"Bearer {TOKEN}"
    return session


def _post(session: requests.Session, entity: str, payload: list[dict]) -> list[dict]:
    """One batch write. `async=false` so the aspects are queryable the
    moment this returns -- a seed that races its own reader is not a
    deterministic fixture."""
    response = session.post(
        f"{GMS_URL}/openapi/v3/entity/{entity}",
        params={"async": "false"},
        data=json.dumps(payload),
        timeout=60,
    )
    if response.status_code >= 400:
        raise SystemExit(f"{entity} write failed: HTTP {response.status_code}\n{response.text}")
    return response.json()


def _aspect(value: dict) -> dict:
    return {"value": value}


def _schema_field(name: str, native_type: str) -> dict:
    return {
        "fieldPath": name,
        "nullable": False,
        "type": {"type": {_TYPE_CLASS[native_type]: {}}},
        "nativeDataType": native_type,
        "recursive": False,
    }


def _warehouse_dataset() -> dict:
    return {
        "urn": WAREHOUSE_DATASET_URN,
        "datasetProperties": _aspect(
            {
                "name": "finance_orders_daily",
                "qualifiedName": WAREHOUSE_TABLE,
                "description": (
                    "Daily finance order facts. Authoritative source for recognized revenue."
                ),
                "customProperties": {
                    "warehouse_schema": "public",
                    "warehouse_table": "finance_orders_daily",
                },
                "tags": [],
            }
        ),
        "schemaMetadata": _aspect(
            {
                "schemaName": WAREHOUSE_TABLE,
                "platform": PLATFORM_WAREHOUSE,
                "version": 0,
                "hash": "",
                "platformSchema": {"com.linkedin.schema.MySqlDDL": {"tableSchema": ""}},
                "fields": [_schema_field(name, kind) for name, kind in WAREHOUSE_COLUMNS],
            }
        ),
        "domains": _aspect({"domains": [DOMAIN_URN]}),
        "ownership": _aspect(
            {
                "owners": [{"owner": OWNER_URN, "type": "DATAOWNER"}],
                "lastModified": {"time": 0, "actor": OWNER_URN},
            }
        ),
        "glossaryTerms": _aspect(
            {
                "terms": [{"urn": GLOSSARY_TERM_URN}],
                "auditStamp": {"time": 0, "actor": OWNER_URN},
            }
        ),
    }


def _bi_dataset() -> dict:
    """The Superset-side dataset, carrying Superset's own identifiers.

    `upstreamLineage` is the explicit edge the cross-source link may use.
    Nothing here claims the two sources agree on meaning -- only that
    DataHub asserts this Superset dataset reads that warehouse table.
    """
    return {
        "urn": BI_DATASET_URN,
        "datasetProperties": _aspect(
            {
                "name": "finance_orders_daily",
                "description": "Superset physical dataset over the finance order facts.",
                "customProperties": {
                    "superset_dataset_uuid": SUPERSET_DATASET_UUID,
                    "superset_dataset_id": str(SUPERSET_DATASET_ID),
                    "superset_instance": SUPERSET_INSTANCE,
                },
                "tags": [],
            }
        ),
        "domains": _aspect({"domains": [DOMAIN_URN]}),
        "upstreamLineage": _aspect(
            {
                "upstreams": [
                    {
                        "dataset": WAREHOUSE_DATASET_URN,
                        "type": "TRANSFORMED",
                        "auditStamp": {"time": 0, "actor": OWNER_URN},
                    }
                ]
            }
        ),
    }


def _domain() -> dict:
    return {
        "urn": DOMAIN_URN,
        "domainProperties": _aspect(
            {
                "name": "Revenue",
                "description": "Recognized revenue reporting and its governed definitions.",
            }
        ),
    }


def _owner() -> dict:
    return {
        "urn": OWNER_URN,
        "corpUserInfo": _aspect(
            {
                "active": True,
                "displayName": "Revenue Data Owner",
                "email": "revenue_owner@example.invalid",
                "title": "Finance Data Owner",
            }
        ),
    }


def _glossary_term(definition: str) -> dict:
    return {
        "urn": GLOSSARY_TERM_URN,
        "glossaryTermInfo": _aspect(
            {
                "name": "Recognized Revenue",
                "definition": definition,
                "termSource": "INTERNAL",
            }
        ),
    }


def seed(session: requests.Session) -> None:
    _post(session, "domain", [_domain()])
    _post(session, "corpuser", [_owner()])
    _post(session, "glossaryterm", [_glossary_term(BASELINE_TERM_DEFINITION)])
    _post(session, "dataset", [_warehouse_dataset(), _bi_dataset()])
    print(f"seeded {WAREHOUSE_DATASET_URN}")
    print(f"seeded {BI_DATASET_URN}")
    print(f"seeded {DOMAIN_URN}, {OWNER_URN}, {GLOSSARY_TERM_URN}")


def _set_term_definition(session: requests.Session, definition: str) -> None:
    _post(session, "glossaryterm", [_glossary_term(definition)])
    print(f"{GLOSSARY_TERM_URN} definition set to: {definition}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seed", "apply", "restore"])
    args = parser.parse_args()

    session = _session()
    if args.action == "seed":
        seed(session)
    elif args.action == "apply":
        _set_term_definition(session, DRIFT_TERM_DEFINITION)
    else:
        _set_term_definition(session, BASELINE_TERM_DEFINITION)
    return 0


if __name__ == "__main__":
    sys.exit(main())
