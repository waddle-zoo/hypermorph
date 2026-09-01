"""The captured chart/dashboard evidence says what it claims (hy-vzk8).

Same discipline as `test_canonical_revenue_evidence.py`, applied to the second
capture set: every record hash covers unmodified bytes, and every reference the
manifest promises is read back out of the captured archive and the captured REST
bodies rather than restated.
"""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "superset" / "6.1.0" / "usage"
MANIFEST = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
CAPTURE = MANIFEST["captures"]["seeded"]
CREDENTIAL_LABELS = {
    "SUPERSET_ADMIN_PASSWORD",
    "ANALYTICS_DB_PASSWORD",
    "SUPERSET_DB_PASSWORD",
}


def _json_result(relative_path: str):
    return json.loads((FIXTURE_DIR / relative_path).read_text(encoding="utf-8"))["result"]


def _export_members() -> dict[str, dict]:
    """Every asset YAML in the captured archive, keyed by its export directory."""
    members: dict[str, list[dict]] = {}
    with zipfile.ZipFile(FIXTURE_DIR / "official-export.zip") as archive:
        for name in sorted(archive.namelist()):
            if not name.endswith(".yaml") or name.endswith("metadata.yaml"):
                continue
            directory = next(
                part
                for part in name.split("/")
                if part in {"databases", "datasets", "charts", "dashboards"}
            )
            members.setdefault(directory, []).append(yaml.safe_load(archive.read(name)))
    return members


@pytest.mark.integration
def test_capture_hashes_cover_unmodified_raw_evidence():
    for record in CAPTURE["records"].values():
        data = (FIXTURE_DIR / record["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        assert len(data) == record["bytes"]

    scan = MANIFEST["secret_scan"]
    assert scan["status"] == "passed"
    assert scan["findings"] == []
    # A pass that checked nothing has the same shape as a pass that checked
    # everything, so the served scope carries the verdict (hy-jnem).
    assert set(scan["credential_labels_checked"]) >= CREDENTIAL_LABELS
    scan_bytes = (FIXTURE_DIR / scan["evidence_path"]).read_bytes()
    assert hashlib.sha256(scan_bytes).hexdigest() == scan["evidence_sha256"]
    persisted = json.loads(scan_bytes.decode("utf-8"))
    assert persisted["credential_labels_checked"] == scan["credential_labels_checked"]


@pytest.mark.integration
def test_manifest_pins_the_same_pinned_source_as_the_revenue_capture():
    source = MANIFEST["source_contract"]
    revenue = json.loads(
        (FIXTURE_DIR.parent / "revenue" / "manifest.json").read_text(encoding="utf-8")
    )["source_contract"]
    assert MANIFEST["extends_fixture"] == "canonical-recognized-revenue-by-region-v1"
    assert (source["source_version"], source["image"], source["image_digest"]) == (
        revenue["source_version"],
        revenue["image"],
        revenue["image_digest"],
    )
    # Seeded onto the revenue datasets, so the two sets describe one instance
    # and not two competing identities for the same asset.
    assert source["database"]["native_uuid"] == revenue["database"]["native_uuid"]
    revenue_datasets = {item["native_uuid"] for item in revenue["datasets"]}
    assert {chart["queries_dataset_uuid"] for chart in source["charts"]} <= revenue_datasets


@pytest.mark.integration
def test_the_export_archive_declares_every_reference_the_manifest_promises():
    members = _export_members()
    expected = MANIFEST["expected_observed_references"]

    assert {chart["uuid"]: chart["dataset_uuid"] for chart in members["charts"]} == expected[
        "chart_queries_dataset"
    ]
    dashboards = {dashboard["uuid"]: dashboard for dashboard in members["dashboards"]}
    assert set(dashboards) == set(expected["dashboard_contains_chart"])
    for uuid_value, chart_uuids in expected["dashboard_contains_chart"].items():
        position = dashboards[uuid_value]["position"]
        assert (
            sorted(
                component["meta"]["uuid"]
                for component in position.values()
                if isinstance(component, dict) and component.get("type") == "CHART"
            )
            == chart_uuids
        )

    # A dataset no chart queries is absent from the archive entirely -- the
    # manifest says so instead of implying every seeded dataset is covered.
    exported_datasets = {dataset["uuid"] for dataset in members["datasets"]}
    uncovered = set(expected["uncovered_by_this_capture"]["dataset_uuids"])
    assert exported_datasets == set(expected["dataset_reference_counts"]) - uncovered
    assert all(expected["dataset_reference_counts"][uuid] == 0 for uuid in uncovered)

    # The countable signal is a tally of the references the archive declares, so
    # a count the archive does not support fails here too and not only where a
    # sync reads it back.
    tally = dict.fromkeys(expected["dataset_reference_counts"], 0)
    for dataset_uuid in expected["chart_queries_dataset"].values():
        tally[dataset_uuid] += 1
    assert tally == expected["dataset_reference_counts"]


@pytest.mark.integration
def test_the_rest_bodies_disclose_the_reference_fields_the_manifest_names():
    """Both references are readable over REST too, under different field names.

    Pinned when the connector read charts and dashboards from export only, as
    the evidence that the limit was the connector's rather than the source's.
    It is now the contract REST normalization reads (hy-rt4v), so a pinned
    build that stopped serving either field fails here before it fails
    anywhere a user would see.
    """
    disclosure = MANIFEST["rest_disclosure"]
    charts = {chart["native_uuid"]: chart for chart in MANIFEST["source_contract"]["charts"]}
    listed = {row["uuid"]: row for row in _json_result("rest/chart-list.json")}
    assert set(listed) == set(charts)

    for record_name, record in CAPTURE["records"].items():
        if not record_name.startswith("chart_detail_"):
            continue
        detail = _json_result(record["path"])
        assert detail[disclosure["chart_identity_field"]] in charts
        assert (
            detail[disclosure["chart_reference_field"]]
            == charts[detail[disclosure["chart_identity_field"]]]["queries_dataset_uuid"]
        )

    dashboard = _json_result("rest/dashboard-detail.json")
    assert (
        dashboard[disclosure["dashboard_identity_field"]]
        in (MANIFEST["expected_observed_references"]["dashboard_contains_chart"])
    )
    position = json.loads(dashboard[disclosure["dashboard_reference_field"]])
    assert (
        sorted(
            component["meta"]["uuid"]
            for component in position.values()
            if isinstance(component, dict) and component.get("type") == "CHART"
        )
        == disclosure["rest_contains_chart_uuids"]
    )
