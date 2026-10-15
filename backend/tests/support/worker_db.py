"""Postgres URLs and worker database provisioning for pytest integration tests."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
import zlib
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse, urlunparse

from sqlalchemy import create_engine

from tests.support.migration_helpers import (
    ensure_migrated_database,
    is_alembic_at_head,
    postgres_sqlalchemy_url,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", "").strip()


def xdist_worker_id() -> str:
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "").strip()
    if worker_id:
        return worker_id
    return "master"


def _postgres_scheme(scheme: str) -> bool:
    return scheme in ("postgres", "postgresql") or scheme.startswith("postgresql+")


def _loopback_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    normalized = hostname.lower().strip("[]")
    return normalized in ("localhost", "127.0.0.1", "::1")


def _ipv4_addrs_for_host(host: str) -> frozenset[str]:
    """Best-effort IPv4 addresses for ``host`` (literal or DNS)."""
    host = host.strip().strip("[]")
    try:
        return frozenset({str(ipaddress.IPv4Address(host))})
    except ValueError:
        pass
    try:
        return frozenset({socket.gethostbyname(host)})
    except OSError:
        return frozenset()


def _hostname_matches_testcontainers_override(hostname: str | None) -> bool:
    """True when the URL host is the configured Testcontainers published-port host (non-loopback)."""
    if not hostname:
        return False
    candidates = [
        os.environ.get("TESTCONTAINERS_HOST_OVERRIDE", "").strip(),
        os.environ.get("TC_HOST", "").strip(),
    ]
    candidates = [c for c in candidates if c]
    if not candidates:
        return False
    hn = hostname.strip().strip("[]")
    url_ips = _ipv4_addrs_for_host(hn)
    if not url_ips:
        return False
    for c in candidates:
        if hn.lower() == c.lower():
            return True
        if url_ips & _ipv4_addrs_for_host(c):
            return True
    return False


def test_database_url_allowed(database_url: str) -> bool:
    if os.environ.get("MAILROOM_INTEGRATION_TESTS_ALLOW_ANY_DATABASE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True
    parsed = urlparse(database_url)
    if not _postgres_scheme(parsed.scheme):
        return False
    if not (
        _loopback_hostname(parsed.hostname)
        or _hostname_matches_testcontainers_override(parsed.hostname)
    ):
        return False
    path = (parsed.path or "").lstrip("/")
    database_name = path.split("?", maxsplit=1)[0]
    return bool(database_name) and database_name.endswith("_test")


def safe_db_name(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


def database_name_from_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    path = unquote((parsed.path or "").lstrip("/"))
    return path.split("?", maxsplit=1)[0] if path else ""


def replace_database_name(database_url: str, database_name: str) -> str:
    parsed = urlparse(database_url)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            f"/{database_name}",
            "",
            parsed.query,
            parsed.fragment,
        ),
    )


def worker_database_url(base_database_url: str) -> str:
    worker_id = xdist_worker_id()
    if worker_id == "master":
        return base_database_url

    base_name = database_name_from_url(base_database_url)
    if not base_name:
        return base_database_url

    if base_name.endswith("_test"):
        root_name = base_name.removesuffix("_test")
        worker_database_name = f"{root_name}_{worker_id}_test"
        return replace_database_name(base_database_url, worker_database_name)

    # Parallel workers must not share one database (Alembic, TRUNCATE, and pooled connections).
    worker_database_name = f"{base_name}_{worker_id}"
    if not safe_db_name(worker_database_name):
        raise RuntimeError(
            "xdist parallel integration tests need a distinct database per worker. "
            f"The derived name {worker_database_name!r} is not a safe PostgreSQL identifier "
            "(use letters, digits, underscores in the base database name), or run pytest without "
            "parallel workers (omit pytest-xdist / do not pass -n)."
        )
    return replace_database_name(base_database_url, worker_database_name)


def admin_database_url(database_url: str) -> str:
    parsed = urlparse(database_url)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            "/postgres",
            "",
            parsed.query,
            parsed.fragment,
        ),
    )


def ensure_database_exists(database_url: str) -> None:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - psycopg is a runtime dependency
        raise RuntimeError("psycopg is required to provision pytest worker databases") from exc

    database_name = database_name_from_url(database_url)
    if not database_name or not safe_db_name(database_name):
        raise RuntimeError("Refusing to provision an unsafe pytest database name")

    with (
        psycopg.connect(admin_database_url(database_url), autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
        if cursor.fetchone():
            return
        cursor.execute(f'CREATE DATABASE "{database_name}"')


def database_exists(database_url: str) -> bool:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg is required to provision pytest worker databases") from exc

    database_name = database_name_from_url(database_url)
    if not database_name:
        return False
    with (
        psycopg.connect(admin_database_url(database_url), autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
        return cursor.fetchone() is not None


def _terminate_database_connections(database_url: str, database_name: str) -> None:
    import psycopg

    with (
        psycopg.connect(admin_database_url(database_url), autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = %s AND pid <> pg_backend_pid()
            """,
            (database_name,),
        )


