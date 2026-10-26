"""Add linked sign-ins and SSO sign-ins under way.

Revision ID: 202610251200
Revises: 202610201400
Create Date: 2026-10-25 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202610251200"
down_revision: str | Sequence[str] | None = "202610201400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_identity",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_external_identity_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_identity")),
        sa.UniqueConstraint("issuer", "subject", name=op.f("uq_external_identity_issuer")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_external_identity_user_id"),
        "external_identity",
        ["user_id"],
        unique=False,
        schema="app",
    )
    op.create_table(
        "sso_login_state",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("code_verifier", sa.Text(), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False),
        sa.Column("link_user_id", sa.BigInteger(), nullable=True),
        sa.Column("return_to", sa.Text(), nullable=False),
        sa.Column("browser_binding_hash", sa.String(length=64), nullable=False),
        sa.Column("client_key", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("intent IN ('login', 'link')", name=op.f("ck_sso_login_state_intent")),
        sa.ForeignKeyConstraint(
            ["link_user_id"],
            ["app.user.id"],
            name=op.f("fk_sso_login_state_link_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("state_hash", name=op.f("pk_sso_login_state")),
        schema="app",
    )
    op.create_index(
        op.f("ix_app_sso_login_state_expires_at"),
        "sso_login_state",
        ["expires_at"],
        unique=False,
        schema="app",
    )
    op.create_index(
        op.f("ix_app_sso_login_state_client_key"),
        "sso_login_state",
        ["client_key"],
        unique=False,
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_app_sso_login_state_client_key"), table_name="sso_login_state", schema="app"
    )
    op.drop_index(
        op.f("ix_app_sso_login_state_expires_at"), table_name="sso_login_state", schema="app"
    )
    op.drop_table("sso_login_state", schema="app")
    op.drop_index(
        op.f("ix_app_external_identity_user_id"), table_name="external_identity", schema="app"
    )
    op.drop_table("external_identity", schema="app")
