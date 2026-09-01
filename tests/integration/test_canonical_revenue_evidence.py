import hashlib
import json
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "superset" / "6.1.0" / "revenue"
MANIFEST = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
CREDENTIAL_LABELS = {
    "SUPERSET_ADMIN_PASSWORD",
    "ANALYTICS_DB_PASSWORD",
    "SUPERSET_DB_PASSWORD",
}


def _json_result(relative_path: str):
    return json.loads((FIXTURE_DIR / relative_path).read_text(encoding="utf-8"))["result"]


def _metric(snapshot: str) -> dict:
    detail = _json_result(f"{snapshot}/rest/dataset-finance_orders_daily-detail.json")
    metric_uuid = MANIFEST["controlled_drift"]["metric_uuid"]
    return next(metric for metric in detail["metrics"] if metric["uuid"] == metric_uuid)


@pytest.mark.integration
def test_manifest_pins_one_canonical_question_and_real_source_identity():
    assert MANIFEST["canonical_question"] == (
        "Which source and rules should an analyst use for recognized revenue by region?"
    )
    source = MANIFEST["source_contract"]
    assert source["source_version"] == "6.1.0"
    assert source["image"] == "apache/superset:6.1.0"
    assert source["image_digest"].startswith("sha256:")
    assert source["database"]["native_uuid"]
    assert {item["role"] for item in source["datasets"]} == {
        "approved_candidate",
        "required_dimension",
        "prohibited_alternative",
    }
    assert all(item["native_id"] and item["native_uuid"] for item in source["datasets"])


@pytest.mark.integration
def test_rest_list_and_detail_captures_resolve_the_manifest_assets():
    list_rows = _json_result("baseline/rest/dataset-list.json")
    listed = {row["uuid"]: row["id"] for row in list_rows}
    for asset in MANIFEST["source_contract"]["datasets"]:
        assert listed[asset["native_uuid"]] == asset["native_id"]
        detail = _json_result(f"baseline/rest/dataset-{asset['display_name']}-detail.json")
        assert detail["id"] == asset["native_id"]
        assert detail["uuid"] == asset["native_uuid"]
        assert detail["table_name"] == asset["display_name"]


@pytest.mark.integration
def test_official_export_matches_the_live_rest_objects():
    archive_path = FIXTURE_DIR / "baseline" / "official-export.zip"
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.namelist()
        database_name = next(name for name in members if name.endswith("/databases/analytics.yaml"))
        database = yaml.safe_load(archive.read(database_name))
        assert database["uuid"] == MANIFEST["source_contract"]["database"]["native_uuid"]

        for asset in MANIFEST["source_contract"]["datasets"]:
            suffix = f"/datasets/analytics/{asset['display_name']}_{asset['native_id']}.yaml"
            member = next(name for name in members if name.endswith(suffix))
            exported = yaml.safe_load(archive.read(member))
            assert exported["uuid"] == asset["native_uuid"]
            assert exported["database_uuid"] == database["uuid"]
            assert exported["table_name"] == asset["display_name"]


@pytest.mark.integration
def test_capture_hashes_cover_unmodified_raw_evidence():
    for capture in MANIFEST["captures"].values():
        for record in capture["records"].values():
            data = (FIXTURE_DIR / record["path"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == record["sha256"]
            assert len(data) == record["bytes"]


@pytest.mark.integration
def test_controlled_drift_changes_only_the_metric_expression_and_restores_hash():
    baseline = _metric("baseline")
    drift = _metric("drift")
    restored = _metric("restored")
    expected = MANIFEST["controlled_drift"]

    assert baseline["expression"] == expected["baseline_value"]
    assert drift["expression"] == expected["drift_value"]
    assert restored == baseline
    assert {**drift, "expression": baseline["expression"]} == baseline
    assert expected["baseline_property_hash"] == expected["restored_property_hash"]
    assert expected["drift_property_hash"] != expected["baseline_property_hash"]
    assert expected["expected_connector_change_category"] == "updated"
    assert expected["expected_processor_finding_type"] == "metric_expression_drift"


@pytest.mark.integration
def test_proposal_and_secret_scan_preserve_governance_and_security_boundaries():
    proposal = MANIFEST["governed_context_proposal"]
    assert proposal["lifecycle"] == "candidate"
    assert proposal["review_state"] == "pending_human_review"
    assert proposal["approval_decision"] is None
    source_uuids = {item["native_uuid"] for item in MANIFEST["source_contract"]["datasets"]}
    assert {item["native_uuid"] for item in proposal["observed_source_refs"]} == source_uuids
    proposal_yaml = yaml.safe_load((REPO_ROOT / proposal["path"]).read_text(encoding="utf-8"))
    assert proposal_yaml["status"] == "draft"
    assert proposal_yaml["proposal_state"] == "pending_human_review"
    assert {item["native_uuid"] for item in proposal_yaml["observed_source_refs"]} == source_uuids

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
