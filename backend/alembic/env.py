from __future__ import annotations

import logging
import os
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import engine_from_config, pool, text

import mailroom.models as _mailroom_registered_models  # noqa: F401 -- register ORM mappers on Base.metadata
from mailroom.database import get_sync_database_url, load_local_environment
from mailroom.models.base import ALEMBIC_VERSION_TABLE, APP_SCHEMA, Base

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config

if config.config_file_name is not None:
    # Keep API logging when env.py runs in-process (fileConfig defaults to disabling others).
    fileConfig(config.config_file_name, disable_existing_loggers=False)
    if config.attributes.get("quiet"):
        logging.getLogger("alembic").setLevel(logging.WARNING)

load_local_environment()
if os.environ.get("DATABASE_URL", "").strip():
    config.set_main_option("sqlalchemy.url", get_sync_database_url())


def _resolved_database_url() -> str:
    load_local_environment()
    if os.environ.get("DATABASE_URL", "").strip():
        return get_sync_database_url()
    url = (config.get_main_option("sqlalchemy.url") or "").strip()
    if not url or url.startswith("driver://"):
        message = (
            "DATABASE_URL is not set and sqlalchemy.url is empty or still using the "
            "default placeholder in alembic.ini. Configure one of them before running migrations."
        )
        raise RuntimeError(message)
    return url


target_metadata = Base.metadata


def _include_name(name: str | None, type_: str, parent_names: object) -> bool:
    # Only this app's schema: mailroom-mcp's `mcp` schema has its own Alembic history.
    if type_ == "schema":
        return name == APP_SCHEMA
    return True


_CONFIGURE_OPTIONS: dict[str, object] = {
    "target_metadata": target_metadata,
    "compare_type": True,
    "include_schemas": True,
    "include_name": _include_name,
    "version_table": ALEMBIC_VERSION_TABLE,
    "version_table_schema": APP_SCHEMA,
}


def run_migrations_offline() -> None:
    context.configure(
        url=_resolved_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_CONFIGURE_OPTIONS,
    )

    with context.begin_transaction():
        context.run_migrations()


def _ensure_app_schema(connection: Connection) -> None:
    # The version table lives in the app schema, and Alembic creates it before the first revision
    # runs, so the schema has to exist first.
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{APP_SCHEMA}"'))
    connection.commit()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolved_database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        _ensure_app_schema(connection)
        context.configure(connection=connection, **_CONFIGURE_OPTIONS)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
