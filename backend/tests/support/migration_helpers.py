"""Alembic upgrade helpers for pytest integration database provisioning."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from mailroom.database import normalize_sync_database_url
from mailroom.models.base import ALEMBIC_VERSION_TABLE, APP_SCHEMA

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.engine import Engine


def alembic_config(backend_root: Path, *, quiet: bool = False) -> Config:
    cfg = Config(str(backend_root / "alembic.ini"))
    if quiet:
        cfg.attributes["quiet"] = True
    return cfg


def alembic_head_revision(backend_root: Path) -> str:
    script = ScriptDirectory.from_config(alembic_config(backend_root))
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("Alembic script directory has no head revision")
    return head


def postgres_sqlalchemy_url(database_url: str) -> str:
    return normalize_sync_database_url(database_url)


def is_alembic_at_head(engine: Engine, backend_root: Path) -> bool:
    head = alembic_head_revision(backend_root)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(f"SELECT version_num FROM {APP_SCHEMA}.{ALEMBIC_VERSION_TABLE}"),
            ).fetchone()
    except ProgrammingError:
        return False
    if row is None:
        return False
    return str(row[0]) == head


def run_alembic_upgrade_head(backend_root: Path, database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url
    os.environ["TEST_DATABASE_URL"] = database_url
    command.upgrade(alembic_config(backend_root, quiet=True), "head")


def ensure_migrated_database(backend_root: Path, database_url: str) -> None:
    engine = create_engine(postgres_sqlalchemy_url(database_url), pool_pre_ping=True)
    try:
        if not is_alembic_at_head(engine, backend_root):
            engine.dispose()
            run_alembic_upgrade_head(backend_root, database_url)
    finally:
        engine.dispose()
