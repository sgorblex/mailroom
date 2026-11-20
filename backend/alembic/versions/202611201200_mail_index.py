"""The mail index: ``app.mail_index_folder`` and ``app.mail_index_message``, with the
``pg_trgm`` extension for substring search.

The extension is created in schema ``app`` when the database has none, so the downgrade drops it
only when it lives there: one the database had already (in any other schema) stays.

Revision ID: 202611201200
Revises: 202611191700
Create Date: 2026-11-20 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611201200"
down_revision: str | Sequence[str] | None = "202611191700"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRGM_SCHEMA = sa.text(
    "SELECT extnamespace::regnamespace::text FROM pg_extension WHERE extname = 'pg_trgm'"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA app")
    trgm_schema = op.get_bind().scalar(_TRGM_SCHEMA)
    op.create_table(
        "mail_index_folder",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("uid_validity", sa.BigInteger(), nullable=True),
        sa.Column("highest_modseq", sa.BigInteger(), nullable=True),
        sa.Column("uid_next", sa.BigInteger(), nullable=True),
        sa.Column("oldest_indexed", sa.Date(), nullable=True),
        sa.Column("state", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("unread_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "state IN ('pending', 'syncing', 'ready', 'failed')",
            name=op.f("ck_mail_index_folder_state"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_mail_index_folder_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_index_folder")),
        sa.UniqueConstraint("id", "user_id", name=op.f("uq_mail_index_folder_id")),
        sa.UniqueConstraint(
            "user_id", "account_id", "path", name=op.f("uq_mail_index_folder_user_id")
        ),
        schema="app",
    )
    op.create_table(
        "mail_index_message",
        sa.Column("folder_id", sa.BigInteger(), nullable=False),
        sa.Column("uid", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("received", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sender", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recipients", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sender_text", sa.Text(), nullable=False),
        sa.Column("to_text", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("flags", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("keywords", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("has_attachments", sa.Boolean(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Text(), nullable=True),
        sa.Column("in_reply_to", sa.Text(), nullable=True),
        sa.Column("references_root", sa.Text(), nullable=True),
        sa.Column("thread_key", sa.Text(), nullable=True),
        sa.Column("list_id", sa.Text(), nullable=True),
        sa.Column("gm_msgid", sa.BigInteger(), nullable=True),
        sa.Column("gm_thrid", sa.BigInteger(), nullable=True),
        sa.Column(
            "labels", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["folder_id", "user_id"],
            ["app.mail_index_folder.id", "app.mail_index_folder.user_id"],
            name=op.f("fk_mail_index_message_folder_id_mail_index_folder"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("folder_id", "uid", name=op.f("pk_mail_index_message")),
        schema="app",
    )
    index = "mail_index_message"
    op.create_index(
        "ix_mail_index_message_folder_newest",
        index,
        ["folder_id", "received", "uid"],
        schema="app",
    )
    op.create_index(
        "ix_mail_index_message_user_newest",
        index,
        ["user_id", "received", "folder_id", "uid"],
        schema="app",
    )
    op.create_index(
        "ix_mail_index_message_unread",
        index,
        ["folder_id"],
        schema="app",
        postgresql_where=sa.text("NOT ('\\Seen' = ANY (flags))"),
    )
    op.create_index(
        "ix_mail_index_message_thread_key", index, ["user_id", "thread_key"], schema="app"
    )
    op.create_index(
        "ix_mail_index_message_gm_msgid",
        index,
        ["folder_id", "gm_msgid"],
        schema="app",
        postgresql_where=sa.text("gm_msgid IS NOT NULL"),
    )
    op.create_index(
        "ix_mail_index_message_labels", index, ["labels"], schema="app", postgresql_using="gin"
    )
    for column in ("subject", "sender_text", "to_text"):
        op.create_index(
            f"ix_mail_index_message_{column}_trgm",
            index,
            [column],
            schema="app",
            postgresql_using="gin",
            postgresql_ops={column: f"{trgm_schema}.gin_trgm_ops"},
        )


def downgrade() -> None:
    op.drop_table("mail_index_message", schema="app")
    op.drop_table("mail_index_folder", schema="app")
    if op.get_bind().scalar(_TRGM_SCHEMA) == "app":
        op.execute("DROP EXTENSION pg_trgm")
