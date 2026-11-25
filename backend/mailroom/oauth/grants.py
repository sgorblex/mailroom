"""A user's grants, as their *connected apps*: who they approved, when, and when it last came."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from mailroom.oauth.models import OAuthClient, OAuthGrant
from mailroom.reach import Reach, reach_of

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
    tool_groups: list[str]
    reach: Reach


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
            tool_groups=list(grant.tool_groups),
            reach=reach_of(grant.account_ids, grant.send_ceiling),
        )
        for grant, client in rows
    ]


GRANT_REVOKED: Final = "grant_revoked"
NOT_APPROVED: Final = "not_approved"


class GrantRefusal(Exception):  # noqa: N818 -- the answer, not a bug
    """Why a grant does not allow something, as ``code``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def grant_reach(db: Session, user_id: int, grant_id: str, group: str, account_id: int) -> Reach:
    """How far ``user_id``'s grant ``grant_id`` (as a token's claim has it) reaches now. Raises
    ``GrantRefusal`` when it no longer lets its app act with tool group ``group`` on
    ``account_id``: ``grant_revoked`` once it is gone, ``not_approved`` once the user took the
    group or the account back. For what an agent asked for earlier and the worker does now (a
    held send)."""
    grant = None
    if grant_id.isascii() and grant_id.isdigit() and len(grant_id) <= 18:
        grant = db.scalar(
            select(OAuthGrant).where(OAuthGrant.id == int(grant_id), OAuthGrant.user_id == user_id)
        )
    if grant is None:
        raise GrantRefusal(GRANT_REVOKED)
    reach = reach_of(grant.account_ids, grant.send_ceiling)
    if group not in grant.tool_groups or not reach.includes(account_id):
        raise GrantRefusal(NOT_APPROVED)
    return reach
