"""Integrity of the checked-in pinned DataHub v1.6.0 captures (hy-gh-17).

These fixtures are only useful as *evidence*, which means two things have to
stay true without a running instance: they came from the pinned build, and
they answer the queries the connector actually sends today. A projection
edited in `hyperset/connectors/datahub/connector.py` without a re-capture
would otherwise leave the offline suite asserting against a contract nothing
reads anymore.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyperset.connectors.datahub.connector import _HASH_BASIS, projection_fingerprint
from hyperset.repositories.hash_basis import apply_hash_basis

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "datahub" / "v1.6.0" / "revenue"
CAPTURES = ("baseline", "drift")


def _manifest(capture: str) -> dict:
    return json.loads((FIXTURE_DIR / capture / "manifest.json").read_text(encoding="utf-8"))


def _entity(capture: str, slug: str) -> dict:
    path = FIXTURE_DIR / capture / "graphql" / "entities" / f"{slug}.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.integration
@pytest.mark.parametrize("capture", CAPTURES)
def test_captures_came_from_the_pinned_build(capture):
    manifest = _manifest(capture)
    assert manifest["pinned_version"] == "v1.6.0"
    app_config = json.loads(
        (FIXTURE_DIR / capture / "graphql" / "app-config.json").read_text(encoding="utf-8")
    )
    # The instance's own answer, not a value the capture script chose.
    assert app_config["data"]["appConfig"]["appVersion"] == "v1.6.0"


@pytest.mark.integration
@pytest.mark.parametrize("capture", CAPTURES)
def test_captures_still_match_the_connector_projections(capture):
    assert _manifest(capture)["projection_fingerprint"] == projection_fingerprint(), (
        "connector projections changed without re-capturing evidence: run "
        "`make datahub-generate-evidence` against the pinned instance"
    )


@pytest.mark.integration
@pytest.mark.parametrize("capture", CAPTURES)
def test_every_manifest_urn_has_a_recorded_response_carrying_that_urn(capture):
    manifest = _manifest(capture)
    urns = [urn for urns in manifest["urns_by_asset_type"].values() for urn in urns]
    assert sorted(urns) == sorted(manifest["entity_file_slugs"])

    for urn, slug in manifest["entity_file_slugs"].items():
        body = _entity(capture, slug)
        assert "errors" not in body, f"{slug} recorded a failed read"
        served = next(iter(body["data"].values()))
        # The slug is a filename convenience; the URN is the identity, and it
        # is asserted from the response body rather than inferred from a name.
        assert served["urn"] == urn


@pytest.mark.integration
def test_scroll_discovery_covers_exactly_the_projected_entities():
    manifest = _manifest("baseline")
    scroll = json.loads(
        (FIXTURE_DIR / "baseline" / "graphql" / "scroll.json").read_text(encoding="utf-8")
    )
    results = scroll["data"]["scrollAcrossEntities"]["searchResults"]
    assert len(results) == manifest["discovered_entity_count"]
    # Discovery asked for every entity type; the pinned seed holds only the
    # four the connector projects, so nothing was silently skipped.
    assert {row["entity"]["type"] for row in results} == set(manifest["projected_entity_types"])
    assert {row["entity"]["urn"] for row in results} == set(manifest["entity_file_slugs"])


@pytest.mark.integration
def test_the_drift_capture_differs_from_baseline_in_exactly_one_field():
    baseline, drift = _manifest("baseline"), _manifest("drift")
    assert baseline["entity_file_slugs"] == drift["entity_file_slugs"]

    # Compared through the connector's declared hash basis, which is what "one
    # controlled change" means operationally. A raw JSON comparison would also
    # trip on `customProperties` reordering, which the pinned instance does
    # across rewrites and which the connector deliberately ignores.
    differing = [
        urn
        for urn, slug in baseline["entity_file_slugs"].items()
        if apply_hash_basis(_entity("baseline", slug), _HASH_BASIS)
        != apply_hash_basis(_entity("drift", slug), _HASH_BASIS)
    ]
    term_urn = baseline["urns_by_asset_type"]["glossary_term"][0]
    assert differing == [term_urn]

    before = _entity("baseline", baseline["entity_file_slugs"][term_urn])["data"]["glossaryTerm"]
    after = _entity("drift", drift["entity_file_slugs"][term_urn])["data"]["glossaryTerm"]
    changed = {
        key for key in before["properties"] if before["properties"][key] != after["properties"][key]
    }
    assert changed == {"definition"}
