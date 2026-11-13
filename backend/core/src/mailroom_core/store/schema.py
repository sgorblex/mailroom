"""mailroom-core's tables, in schema `core`: core's part of `src/store/db.ts`.

A host such as the Mailroom app keeps its own tables in another schema, each with its own Alembic
history in the same database.

Every row belongs to one user (`user_id`), and nothing is shared between users.
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

CORE_SCHEMA: Final = "core"

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

# Every message handed to SMTP, for each user's limits.send_per_hour (stored, so it survives
# restarts). A row written by `claim_send` also records the send's state, and with a `key` it stays
# at least 30 days instead of one, pruned on the user's next send, so a retry can't deliver twice.
sends = Table(
    "sends",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("user_id", Text, nullable=False),
    Column("at", DateTime(timezone=True), nullable=False),
    Column("key", Text),
    Column("state", Text),
    Column("account", Text),
    Column("result", JSONB(none_as_null=True)),
    Index("ix_sends_user_id_at", "user_id", "at"),
    Index(
        "uq_sends_user_id_key",
        "user_id",
        "key",
        unique=True,
        postgresql_where=text("key is not null"),
    ),
)
