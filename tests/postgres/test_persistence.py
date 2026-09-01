"""Persistence-across-restart proxy test.

A real `docker stop && docker start` cycle needs a named volume, which is
owned by hy-gh-37's compose stack (not this repository layer) -- the
ephemeral test container this suite spins up has none, so an actual
container restart would just lose the data. What *is* this layer's
responsibility, and what this test exercises, is that committed data lives
in Postgres itself, not in any Python-process-local cache: a brand new
engine/session, opened only after the one that wrote the row has been
fully closed and disposed, still sees it.
"""

import pytest
from sqlalchemy.orm import sessionmaker

from hyperset.db.engine import make_engine
from hyperset.repositories.postgres import PostgresConnectionRepository


@pytest.mark.postgres
def test_data_persists_across_independent_engine_instances(postgres_dsn, db_engine):
    write_engine = make_engine(postgres_dsn)
    write_factory = sessionmaker(bind=write_engine, expire_on_commit=False)
    created = PostgresConnectionRepository(write_factory).create_or_update(
        connector_type="superset", display_name="Persistence Probe"
    )
    write_engine.dispose()

    read_engine = make_engine(postgres_dsn)
    read_factory = sessionmaker(bind=read_engine, expire_on_commit=False)
    fetched = PostgresConnectionRepository(read_factory).get(created.id)
    read_engine.dispose()

    assert fetched.display_name == "Persistence Probe"
