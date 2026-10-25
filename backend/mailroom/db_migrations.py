from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from mailroom_core.store import database_engine
from mailroom_core.store import migrate as migrate_core_schema

from mailroom.database import get_sync_database_url, load_local_environment
from mailroom.database_connectivity import (
    CONNECTIVITY_ERRORS,
    connectivity_retry_settings,
    interruptible_connectivity_wait,
    wait_for_sync_database,
)
from mailroom.models.base import ALEMBIC_VERSION_TABLE, APP_SCHEMA

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in ("1", "true", "yes", "on")


def should_run_startup_migrations() -> bool:
    return _truthy_env("MAILROOM_RUN_STARTUP_MIGRATIONS")


def alembic_ini_path() -> Path:
    """Resolve backend/alembic.ini from the package tree or container /app layout."""
    backend_root = Path(__file__).resolve().parents[1]
    return backend_root / "alembic.ini"


def _alembic_config() -> Config:
    """Config whose ``script_location`` is absolute, so callers need not run from ``backend/``."""
    ini = alembic_ini_path()
    if not ini.is_file():
        message = f"Alembic config not found at {ini}"
        raise FileNotFoundError(message)

    config = Config(str(ini))
    location = config.get_main_option("script_location", "")
    if location and not Path(location).is_absolute():
        config.set_main_option("script_location", str(ini.parent / location))
    return config


def migrate_core(database_url: str) -> None:
    """Bring mailroom-core's schema ``core`` (its own Alembic history and version table) up to
    date. The app's history runs after it, so its revisions may rely on ``core``."""
    engine = database_engine(database_url)
    try:
        migrate_core_schema(engine)
    finally:
        engine.dispose()


def upgrade_head(*, wait_for_database: bool = True) -> None:
    """Migrate schema ``core``, then run ``alembic upgrade head`` on the app's history, optionally
    waiting for database connectivity first."""
    load_local_environment()

    config = _alembic_config()

    database_url = get_sync_database_url()
    if wait_for_database:
        probe_engine = wait_for_sync_database(database_url)
        probe_engine.dispose()

    max_attempts, retry_delay_sec = connectivity_retry_settings()

    with interruptible_connectivity_wait():
        for attempt in range(1, max_attempts + 1):
            try:
                migrate_core(database_url)
                command.upgrade(config, "head")
            except CONNECTIVITY_ERRORS as exc:
                if attempt >= max_attempts:
                    logger.warning(
                        "Database connectivity error during migrations "
                        f"(no success after {max_attempts} attempt(s))",
                        exc_info=exc,
                    )
                    raise
                logger.info(
                    "Database not ready for migrations (attempt %s/%s); retrying in %s s",
                    attempt,
                    max_attempts,
                    retry_delay_sec,
                )
                if retry_delay_sec > 0:
                    time.sleep(retry_delay_sec)
            else:
                logger.info("Database migrations completed successfully.")
                break


def apply_startup_migrations_if_configured() -> None:
    """Run ``upgrade_head`` when ``MAILROOM_RUN_STARTUP_MIGRATIONS`` is truthy.

    Retries on transient SQLAlchemy connectivity errors (``OperationalError``, etc.); other
    failures bubble up immediately.

    With multiple API worker processes, each may run migrations at startup — prefer a single worker
    or an external migrate step when scaling horizontally.
    """
    if not should_run_startup_migrations():
        return

    logger.info(
        "Applying database migrations "
        "(schema core, then Alembic upgrade head; MAILROOM_RUN_STARTUP_MIGRATIONS enabled).",
    )
    upgrade_head(wait_for_database=True)


def expected_heads() -> set[str]:
    """Head revision(s) of the Alembic scripts in this checkout."""
    return set(ScriptDirectory.from_config(_alembic_config()).get_heads())


def applied_revisions(engine: Engine) -> set[str]:
    """Revisions recorded in ``app.alembic_version``; empty when the table does not exist yet."""
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"version_table": ALEMBIC_VERSION_TABLE, "version_table_schema": APP_SCHEMA},
        )
        return set(context.get_current_heads())


def wait_for_head(engine: Engine) -> None:
    """Block until the database is at this checkout's head, for a process that never migrates
    (the worker) while another (the API) does. Polls as the connectivity wait does."""
    heads = expected_heads()
    max_attempts, retry_delay_sec = connectivity_retry_settings()
    with interruptible_connectivity_wait():
        for attempt in range(1, max_attempts + 1):
            if applied_revisions(engine) == heads:
                return
            logger.info(
                "Database not migrated to head yet (attempt %s/%s); retrying in %s s",
                attempt,
                max_attempts,
                retry_delay_sec,
            )
            if retry_delay_sec > 0:
                time.sleep(retry_delay_sec)
    message = f"database not at head {sorted(heads)} after {max_attempts} attempt(s)"
    raise RuntimeError(message)
