"""Drop and recreate DATABASE_URL's database, then re-apply Alembic migrations (upgrade head).

Leaves an empty, fully-migrated database — no seed data. For sample data afterwards, run
``mise run seed-db``.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from mailroom.database import load_local_environment, normalize_sync_database_url
from mailroom.database_connectivity import wait_for_sync_database
from mailroom.db_migrations import upgrade_head
from mailroom.dev.db_admin import create_database, drop_database

logger = logging.getLogger(__name__)


def _base_database_url() -> str:
    """Raw ``postgresql://`` URL — psycopg.connect() rejects the ``+psycopg`` SQLAlchemy suffix."""
    load_local_environment()
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        message = "DATABASE_URL is not set; start PostgreSQL first with `mise run dev-db`."
        raise SystemExit(message)
    return raw


def _database_name(database_url: str) -> str:
    name = urlparse(database_url).path.lstrip("/")
    if not name:
        message = f"Could not determine a database name from DATABASE_URL={database_url!r}"
        raise SystemExit(message)
    return name


def reset_database() -> None:
    base_url = _base_database_url()
    database_name = _database_name(base_url)
    wait_for_sync_database(normalize_sync_database_url(base_url)).dispose()

    logger.info("Dropping and recreating database %r...", database_name)
    drop_database(base_url, database_name)
    create_database(base_url, database_name)
    logger.info("Applying Alembic migrations (upgrade head)...")
    upgrade_head(wait_for_database=False)
    logger.info("Database reset complete: %r is empty and fully migrated.", database_name)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    reset_database()


if __name__ == "__main__":
    main()