def drop_database(database_url: str) -> None:
    import psycopg

    database_name = database_name_from_url(database_url)
    if not database_name or not safe_db_name(database_name):
        raise RuntimeError("Refusing to drop an unsafe pytest database name")
    _terminate_database_connections(database_url, database_name)
    with (
        psycopg.connect(admin_database_url(database_url), autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


_TEMPLATE_CLONE_LOCK_KEY1 = 0x6D61696C  # "mail" — serializes xdist worker template clones


def _template_clone_advisory_lock_key(template_database_name: str) -> tuple[int, int]:
    lock_key2 = zlib.crc32(template_database_name.encode()) & 0x7FFFFFFF
    return (_TEMPLATE_CLONE_LOCK_KEY1, lock_key2)


def create_database_from_template(database_url: str, *, template_database_name: str) -> None:
    import psycopg

    database_name = database_name_from_url(database_url)
    if not database_name or not safe_db_name(database_name):
        raise RuntimeError("Refusing to provision an unsafe pytest database name")
    if not safe_db_name(template_database_name):
        raise RuntimeError("Refusing to clone from an unsafe template database name")

    lock_key1, lock_key2 = _template_clone_advisory_lock_key(template_database_name)
    with psycopg.connect(admin_database_url(database_url), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s, %s)", (lock_key1, lock_key2))
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
                if cursor.fetchone():
                    return
            _terminate_database_connections(database_url, template_database_name)
            with connection.cursor() as cursor:
                cursor.execute(
                    f'CREATE DATABASE "{database_name}" WITH TEMPLATE "{template_database_name}"',
                )
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s, %s)", (lock_key1, lock_key2))


def ensure_coordinator_database_migrated(backend_root: Path, base_database_url: str) -> None:
    """Migrate the coordinator ``mailroom_test`` database once before xdist workers start."""
    ensure_database_exists(base_database_url)
    ensure_migrated_database(backend_root, base_database_url)


def provision_worker_database(backend_root: Path, base_database_url: str) -> str:
    """Return the worker database URL, cloning from ``mailroom_test`` when running under xdist."""
    worker_url = worker_database_url(base_database_url)
    base_name = database_name_from_url(base_database_url)

    if worker_url == base_database_url:
        ensure_database_exists(worker_url)
        ensure_migrated_database(backend_root, worker_url)
        return worker_url

    if not base_name:
        ensure_database_exists(worker_url)
        ensure_migrated_database(backend_root, worker_url)
        return worker_url

    if database_exists(worker_url):
        engine = create_engine(postgres_sqlalchemy_url(worker_url), pool_pre_ping=True)
        try:
            if is_alembic_at_head(engine, backend_root):
                return worker_url
        finally:
            engine.dispose()
        drop_database(worker_url)

    create_database_from_template(worker_url, template_database_name=base_name)
    return worker_url


def coordinator_process() -> bool:
    """True on pytest coordinator / serial runs; false on xdist workers."""
    return not os.environ.get("PYTEST_XDIST_WORKER", "").strip()
