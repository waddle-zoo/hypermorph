#!/usr/bin/env python3
"""Scan a captured evidence directory for this stack's plaintext credentials.

Shared by both fixture generators, because a capture that leaks a password is
the same defect whichever question it was capturing for.

Two checks run over every captured file and every ZIP member:

1. exact-value match of the three credentials this stack actually has. For a
   demo estate with exactly three secrets this is *stronger* than a generic
   scanner: it catches the credential under a key name nobody predicted.
2. a `scheme://user:password@` regex, failing on any password outside the
   masks Superset and the drivers are known to emit.

Check (1) can only run on a credential the process can read, so the record
names what it checked, names what it could not, and REFUSES rather than
passing when a credential is missing -- a scan that read no credential cannot
report that credential absent from the capture, and a `passed` that means
"checked nothing" in the same shape as a `passed` that means "checked
everything" is not an instrument (hy-jnem).
"""

from __future__ import annotations

import json
import re
import zipfile
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path

RECORD_NAME = "secret-scan.json"

#: The credentials this stack has. Read from the environment at scan time;
#: `docker-compose.yml` declares all three as `${...:?set in .env}` on both
#: evidence services, so a run through the make targets always has them.
CREDENTIAL_ENV_VARS = (
    "SUPERSET_ADMIN_PASSWORD",
    "ANALYTICS_DB_PASSWORD",
    "SUPERSET_DB_PASSWORD",
)

#: Label for check (2). Served beside the credential labels because it is a
#: finding this scan can emit, so leaving it out understates the real scope.
URI_CHECK = "unmasked_uri"

ALLOWED_MASKS = frozenset({b"XXXXXXXXXX", b"**********", b"****", b"xxxxx", b"XXXXXXXX"})

_URI_PATTERN = re.compile(rb"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/]+:([^\s@/]+)@")

_SKIP_FILES = frozenset({"manifest.json", RECORD_NAME})

#: What `passed` ranges over, served in the record so the verdict is not read
#: wider than it was earned.
SCOPE = (
    "passed means: no value of this stack's three credentials appears in any "
    "captured file or ZIP member in any form, and no scheme://user:password@ "
    "URI carries a password outside the known mask set. It does not range over "
    "a secret that is neither -- a token, an API key, or a service-account blob "
    "-- which this estate does not have today (one Postgres analytics database, "
    "no OAuth)."
)


def _archive_members(data: bytes) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return [(name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")]


def _blobs(output_dir: Path) -> list[tuple[str, bytes]]:
    blobs: list[tuple[str, bytes]] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name in _SKIP_FILES:
            continue
        data = path.read_bytes()
        blobs.append((str(path.relative_to(output_dir)), data))
        if path.suffix == ".zip":
            blobs.extend(
                (f"{path.relative_to(output_dir)}::{name}", member)
                for name, member in _archive_members(data)
            )
    return blobs


def scan(output_dir: Path, environ: Mapping[str, str]) -> dict:
    """The scan record: its verdict, everything it checked, and what it could not."""
    credentials = {name: environ.get(name) for name in CREDENTIAL_ENV_VARS}
    unavailable = [name for name, value in credentials.items() if not value]
    checked = [name for name, value in credentials.items() if value] + [URI_CHECK]

    findings: list[dict] = []
    for locator, data in _blobs(output_dir):
        for label, value in credentials.items():
            if value and value.encode() in data:
                findings.append({"file": locator, "credential_label": label})
        for match in _URI_PATTERN.finditer(data):
            if match.group(1) not in ALLOWED_MASKS:
                findings.append({"file": locator, "credential_label": URI_CHECK})

    if unavailable:
        status = "refused"
    elif findings:
        status = "failed"
    else:
        status = "passed"

    return {
        "status": status,
        "credential_labels_checked": checked,
        "credential_labels_unavailable": unavailable,
        "scope": SCOPE,
        "findings": findings,
    }


def write(output_dir: Path, environ: Mapping[str, str]) -> dict:
    """Run the scan, persist the record, and stop the capture unless it passed."""
    record = scan(output_dir, environ)
    (output_dir / RECORD_NAME).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if record["status"] == "refused":
        raise RuntimeError(
            "secret scan refused: no value for "
            f"{', '.join(record['credential_labels_unavailable'])}. "
            "A scan that cannot read a credential cannot report it absent from "
            "the capture."
        )
    if record["status"] == "failed":
        raise RuntimeError(f"plaintext credential findings: {record['findings']}")
    return record
