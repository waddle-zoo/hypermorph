"""The live-lookup read is structurally incapable of warehouse SQL (hy-jg2v).

Criterion D of ADR 0024 decision 2, at the unit tier: the default transport a
live lookup builds is a connector's READ-ONLY metadata client, and the module
constructs no warehouse engine at all. The DB-touching behaviour -- held body vs
a live read through an injected transport -- lives in
`tests/postgres/test_live_lookup.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hyperset.flywheel import live_lookup
from hyperset.flywheel.live_lookup import (
    _DataHubReadOnly,
    _default_transport,
    _SupersetReadOnly,
)
from hyperset.repositories.dto import ConnectionRecord


def _connection(connector_type: str) -> ConnectionRecord:
    now = datetime.now(UTC)
    return ConnectionRecord(
        id="cn-1",
        connector_type=connector_type,
        display_name="c",
        enabled=True,
        health_status="unknown",
        health_checked_at=None,
        health_detail=None,
        config_ref="http://source.local",
        created_at=now,
        updated_at=now,
    )


def test_the_default_transport_is_the_connector_read_only_client():
    assert isinstance(_default_transport(_connection("superset")), _SupersetReadOnly)
    assert isinstance(_default_transport(_connection("datahub")), _DataHubReadOnly)


def test_an_unknown_connector_type_has_no_live_lookup_transport():
    with pytest.raises(ValueError, match="no live-lookup transport"):
        _default_transport(_connection("warehouse"))


def test_the_lookup_module_constructs_no_warehouse_engine():
    """Structural: nothing in the live-lookup module reaches SQLAlchemy engine
    construction or a DBAPI. There is no warehouse client to run a query on."""
    source = live_lookup.__file__
    text = open(source).read()
    for forbidden in ("create_engine", "sqlalchemy", "psycopg", "cursor(", ".execute("):
        assert forbidden not in text, f"live_lookup reaches {forbidden!r}"


def test_a_body_read_returns_a_body_never_an_execution_verdict():
    """It reads the asset object; it does not build a `ContextBundle`, so it has
    no `execution.performed_by_hyperset` to flip. The two read-only transport
    wrappers hold only a connection and defer client construction."""
    for wrapper in (
        _SupersetReadOnly(_connection("superset")),
        _DataHubReadOnly(_connection("datahub")),
    ):
        assert hasattr(wrapper, "fetch_body")
        assert not hasattr(wrapper, "execution")
        assert not any(
            attr in dir(wrapper) for attr in ("execute", "run_sql", "warehouse", "query_warehouse")
        )
