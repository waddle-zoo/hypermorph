"""Engine/session construction. No module-level global engine: every caller
(CLI, tests, future API layer) builds its own via `make_engine`, so tests
can point at an ephemeral container without import-order tricks."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from hyperset.config.runtime import active_settings
from hyperset.config.secrets import reveal_secret

DEFAULT_DATABASE_URL_ENV = "HYPERSET_DATABASE_URL"


def database_url_from_env(env: dict | None = None) -> str:
    """The Postgres DSN, from the loaded settings object or `$HYPERSET_DATABASE_URL`.

    Migrated to the settings object (hy-2562h): when the serving process loaded a config the
    DSN is `connections.system_db.url` -- a secret-typed reference resolved to a write-only
    `Secret` and revealed here (a legacy `HYPERSET_DATABASE_URL` was folded in as a
    `${env:...}` reference, so the same DSN resolves). When startup has not run -- a unit test,
    a non-serving CLI call -- it is the exact legacy env read.

    Raises `RuntimeError` (not a silent local default) if unset either way -- v0 has exactly
    one required backend (Postgres, per MANIFESTO.md "Storage and Deployment Must Remain
    Replaceable"), and guessing a DSN wrong is worse than failing loudly.
    """
    settings = active_settings()
    if settings is not None:
        configured = settings.get("connections", {}).get("system_db", {}).get("url")
        if configured is not None:
            return reveal_secret(configured)
    env = env if env is not None else os.environ
    url = env.get(DEFAULT_DATABASE_URL_ENV)
    if not url:
        raise RuntimeError(
            f"${DEFAULT_DATABASE_URL_ENV} is not set; expected a "
            "postgresql+psycopg://... connection string"
        )
    return url


def make_engine(database_url: str, **kwargs) -> Engine:
    return create_engine(database_url, **kwargs)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
