"""Alembic's entry point for schema `core`; `mailroom_core.store.migrate` runs it."""

from mailroom_core.store.database import run_migrations
from mailroom_core.store.schema import metadata

run_migrations(metadata)
