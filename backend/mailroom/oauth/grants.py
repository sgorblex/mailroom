"""A user's grants, as their *connected apps*: who they approved, when, and when it last came."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from mailroom.oauth.models import OAuthClient, OAuthGrant

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class ConnectedApp:
    grant_id: int
    client_id: str
    client_name: str | None
    client_kind: str
    created_at: datetime
    last_used_at: datetime | None


def user_grants(db: Session, user_id: int) -> list[ConnectedApp]:
    """``user_id``'s grants, newest first. Revoke one with ``tokens.revoke_grant``."""
    rows = db.execute(
        select(OAuthGrant, OAuthClient)
        .join(OAuthClient, OAuthClient.client_id == OAuthGrant.client_id)
        .where(OAuthGrant.user_id == user_id)
        .order_by(OAuthGrant.created_at.desc(), OAuthGrant.id.desc())
    ).all()
    return [
        ConnectedApp(
            grant_id=grant.id,
            client_id=client.client_id,
            client_name=client.client_name,
            client_kind=client.kind,
            created_at=grant.created_at,
            last_used_at=grant.last_used_at,
        )
        for grant, client in rows
    ]
