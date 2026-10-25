from __future__ import annotations

import os

from alembic import command
from alembic.config import Config

from mailroom.database import load_local_environment, normalize_sync_database_url
from mailroom.database_connectivity import wait_for_sync_database
from mailroom.db_migrations import alembic_ini_path, migrate_core
from mailroom.dev.db_admin import create_database, database_url_for, drop_database

SCRATCH_DATABASE_NAME = "mailroom_migration_roundtrip_check"


def _base_database_url() -> str:
    """Raw ``postgresql://`` URL — psycopg.connect() rejects the ``+psycopg`` SQLAlchemy suffix."""
    load_local_environment()
    raw = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not raw:
        message = "TEST_DATABASE_URL is not set; start PostgreSQL first with `mise run dev-db`."
        raise SystemExit(message)
    return raw


def main() -> None:
    # Uses print() rather than logging: alembic's own env.py calls fileConfig() on every
    # command.upgrade()/downgrade(), which re-applies alembic.ini's WARN root-logger level and
    # would silently swallow our own INFO-level status lines after the first alembic call.
    base_url = _base_database_url()
    wait_for_sync_database(normalize_sync_database_url(base_url)).dispose()

    drop_database(base_url, SCRATCH_DATABASE_NAME)
    create_database(base_url, SCRATCH_DATABASE_NAME)
    try:
        os.environ["DATABASE_URL"] = database_url_for(base_url, SCRATCH_DATABASE_NAME)
        config = Config(str(alembic_ini_path()))
        # mailroom-core's history is its own (and checked there); the app's runs after it.
        print("schema core (mailroom-core's migrations)...")
        migrate_core(normalize_sync_database_url(os.environ["DATABASE_URL"]))
        print("alembic upgrade head (scratch database)...")
        command.upgrade(config, "head")
        print("alembic downgrade base...")
        try:
            command.downgrade(config, "base")
        except NotImplementedError as exc:
            # A migration may deliberately raise NotImplementedError in downgrade() to mark
            # itself as a one-way door (e.g. a lossy data-model change already applied to
            # main). alembic runs the whole downgrade chain in one transaction, so this
            # rolls the scratch database back to head automatically — treat it as an
            # intentional stopping point, not a broken downgrade.
            print(f"Downgrade chain stopped at a migration marked non-reversible: {exc}")
        print("alembic upgrade head again (round-trip)...")
        command.upgrade(config, "head")
        print("Migration downgrade round-trip check passed.")
    finally:
        drop_database(base_url, SCRATCH_DATABASE_NAME)


if __name__ == "__main__":
    main()
