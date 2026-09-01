"""Shared test fixtures for the whole suite.

The serving process records the loaded config as a PROCESS-GLOBAL active settings object
(hy-tc4o), set by `apply_startup_config`. Reset it (and the deprecation-warning dedup) before
and after every test so one test's startup never leaks its config into another's reads.
"""

from __future__ import annotations

import pytest

from hyperset.config import legacy
from hyperset.config.playground_settings import PLAYGROUND_ENABLED_ENV
from hyperset.config.runtime import clear_active_settings
from tests.postgres import conftest as postgres_fixtures


@pytest.fixture(scope="session")
def postgres_dsn():
    yield from postgres_fixtures.postgres_dsn._fixture_function()


@pytest.fixture(scope="session")
def db_engine(postgres_dsn):
    yield from postgres_fixtures.db_engine._fixture_function(postgres_dsn)


@pytest.fixture
def session_factory(db_engine):
    yield from postgres_fixtures.session_factory._fixture_function(db_engine)


@pytest.fixture
def committed_session_factory(db_engine):
    yield from postgres_fixtures.committed_session_factory._fixture_function(db_engine)


@pytest.fixture()
def revenue_slice(session_factory, tmp_path):
    yield from postgres_fixtures.revenue_slice._fixture_function(session_factory, tmp_path)


@pytest.fixture()
def wide_context(session_factory, tmp_path):
    yield from postgres_fixtures.wide_context._fixture_function(session_factory, tmp_path)


@pytest.fixture(autouse=True)
def _reset_active_settings(monkeypatch):
    monkeypatch.delenv(PLAYGROUND_ENABLED_ENV, raising=False)
    clear_active_settings()
    legacy._warned.clear()
    yield
    clear_active_settings()
    legacy._warned.clear()
