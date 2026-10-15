"""Shared Postgres admin-connection helpers for scratch/reset database scripts.

Connects to the ``postgres`` maintenance database (not the target database itself) to drop and
create other databases by name — used by both the migration round-trip check (a throwaway
scratch database) and the local dev database reset (the real ``DATABASE_URL`` target).
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def admin_url(database_url: str) -> str:
    """Same connection, pointed at the ``postgres`` maintenance database."""
    return database_url_for(database_url, "postgres")


def database_url_for(database_url: str, database_name: str) -> str:
    """Same connection, pointed at ``database_name`` instead of the URL's own path."""
    parsed = urlparse(database_url)
    return urlunparse(
        (parsed.scheme, parsed.netloc, f"/{database_name}", "", parsed.query, parsed.fragment),
    )


def drop_database(database_url: str, database_name: str) -> None:
    import psycopg

    with (
        psycopg.connect(admin_url(database_url), autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database_name,),
        )
        cursor.execute(f'DROP DATABASE IF EXISTS "{database_name}"')


def create_database(database_url: str, database_name: str) -> None:
    import psycopg

    with (
        psycopg.connect(admin_url(database_url), autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(f'CREATE DATABASE "{database_name}"')
