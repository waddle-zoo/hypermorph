import argparse
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "hyperset" / "db" / "migrations"

_EXPECTED_TABLES = {
    "answer_feedback",
    "connections",
    "sync_runs",
    "connector_checkpoints",
    "observed_assets",
    "observed_asset_versions",
    "asset_relationships",
    "connector_changes",
    "governed_context",
    "governed_context_versions",
    "context_asset_links",
    "context_sources",
    "context_snapshots",
    "review_tasks",
    "review_decisions",
    "evaluation_cases",
    "evaluation_runs",
    "evidence_records",
    "resolve_miss",
    "context_writeback_config",
}


_EXPECTED_CONNECTOR_CHANGE_INDEXES = {
    "ix_connector_changes_connection_detected",
    "ix_connector_changes_run_detected",
    "ix_connector_changes_asset_detected",
}

# The connector-change index revision: the last one before `transport` was
# added to `observed_asset_versions` and `sync_runs`.
_BELOW_TRANSPORT = "f2b6c04a91d7"

# The revision below the declared-reference constraints, named for the same
# reason `_BELOW_TRANSPORT` is: "-1" retargets itself the moment another
# migration lands on top.
_BELOW_RELATIONSHIP_PROJECTION = "a91f3c7d5e04"

# The write-back-config revision, below the token_ref column added on top of it.
_BELOW_WRITEBACK_TOKEN_REF = "d4e8b1c60a29"

# The token_ref revision, below the encrypted-token columns added on top of it.
_BELOW_WRITEBACK_ENCRYPTED_TOKEN = "e7b3f9a2c815"

# The encrypted-token revision, below the github-app columns added on top of it.
_BELOW_WRITEBACK_GITHUB_APP = "f1a2b3c4d5e6"

# Current main head below the trace-linked feedback revision. Naming it proves a database
# already deployed at main upgrades through one chain rather than relying on relative `-1`.
_BELOW_ANSWER_FEEDBACK = "d9e2f3a4b5c6"


