"""An SSO flow may sign in for an MCP client's authorization request (``MAILROOM_MCP_AUTH=oidc``).

``sso_login_state.intent`` also takes ``mcp``. Downgrading drops the flows under way with that
intent: each lasts ten minutes, and the browser that started it is told to start again.

Revision ID: 202611131200
Revises: 202611121200
Create Date: 2026-11-13 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611131200"
down_revision: str | Sequence[str] | None = "202611121200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_sso_login_state_intent"


def _allow_intents(*intents: str) -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "sso_login_state", schema="app", type_="check")
    listed = ", ".join(f"'{intent}'" for intent in intents)
    op.create_check_constraint(
        op.f(_CONSTRAINT), "sso_login_state", f"intent IN ({listed})", schema="app"
    )


def upgrade() -> None:
    _allow_intents("login", "link", "mcp")


def downgrade() -> None:
    op.execute("DELETE FROM app.sso_login_state WHERE intent = 'mcp'")
    _allow_intents("login", "link")
