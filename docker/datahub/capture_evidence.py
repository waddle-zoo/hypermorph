#!/usr/bin/env python3
"""Record real GraphQL evidence from the pinned DataHub OSS v1.6.0 instance.

Writes the *unmodified* response bodies for exactly the queries
`hyperset.connectors.datahub` sends, so `tests/fake_datahub.py` can replay
real v1.6.0 shapes with no network while
`tests/compose/test_datahub_live_sync.py` proves the same code against the
running instance.

The projections come from `projections()` in the connector itself rather
than a copy kept here -- a capture with its own query text would drift, and
the fixtures would stop being evidence of the real contract.

Usage:
    capture_evidence.py <capture-name>     # e.g. baseline, drift, restored
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import requests

from hyperset.config.connection_settings import datahub_token
from hyperset.connectors.datahub.connector import projection_fingerprint, projections
from hyperset.connectors.datahub.graphql import (
    _APP_VERSION_QUERY,
    _PAGE_SIZE,
    _SCROLL_QUERY,
)

GMS_URL = os.environ.get("DATAHUB_GMS_URL", "http://datahub-gms:8080").rstrip("/")
# Through the settings accessor (hy-py62a); standalone (no startup) it is the exact prior env
# read, and `or None` preserves the original present-empty -> None.
TOKEN = datahub_token() or None
OUTPUT_DIR = Path(os.environ.get("EVIDENCE_OUTPUT_DIR", "/evidence"))
PINNED_VERSION = os.environ.get("DATAHUB_PINNED_VERSION", "v1.6.0")


def _post(session: requests.Session, query: str, variables: dict) -> dict:
    response = session.post(
        f"{GMS_URL}/api/graphql",
        json={"query": query, "variables": variables},
        timeout=60,
    )
    if response.status_code >= 400:
        raise SystemExit(f"GraphQL read failed: HTTP {response.status_code}\n{response.text}")
    body = response.json()
    if body.get("errors"):
        raise SystemExit(f"GraphQL read reported errors: {json.dumps(body['errors'])}")
    return body


def _slug(urn: str) -> str:
    """A filesystem-safe name for a URN that stays readable and unique.

    URNs carry `:`, `(`, `)` and `,`, none of which belong in a filename on
    every platform. The mapping is recorded in the manifest, so the original
    URN is never only inferable from the slug.
    """
    return re.sub(r"[^a-zA-Z0-9]+", "-", urn).strip("-").lower()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", help="capture name, e.g. baseline / drift / restored")
    args = parser.parse_args()

    session = requests.Session()
    session.headers["Content-Type"] = "application/json"
    if TOKEN:
        session.headers["Authorization"] = f"Bearer {TOKEN}"

    root = OUTPUT_DIR / args.capture / "graphql"

    app_config = _post(session, _APP_VERSION_QUERY, {})
    reported_version = (app_config.get("data") or {}).get("appConfig", {}).get("appVersion")
    if reported_version != PINNED_VERSION:
        # The whole point of a pinned source is that evidence is traceable to
        # one build. Capturing against a different one silently would make
        # every fixture a lie about which version produced it.
        raise SystemExit(
            f"refusing to capture: instance reports {reported_version!r}, "
            f"expected the pinned {PINNED_VERSION!r}"
        )
    _write(root / "app-config.json", app_config)

    scroll = _post(session, _SCROLL_QUERY, {"types": None, "count": _PAGE_SIZE, "scrollId": None})
    _write(root / "scroll.json", scroll)

    specs = {
        entity_type: (asset_type, root_field, query)
        for entity_type, asset_type, root_field, query in projections()
    }
    discovered = (scroll["data"]["scrollAcrossEntities"] or {}).get("searchResults") or []

    urns_by_asset_type: dict[str, list[str]] = {}
    slugs: dict[str, str] = {}
    for row in discovered:
        entity = row.get("entity") or {}
        spec = specs.get(entity.get("type"))
        if spec is None:
            continue
        asset_type, root_field, query = spec
        urn = entity["urn"]
        body = _post(session, query, {"urn": urn})
        _write(root / "entities" / f"{_slug(urn)}.json", body)
        urns_by_asset_type.setdefault(asset_type, []).append(urn)
        slugs[urn] = _slug(urn)

    _write(
        OUTPUT_DIR / args.capture / "manifest.json",
        {
            "capture": args.capture,
            "source": "datahub-oss",
            "pinned_version": reported_version,
            "projection_fingerprint": projection_fingerprint(),
            "projected_entity_types": sorted(specs),
            "discovered_entity_count": len(discovered),
            "urns_by_asset_type": {k: sorted(v) for k, v in sorted(urns_by_asset_type.items())},
            "entity_file_slugs": dict(sorted(slugs.items())),
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
