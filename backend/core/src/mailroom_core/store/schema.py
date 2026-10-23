"""mailroom-core's tables, in schema `core`: core's part of `src/store/db.ts`.

mailroom-mcp keeps its own tables in schema `mcp`, and a host such as the Mailroom app its own in
another schema, each with its own Alembic history in the same database.

Every row belongs to one user (`user_id`), and nothing is shared between users: standalone there
is only `STANDALONE_USER`, inside the Mailroom app each of its users.
"""

from typing import Final

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

CORE_SCHEMA: Final = "core"

STANDALONE_USER: Final = "owner"
"""The user of a standalone server, and of every row stored before users were keyed."""

NAMING_CONVENTION: Final = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
"""Constraint names, so that a migration can name what it drops."""

metadata = MetaData(schema=CORE_SCHEMA, naming_convention=NAMING_CONVENTION)

# Each user's accounts and limits: one validated JSON document per revision, replaced whole. The
# highest revision is the current one, so two editors cannot overwrite each other; recent ones are
# kept.
settings = Table(
    "settings",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("revision", Integer, primary_key=True, autoincrement=False),
    Column("doc", JSONB, nullable=False),
    Column("saved_at", DateTime(timezone=True), nullable=False),
)

# Mailbox secrets entered on the settings page, sealed under MAILROOM_SECRETS_KEY. Account ids are
# unique per user only.
account_secrets = Table(
    "account_secrets",
    metadata,
    Column("user_id", Text, primary_key=True),
    Column("account_id", Text, primary_key=True),
    Column("purpose", Text, primary_key=True),
    Column("blob", LargeBinary, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# Every message handed to SMTP in the last day, for each user's limits.send_per_hour (stored, so it
# survives restarts).
sends = Table(
    "sends",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("user_id", Text, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Index("ix_sends_user_id_at", "user_id", "at"),
)
