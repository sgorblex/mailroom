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
    from collections.abc import Callable, Iterator

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import AuditRecord, User
    from mailroom.worker.context import Mailboxes
    from mailroom_core.crypto import SecretsSubkey

USERNAME = "alice"
ADMIN_USERNAME = "root"
PASSWORD = "correct horse battery staple"

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# mise.toml sets a dev key; CI sets none. Set at import, so every application a test builds has
# one, not only the shared one.
os.environ.setdefault("MAILROOM_SECRETS_KEY", os.urandom(32).hex())


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
    """The worker's migrated database; tests that write go through ``db_session``."""
    return _worker_engine


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """A session inside one outer transaction that is rolled back after the test: the code under
    test commits to savepoints, so nothing it writes outlives the test."""
    from sqlalchemy.orm import Session

    connection = migrated_engine.connect()
    outer = connection.begin()
    session = Session(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    try:
        yield session
    finally:
        session.close()
        outer.rollback()
        connection.close()


@pytest.fixture(scope="session")
def _shared_application(_worker_engine: Engine) -> Iterator[FastAPI]:
    """Build the FastAPI app once per xdist worker, with ``DATABASE_URL`` already pointed at this
    worker's database before the lifespan runs."""
    from starlette.testclient import TestClient

    from mailroom.api.main import create_application
    from mailroom.config.features import ALL_FEATURES

    application = create_application(features=ALL_FEATURES)
    with TestClient(application):
        yield application


@pytest.fixture
def api_client(db_session: Session, _shared_application: FastAPI) -> Iterator[TestClient]:
    """A client on ``https://testserver``, so ``Secure`` cookies come back, with the routes'
    database session bound to ``db_session`` and a fresh login throttle and SSO start limit."""
    from starlette.testclient import TestClient

    from mailroom.api.deps import get_db_session
    from mailroom.auth.sso import sso_start_limit
    from mailroom.auth.throttle import LoginThrottle

    _shared_application.dependency_overrides[get_db_session] = lambda: db_session
    _shared_application.state.login_throttle = LoginThrottle()
    _shared_application.state.sso_start_limit = sso_start_limit()
    client = TestClient(_shared_application, base_url="https://testserver")
    try:
        yield client
    finally:
        client.close()
        _shared_application.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _cheap_password_hashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Argon2id with the smallest parameters: the real ones cost 64 MiB and tens of ms a hash."""
    from argon2 import PasswordHasher

    from mailroom.auth import password

    monkeypatch.setattr(
        password, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )
    password._dummy_hash.cache_clear()  # noqa: SLF001


@pytest.fixture
def user(db_session: Session) -> User:
    """``USERNAME``, signing in with ``PASSWORD``; not an admin."""
    from mailroom.auth.users import create_user

    return create_user(db_session, USERNAME, PASSWORD)


@pytest.fixture
def user_client(api_client: TestClient, db_session: Session, user: User) -> TestClient:
    """``api_client`` signed in as ``user``: a session minted directly, without the login route."""
    from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session

    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, user))
    return api_client


@pytest.fixture
def application(_shared_application: FastAPI) -> FastAPI:
    """The app ``api_client`` talks to, to adjust ``application.state`` (with ``monkeypatch``)."""
    return _shared_application


@pytest.fixture
def secrets_subkey(_shared_application: FastAPI) -> SecretsSubkey:
    """The key the app seals mailbox passwords under."""
    subkey: SecretsSubkey = _shared_application.state.secrets_subkey
    return subkey


@pytest.fixture
def admin(db_session: Session) -> User:
    """``ADMIN_USERNAME``, an admin, signing in with ``PASSWORD``."""
    from mailroom.auth.users import create_user

    return create_user(db_session, ADMIN_USERNAME, PASSWORD, is_admin=True)


@pytest.fixture
def admin_client(api_client: TestClient, db_session: Session, admin: User) -> TestClient:
    """``api_client`` signed in as ``admin``."""
    from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session

    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, admin))
    return api_client


@pytest.fixture
def sessions(db_session: Session) -> sessionmaker[Session]:
    """Sessions inside the test's transaction, as the worker opens them."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(
        bind=db_session.connection(),
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )


@pytest.fixture
def audited(
    application: FastAPI,
    sessions: sessionmaker[Session],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[int], list[AuditRecord]]:
    """The web's audit records written inside the test's transaction (they need its user), and
    a reader of a user's, oldest first."""
    from sqlalchemy import select

    from mailroom.models import AuditRecord

    monkeypatch.setattr(application.state, "session_maker", sessions)

    def read(user_id: int) -> list[AuditRecord]:
        query = select(AuditRecord).where(AuditRecord.user_id == user_id).order_by(AuditRecord.id)
        return list(db_session.scalars(query))

    return read


@pytest.fixture
def silent_server() -> Iterator[int]:
    """A port that accepts connections and never answers; closed after the test, which ends
    whatever still waits on it."""
    import socket
    import threading

    listener = socket.create_server(("127.0.0.1", 0))
    accepted: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            accepted.append(conn)

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join()
        for conn in accepted:
            conn.close()
        listener.close()


@pytest.fixture
def mailboxes(migrated_engine: Engine, secrets_subkey: SecretsSubkey) -> Mailboxes:
    """The worker's way to the test users' mailboxes."""
    from mailroom.accounts.mail import mail_ssl_context
    from mailroom.worker.context import Mailboxes

    return Mailboxes(migrated_engine, secrets_subkey, mail_ssl_context())
