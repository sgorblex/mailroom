from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine

from tests.support.worker_db import (
    admin_database_url,
    coordinator_process,
    ensure_coordinator_database_migrated,
    provision_worker_database,
    test_database_url,
    test_database_url_allowed,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from starlette.testclient import TestClient

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config: pytest.Config) -> None:
    """Migrate the coordinator's ``TEST_DATABASE_URL`` database on a long-lived PostgreSQL server.

    Tests expect PostgreSQL to already be running: the repository's ``compose.yml`` ``postgres``
    service locally (``mise run dev-db``), or the CI ``database`` step.
    """
    from mailroom.database import load_local_environment

    _ = config
    if not coordinator_process():
        return
    load_local_environment()
    database_url = test_database_url()
    if not database_url or not test_database_url_allowed(database_url):
        return

    import psycopg

    try:
        with psycopg.connect(admin_database_url(database_url), connect_timeout=5):
            pass
    except psycopg.OperationalError as exc:
        pytest.exit(
            f"Could not reach the PostgreSQL server for TEST_DATABASE_URL ({database_url!r}): "
            f"{exc}. Start it first with `mise run dev-db`.",
            returncode=1,
        )
    ensure_coordinator_database_migrated(BACKEND_ROOT, database_url)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    from mailroom.database import load_local_environment

    _ = config
    integration = pytest.mark.integration
    for item in items:
        if "migrated_engine" in item.fixturenames and not item.get_closest_marker("integration"):
            item.add_marker(integration)

    load_local_environment()
    url = test_database_url()
    if url:
        if not test_database_url_allowed(url):
            unsafe = pytest.mark.skip(
                reason=(
                    "TEST_DATABASE_URL is not approved for integration tests (use PostgreSQL on "
                    "loopback with a database name ending with '_test', for example "
                    "postgresql://user@127.0.0.1:5434/mailroom_test, or set "
                    "MAILROOM_INTEGRATION_TESTS_ALLOW_ANY_DATABASE=1)"
                ),
            )
            for item in items:
                if item.get_closest_marker("integration"):
                    item.add_marker(unsafe)
        return

    skip = pytest.mark.skip(reason="TEST_DATABASE_URL is not set (see .env.example).")
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def _worker_engine() -> Iterator[Engine]:
    """Provision this xdist worker's PostgreSQL database once for the whole test session."""
    from mailroom.database import get_sync_database_url, load_local_environment

    load_local_environment()
    database_url = test_database_url()
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    if not test_database_url_allowed(database_url):
        raise RuntimeError(
            "Integration tests require TEST_DATABASE_URL using a PostgreSQL URL on loopback whose "
            "database name ends with '_test' (for example mailroom_test), or set "
            "MAILROOM_INTEGRATION_TESTS_ALLOW_ANY_DATABASE=1 to opt in explicitly."
        )
    worker_url = provision_worker_database(BACKEND_ROOT, database_url)
    os.environ["TEST_DATABASE_URL"] = worker_url
    os.environ["DATABASE_URL"] = worker_url
    engine = create_engine(get_sync_database_url(), pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def migrated_engine(_worker_engine: Engine) -> Engine:
    """The worker's migrated database. Per-test transaction isolation (the join-the-outer-
    transaction recipe) comes with the first table a test writes to."""
    return _worker_engine


@pytest.fixture(scope="session")
def _shared_application(_worker_engine: Engine) -> Iterator[FastAPI]:
    """Build the FastAPI app once per xdist worker, with ``DATABASE_URL`` already pointed at this
    worker's database before the lifespan runs."""
    from starlette.testclient import TestClient

    from mailroom.api.main import create_application

    application = create_application()
    with TestClient(application):
        yield application


@pytest.fixture
def api_client(migrated_engine: Engine, _shared_application: FastAPI) -> Iterator[TestClient]:
    from starlette.testclient import TestClient

    _ = migrated_engine
    client = TestClient(_shared_application)
    yield client
    client.close()
