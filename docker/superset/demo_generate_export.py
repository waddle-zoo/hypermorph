#!/usr/bin/env python3
"""Generate the official Superset export ZIP for the bootstrapped demo
assets via Superset's own export REST API (hy-gh-37 "Official fixture
generation" steps 6, 8-9) -- never hand-authored, never a substitute for
this real-source path.

Writes, unmodified as returned by Superset:
  <output_dir>/export.zip
  <output_dir>/metadata.json   -- source-version/capability metadata
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

SUPERSET_URL = os.environ["SUPERSET_URL"].rstrip("/")
ADMIN_USERNAME = os.environ.get("SUPERSET_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ["SUPERSET_ADMIN_PASSWORD"]
OUTPUT_DIR = Path(os.environ.get("EXPORT_OUTPUT_DIR", "/exports"))
PINNED_VERSION = os.environ["SUPERSET_PINNED_VERSION"]


def _session() -> requests.Session:
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
    session.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    csrf = session.get(f"{SUPERSET_URL}/api/v1/security/csrf_token/", timeout=30)
    csrf.raise_for_status()
    session.headers["X-CSRFToken"] = csrf.json()["result"]
    return session


def _all_ids(session: requests.Session, resource: str) -> list[int]:
    resp = session.get(f"{SUPERSET_URL}/api/v1/{resource}/", params={"page_size": 100}, timeout=30)
    resp.raise_for_status()
    return [item["id"] for item in resp.json()["result"]]


def _export(session: requests.Session, resource: str, ids: list[int]) -> bytes | None:
    if not ids:
        return None
    id_list = ",".join(str(i) for i in ids)
    resp = session.get(
        f"{SUPERSET_URL}/api/v1/{resource}/export/", params={"q": f"!({id_list})"}, timeout=60
    )
    resp.raise_for_status()
    return resp.content


def main() -> int:
    session = _session()
    dataset_ids = _all_ids(session, "dataset")
    database_ids = _all_ids(session, "database")
    chart_ids = _all_ids(session, "chart")
    dashboard_ids = _all_ids(session, "dashboard")

    # Superset's own dataset export bundle includes the parent database
    # definition automatically -- exporting datasets is sufficient when
    # datasets exist; fall back to a bare database export otherwise.
    export_bytes = _export(session, "dataset", dataset_ids) or _export(
        session, "database", database_ids
    )
    if export_bytes is None:
        print("nothing to export -- run demo_bootstrap.py first", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUT_DIR / "export.zip"
    zip_path.write_bytes(export_bytes)

    metadata = {
        "source_system": "superset",
        "source_version": PINNED_VERSION,
        # Superset's pinned image runs Python 3.10, which has no datetime.UTC.
        "generated_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "asset_counts": {
            "databases": len(database_ids),
            "datasets": len(dataset_ids),
            "charts": len(chart_ids),
            "dashboards": len(dashboard_ids),
        },
    }
    (OUTPUT_DIR / "metadata.json").write_text(json.dumps(metadata, indent=2))
    print(f"wrote {zip_path} ({len(export_bytes)} bytes) and metadata.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
