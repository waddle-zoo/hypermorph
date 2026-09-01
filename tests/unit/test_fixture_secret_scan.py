"""The fixture secret scan cannot report good news it did not earn (hy-jnem).

`docker/superset/secret_scan.py` is what stands between a real Superset capture
and a checked-in fixture carrying a plaintext credential, and both fixture
generators call it. Its old shape could return
`{"credential_labels_checked": [], "findings": [], "status": "passed"}` -- a
pass that checked nothing, in the same shape as a pass that checked everything
-- because the checked list was built from the truthy environment values. The
make targets never reach that, since `docker-compose.yml` declares all three
credentials as `${...:?set in .env}`, but running either generator directly
does.

These run the scan against a temporary evidence directory, so they exercise the
instrument rather than the checked-in record it once produced.
"""

import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "docker" / "superset"))

import secret_scan  # noqa: E402

FULL_ENV = {
    "SUPERSET_ADMIN_PASSWORD": "admin-secret",
    "ANALYTICS_DB_PASSWORD": "analytics-secret",
    "SUPERSET_DB_PASSWORD": "superset-secret",
}


@pytest.fixture
def evidence(tmp_path: Path) -> Path:
    """A clean capture: one masked URI, one archive, nothing to find."""
    (tmp_path / "rest").mkdir()
    (tmp_path / "rest" / "database.json").write_text(
        json.dumps(
            {
                "result": {
                    "sqlalchemy_uri": "postgresql+psycopg2://analytics:XXXXXXXXXX@analytics-db:5432/analytics"
                }
            }
        ),
        encoding="utf-8",
    )
    with zipfile.ZipFile(tmp_path / "official-export.zip", "w") as archive:
        archive.writestr("export/databases/analytics.yaml", "uuid: 7f0e\n")
    return tmp_path


def test_a_scan_that_could_not_read_a_credential_refuses_instead_of_passing(evidence: Path):
    record = secret_scan.scan(evidence, {})

    assert record["status"] == "refused"
    assert record["credential_labels_unavailable"] == list(secret_scan.CREDENTIAL_ENV_VARS)


def test_refusal_names_only_the_credentials_it_could_not_read(evidence: Path):
    partial = dict(FULL_ENV)
    del partial["ANALYTICS_DB_PASSWORD"]

    record = secret_scan.scan(evidence, partial)

    assert record["status"] == "refused"
    assert record["credential_labels_unavailable"] == ["ANALYTICS_DB_PASSWORD"]
    assert "ANALYTICS_DB_PASSWORD" not in record["credential_labels_checked"]
    assert "SUPERSET_ADMIN_PASSWORD" in record["credential_labels_checked"]


def test_the_served_scope_names_every_check_the_scan_can_report_on(evidence: Path):
    record = secret_scan.scan(evidence, FULL_ENV)

    assert record["status"] == "passed"
    assert set(record["credential_labels_checked"]) == {
        *secret_scan.CREDENTIAL_ENV_VARS,
        secret_scan.URI_CHECK,
    }
    assert record["credential_labels_unavailable"] == []


def test_the_record_says_what_passed_ranges_over(evidence: Path):
    record = secret_scan.scan(evidence, FULL_ENV)

    assert record["scope"] == secret_scan.SCOPE
    # The label space of a finding is exactly the label space of the disclosure,
    # so no check the scan performs is missing from what it says it checked.
    assert secret_scan.URI_CHECK in record["credential_labels_checked"]


def test_a_credential_under_an_unpredicted_key_is_still_found(evidence: Path):
    (evidence / "rest" / "surprise.json").write_text(
        json.dumps({"result": {"engine_params": {"connect_args": {"pw": "analytics-secret"}}}}),
        encoding="utf-8",
    )

    record = secret_scan.scan(evidence, FULL_ENV)

    assert record["status"] == "failed"
    assert record["findings"] == [
        {"file": "rest/surprise.json", "credential_label": "ANALYTICS_DB_PASSWORD"}
    ]


def test_a_credential_inside_an_archive_member_is_found_by_its_member_locator(evidence: Path):
    with zipfile.ZipFile(evidence / "official-export.zip", "w") as archive:
        archive.writestr("export/databases/analytics.yaml", "password: superset-secret\n")

    record = secret_scan.scan(evidence, FULL_ENV)

    # The archive's own bytes match too when a member is stored uncompressed;
    # what this pins is that the member itself is named, so a leak has an address
    # inside the archive and not just the archive's name.
    assert {
        "file": "official-export.zip::export/databases/analytics.yaml",
        "credential_label": "SUPERSET_DB_PASSWORD",
    } in record["findings"]


def test_an_unmasked_uri_password_fails_even_when_it_is_no_credential_of_this_stack(
    evidence: Path,
):
    (evidence / "rest" / "other.json").write_text(
        "postgresql+psycopg2://analytics:hunter2@analytics-db:5432/analytics", encoding="utf-8"
    )

    record = secret_scan.scan(evidence, FULL_ENV)

    assert record["status"] == "failed"
    assert record["findings"] == [
        {"file": "rest/other.json", "credential_label": secret_scan.URI_CHECK}
    ]


def test_the_record_and_the_scan_itself_skip_only_the_manifest_and_the_record(evidence: Path):
    (evidence / "manifest.json").write_text('{"secret": "admin-secret"}', encoding="utf-8")
    (evidence / secret_scan.RECORD_NAME).write_text('{"x": "admin-secret"}', encoding="utf-8")

    assert secret_scan.scan(evidence, FULL_ENV)["findings"] == []


def test_write_persists_the_refusal_rather_than_leaving_no_record(evidence: Path):
    with pytest.raises(RuntimeError, match="secret scan refused"):
        secret_scan.write(evidence, {})

    persisted = json.loads((evidence / secret_scan.RECORD_NAME).read_text(encoding="utf-8"))
    assert persisted["status"] == "refused"


def test_write_stops_the_capture_on_a_finding_and_returns_the_record_on_a_pass(evidence: Path):
    (evidence / "leak.json").write_text('{"pw": "admin-secret"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="plaintext credential findings"):
        secret_scan.write(evidence, FULL_ENV)

    (evidence / "leak.json").unlink()
    assert secret_scan.write(evidence, FULL_ENV)["status"] == "passed"
