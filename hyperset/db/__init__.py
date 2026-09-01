"""Postgres-backed persistence layer (hy-gh-26).

`hyperset.db` owns the SQLAlchemy schema and Alembic migrations —
`hyperset.repositories` is the only allowed consumer of `hyperset.db.models`
outside this package (docs/postgres-persistence-v0.md §3): the domain/
service layer (MCP tools, API, connectors, evaluator) must depend on
`hyperset.repositories`' Protocol contracts, never on SQLAlchemy table
definitions directly.
"""

from hyperset.db.engine import create_session_factory, database_url_from_env, make_engine

__all__ = ["make_engine", "create_session_factory", "database_url_from_env"]