def _config(dsn: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.cmd_opts = argparse.Namespace(x=[f"db_url={dsn}"])
    return cfg


@pytest.mark.postgres
def test_upgrade_creates_all_required_tables(postgres_dsn):
    engine = create_engine(postgres_dsn)
    try:
        command.upgrade(_config(postgres_dsn), "head")
        tables = set(inspect(engine).get_table_names())
        assert _EXPECTED_TABLES <= tables
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_feedback_revision_upgrades_from_the_current_main_head(postgres_dsn):
    engine = create_engine(postgres_dsn)
    try:
        cfg = _config(postgres_dsn)
        command.downgrade(cfg, _BELOW_ANSWER_FEEDBACK)
        inspector = inspect(engine)
        assert "answer_feedback" not in inspector.get_table_names()
        before = {column["name"] for column in inspector.get_columns("mcp_interaction_trace")}
        assert "duration_ms" not in before

        command.upgrade(cfg, "head")
        inspector = inspect(engine)
        assert "answer_feedback" in inspector.get_table_names()
        after = {column["name"] for column in inspector.get_columns("mcp_interaction_trace")}
        assert {"duration_ms", "source_staleness", "miss", "feedback_ids"} <= after
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_upgrade_indexes_the_connector_change_query_paths(postgres_dsn):
    """The change stream is only ever read by run or by asset, so those two
    columns must be indexed, not just `connection_id` (hy-y8g finding 5)."""
    engine = create_engine(postgres_dsn)
    try:
        command.upgrade(_config(postgres_dsn), "head")
        names = {ix["name"] for ix in inspect(engine).get_indexes("connector_changes")}
        assert _EXPECTED_CONNECTOR_CHANGE_INDEXES <= names
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_downgrade_drops_everything_and_upgrade_is_replayable(postgres_dsn):
    engine = create_engine(postgres_dsn)
    try:
        cfg = _config(postgres_dsn)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "base")
        tables_after_downgrade = set(inspect(engine).get_table_names()) - {"alembic_version"}
        assert tables_after_downgrade == set()

        command.upgrade(cfg, "head")
        tables_after_reupgrade = set(inspect(engine).get_table_names())
        assert _EXPECTED_TABLES <= tables_after_reupgrade
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_writeback_token_ref_column_migrates_down_and_up(postgres_dsn):
    """The token-reference column added on top of the write-back-config table
    (hy-eji4) is added on upgrade and dropped on downgrade -- down exercised, not
    just written, and replayable."""
    engine = create_engine(postgres_dsn)
    try:
        cfg = _config(postgres_dsn)
        command.upgrade(cfg, "head")

        def columns(table):
            return {column["name"] for column in inspect(engine).get_columns(table)}

        assert "token_ref" in columns("context_writeback_config")

        command.downgrade(cfg, _BELOW_WRITEBACK_TOKEN_REF)
        assert "token_ref" not in columns("context_writeback_config")

        command.upgrade(cfg, "head")
        assert "token_ref" in columns("context_writeback_config")
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_writeback_encrypted_token_columns_migrate_down_and_up(postgres_dsn):
    """The encrypted-token columns added on top of the write-back-config table
    (hy-up4k) are added on upgrade and dropped on downgrade -- down exercised,
    not just written, and replayable."""
    engine = create_engine(postgres_dsn)
    try:
        cfg = _config(postgres_dsn)
        command.upgrade(cfg, "head")

        def columns(table):
            return {column["name"] for column in inspect(engine).get_columns(table)}

        added = {"token_source", "token_ciphertext", "token_nonce"}
        assert added <= columns("context_writeback_config")

        command.downgrade(cfg, _BELOW_WRITEBACK_ENCRYPTED_TOKEN)
        assert not (added & columns("context_writeback_config"))
        # token_ref, the revision below, survives.
        assert "token_ref" in columns("context_writeback_config")

        command.upgrade(cfg, "head")
        assert added <= columns("context_writeback_config")
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_writeback_github_app_columns_migrate_down_and_up(postgres_dsn):
    """The GitHub App columns added on top of the write-back-config table
    (hy-bdhg) are added on upgrade and dropped on downgrade -- down exercised, not
    just written, and replayable."""
    engine = create_engine(postgres_dsn)
    try:
        cfg = _config(postgres_dsn)
        command.upgrade(cfg, "head")

        def columns(table):
            return {column["name"] for column in inspect(engine).get_columns(table)}

        added = {"app_id", "app_key_ciphertext", "app_key_nonce"}
        assert added <= columns("context_writeback_config")

        command.downgrade(cfg, _BELOW_WRITEBACK_GITHUB_APP)
        assert not (added & columns("context_writeback_config"))
        # The encrypted-token columns, the revision below, survive.
        assert {"token_source", "token_ciphertext", "token_nonce"} <= columns(
            "context_writeback_config"
        )

        command.upgrade(cfg, "head")
        assert added <= columns("context_writeback_config")
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_the_added_columns_survive_a_downgrade_and_a_replay(postgres_dsn):
    """The down path exercised rather than written (hy-6t4).

    A migration whose `downgrade()` has never run is a migration that has been
    reviewed, not tested, and these drop columns other code now reads. Down to
    the revision below them, back up, checked both ways.

    The target is named rather than counted: `-1` meant "undo the transport
    migration" only while that migration was head, so the next revision added
    on top silently retargeted this test at itself.
    """
    engine = create_engine(postgres_dsn)
    try:
        cfg = _config(postgres_dsn)
        command.upgrade(cfg, "head")

        def columns(table):
            return {column["name"] for column in inspect(engine).get_columns(table)}

        assert "transport" in columns("observed_asset_versions")
        assert "transport" in columns("sync_runs")
        assert "evidence_findings" in columns("context_snapshots")

        command.downgrade(cfg, _BELOW_TRANSPORT)
        assert "transport" not in columns("observed_asset_versions")
        assert "transport" not in columns("sync_runs")
        assert "evidence_findings" not in columns("context_snapshots")

        command.upgrade(cfg, "head")
        assert "transport" in columns("observed_asset_versions")
        assert "transport" in columns("sync_runs")
        assert "evidence_findings" in columns("context_snapshots")
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_upgrade_constrains_and_indexes_the_declared_reference_projection(postgres_dsn):
    """`asset_relationships` carried neither constraint for the whole time
    nothing wrote to it (hy-d7xh). The unique key is what makes a count over
    the table mean "references declared" instead of "times synced", and the
    index is the one read a count performs.

    Exercised down and back up, because the down path drops objects the sync
    now depends on."""
    engine = create_engine(postgres_dsn)
    try:
        cfg = _config(postgres_dsn)
        command.upgrade(cfg, "head")

        def objects():
            inspector = inspect(engine)
            return (
                {ix["name"] for ix in inspector.get_indexes("asset_relationships")},
                {uq["name"] for uq in inspector.get_unique_constraints("asset_relationships")},
            )

        indexes, unique = objects()
        assert "ix_asset_relationships_to_relation" in indexes
        assert "uq_asset_relationship" in unique

        command.downgrade(cfg, _BELOW_RELATIONSHIP_PROJECTION)
        indexes, unique = objects()
        assert "ix_asset_relationships_to_relation" not in indexes
        assert "uq_asset_relationship" not in unique

        command.upgrade(cfg, "head")
        indexes, unique = objects()
        assert "ix_asset_relationships_to_relation" in indexes
        assert "uq_asset_relationship" in unique
    finally:
        engine.dispose()
