"""Migration scripts must be importable without a live Alembic proxy.

`alembic upgrade` builds the revision map with the `Operations` proxy
already established, so a module-level `op.f(...)` still upgrades cleanly
while breaking `alembic revision`, `heads`, and `history` with
"proxy object has not yet been established" -- i.e. the next person to add
a migration is blocked and no Postgres test notices. Walking the revisions
here imports every script the same way the CLI does, without a database.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "hyperset" / "db" / "migrations"


def _script_directory() -> ScriptDirectory:
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return ScriptDirectory.from_config(cfg)


def test_every_revision_imports_outside_a_migration_run():
    revisions = list(_script_directory().walk_revisions())
    assert revisions


def test_migrations_have_exactly_one_head():
    assert len(_script_directory().get_heads()) == 1
