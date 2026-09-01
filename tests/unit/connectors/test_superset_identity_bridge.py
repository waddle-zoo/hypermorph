"""What makes `list_resource`'s denominator valid (hy-3187, hy-fc01).

`hyperset/connectors/superset/rest.py` counts completeness in unique listing
`id`s. `hyperset/connectors/sync.py` measures coverage in `external_id`, which
for this connector is the DETAIL payload's `uuid` (`_required_uuid`). Unique-id
coverage therefore proves uuid coverage only because every listed id that
reaches a detail either produces a uuid or RAISES: a listed asset can never be
quietly missing from the seen-set and soft-deleted.

That raise is the bridge, and it is what this file pins. `normalize()` is where
it would most plausibly be softened -- its loop is what someone means by "do
not fail the whole sync for one bad payload" -- and an `except ConnectorError:
continue` added there leaves
`tests/unit/connectors/test_superset_connector.py::test_normalize_rejects_name_only_identity`
green, because that test calls `_normalize_dataset` directly. So the assertion
here is made at the `normalize()` level, and for all four asset types: only the
dataset normalizer is covered anywhere today, and the other three call sites
trip nothing on their own.

Placed in its own file rather than beside the other connector tests because
`tests/unit/connectors/test_superset_connector.py` is contended by #175
(hy-rt4v) at the time of writing.
"""

import pytest

from hyperset.connectors import ConnectorError, ConnectorSnapshot
from hyperset.connectors.superset import SupersetConnector

# One payload per type that is complete enough to normalize EXCEPT for the
# uuid, so what the raise reacts to is the missing identity and nothing else.
NAMED_BUT_UNIDENTIFIED = {
    "databases": {"database_name": "warehouse"},
    "datasets": {"table_name": "orders", "database": {"uuid": "db-1"}},
    "charts": {"slice_name": "orders_by_region", "dataset_uuid": "dataset-1"},
    "dashboards": {"dashboard_title": "Q3 Review", "position": {}},
}


def _connector() -> SupersetConnector:
    # Neither transport is exercised and the path is never opened:
    # `normalize()` reads the snapshot it is handed and never touches the
    # source. A bundle path is the transport that needs no credentials.
    return SupersetConnector(bundle_path="/nonexistent/export.zip")


@pytest.mark.parametrize("asset_type", sorted(NAMED_BUT_UNIDENTIFIED))
def test_a_payload_with_no_uuid_raises_out_of_normalize_rather_than_being_skipped(asset_type):
    """The whole read fails, and that is the point: an asset the connector
    cannot name is an asset the sync cannot mark seen, and a sync that dies is
    rerun while a soft-delete is a restore."""
    snapshot = ConnectorSnapshot(
        source_version=None, bundle={asset_type: [NAMED_BUT_UNIDENTIFIED[asset_type]]}
    )

    with pytest.raises(ConnectorError, match="stable uuid"):
        list(_connector().normalize(snapshot))


@pytest.mark.parametrize("asset_type", sorted(NAMED_BUT_UNIDENTIFIED))
def test_the_identified_assets_beside_it_are_not_yielded_as_a_partial_answer(asset_type):
    """`normalize()` is a generator, so "it raises" is only half the guarantee:
    a caller that consumed the first item and stopped would still be told about
    the asset that came before the bad one. What must not exist is a
    normalization that yields the good rows and drops the unnamed one -- that
    is exactly the shape whose rows go missing from the seen-set."""
    identified = dict(NAMED_BUT_UNIDENTIFIED[asset_type])
    identified["uuid"] = "identified-1"
    snapshot = ConnectorSnapshot(
        source_version=None,
        bundle={asset_type: [identified, NAMED_BUT_UNIDENTIFIED[asset_type]]},
    )

    produced = []
    with pytest.raises(ConnectorError, match="stable uuid"):
        for item in _connector().normalize(snapshot):
            produced.append(item)

    assert [item.external_id for item in produced] == ["identified-1"]
