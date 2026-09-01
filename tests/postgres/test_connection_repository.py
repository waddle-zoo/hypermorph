import pytest

from hyperset.repositories.errors import NotFoundError
from hyperset.repositories.postgres import PostgresConnectionRepository
from hyperset.repositories.scope import ALL_WORKSPACES


@pytest.mark.postgres
def test_create_and_get(session_factory):
    repo = PostgresConnectionRepository(session_factory)
    created = repo.create_or_update(connector_type="superset", display_name="Local Superset")
    fetched = repo.get(created.id)
    assert fetched.id == created.id
    assert fetched.connector_type == "superset"
    assert fetched.enabled is True


@pytest.mark.postgres
def test_update_by_id_does_not_create_a_second_row(session_factory):
    repo = PostgresConnectionRepository(session_factory)
    created = repo.create_or_update(connector_type="superset", display_name="Local Superset")
    updated = repo.create_or_update(
        connection_id=created.id, connector_type="superset", display_name="Renamed", enabled=False
    )
    assert updated.id == created.id
    assert updated.display_name == "Renamed"
    assert updated.enabled is False
    assert len(repo.list(workspace=ALL_WORKSPACES)) == 1


@pytest.mark.postgres
def test_get_missing_connection_raises_not_found(session_factory):
    repo = PostgresConnectionRepository(session_factory)
    with pytest.raises(NotFoundError):
        repo.get("conn-does-not-exist")


@pytest.mark.postgres
def test_never_exposes_plaintext_secret_column(session_factory):
    # Required entities #1: "never expose plaintext secrets through normal
    # serialization" -- enforced by shape: ConnectionRecord has no field
    # that could hold one.
    from hyperset.repositories.dto import ConnectionRecord

    field_names = {f.name for f in ConnectionRecord.__dataclass_fields__.values()}
    assert "config_encrypted" not in field_names
    assert not any("secret" in name or "password" in name for name in field_names)


@pytest.mark.postgres
def test_record_health_updates_status_and_timestamp(session_factory):
    repo = PostgresConnectionRepository(session_factory)
    created = repo.create_or_update(connector_type="superset", display_name="Local Superset")
    assert created.health_status == "unknown"
    updated = repo.record_health(created.id, status="healthy", detail="ping ok")
    assert updated.health_status == "healthy"
    assert updated.health_detail == "ping ok"
    assert updated.health_checked_at is not None


@pytest.mark.postgres
def test_list_enabled_only_filters(session_factory):
    repo = PostgresConnectionRepository(session_factory)
    repo.create_or_update(connector_type="superset", display_name="A", enabled=True)
    repo.create_or_update(connector_type="superset", display_name="B", enabled=False)
    assert len(repo.list(workspace=ALL_WORKSPACES, enabled_only=True)) == 1
    assert len(repo.list(workspace=ALL_WORKSPACES, enabled_only=False)) == 2
