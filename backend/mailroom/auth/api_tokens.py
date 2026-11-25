"""Personal API tokens: what a user's script or command-line client authenticates with, on the
REST API (``mailroom.api.deps``) and on ``/mcp`` (``mailroom.mcp.verifier``). Scoped as a connected
app's grant is (tool groups, accounts, a send ceiling), acting as an agent, never as the user.

``mrt_<prefix>_<secret>``: the prefix is kept to show, the whole only as its SHA-256. Revoking
deletes the row, so it takes effect at once."""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import delete, func, or_, select, update

from mailroom.errors import Refusal
from mailroom.models import ApiToken, User
from mailroom.oauth.grants import NOT_APPROVED, GrantRefusal
from mailroom.oauth.tokens import new_secret, secret_hash
from mailroom.reach import Reach, reach_of

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_TOKENS: Final = 50
"""How many a user may have."""
TOKEN_REVOKED: Final = "token_revoked"
_TOUCH_EVERY: Final = timedelta(minutes=1)
"""``last_used_at`` is written at most this often."""
_TOKEN: Final = re.compile(r"mrt_[0-9a-f]{8}_[A-Za-z0-9_-]{43}")


@dataclass(frozen=True, slots=True)
class TokenAccess:
    """Who a live token speaks for, and how far it reaches."""

    token_id: int
    user: User
    tool_groups: frozenset[str]
    reach: Reach
    expires_at: datetime | None


def is_api_token(raw: str) -> bool:
    """Whether ``raw`` has the shape of a personal API token."""
    return _TOKEN.fullmatch(raw) is not None


def _live(now: datetime) -> ColumnElement[bool]:
    return or_(ApiToken.expires_at.is_(None), ApiToken.expires_at > now)


def create_token(
    db: Session,
    user_id: int,
    *,
    name: str,
    tool_groups: list[str],
    reach: Reach,
    expires_in: timedelta | None,
) -> tuple[ApiToken, str]:
    """A new token and, the only time it is known, its raw text. ``too_many_tokens`` past
    ``MAX_TOKENS``."""
    # The user's row, locked: two requests at once cannot both take the last place.
    db.execute(select(User.id).where(User.id == user_id).with_for_update())
    held = db.scalar(select(func.count()).where(ApiToken.user_id == user_id)) or 0
    if held >= MAX_TOKENS:
        db.rollback()
        msg = f"You can have {MAX_TOKENS} API tokens at most: revoke one first."
        raise Refusal("conflict", "too_many_tokens", msg)
    prefix = secrets.token_hex(4)
    raw = f"mrt_{prefix}_{new_secret()}"
    token = ApiToken(
        user_id=user_id,
        name=name,
        prefix=prefix,
        token_hash=secret_hash(raw),
        tool_groups=tool_groups,
        account_ids=reach.account_ids,
        send_ceiling=reach.ceiling,
        expires_at=None if expires_in is None else datetime.now(UTC) + expires_in,
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    _log("api_token_created", user_id, token.id)
    return token, raw


def user_tokens(db: Session, user_id: int) -> Sequence[ApiToken]:
    """The user's tokens, newest first, expired ones too."""
    return db.scalars(
        select(ApiToken)
        .where(ApiToken.user_id == user_id)
        .order_by(ApiToken.created_at.desc(), ApiToken.id.desc())
        .execution_options(populate_existing=True)
    ).all()


def revoke_token(db: Session, user_id: int, token_id: int) -> bool:
    """End the user's token; ``False`` when they have no such token."""
    gone = db.scalar(
        delete(ApiToken)
        .where(ApiToken.id == token_id, ApiToken.user_id == user_id)
        .returning(ApiToken.id)
    )
    db.commit()
    if gone is None:
        return False
    _log("api_token_revoked", user_id, token_id)
    return True


def delete_user_tokens(db: Session, user_id: int) -> Callable[[], None]:
    """Delete every token of the user. Leaves the commit to the caller
    (``mailroom.auth.app_access.revoke_app_access``), and returns what logs it."""
    gone = db.scalars(
        delete(ApiToken).where(ApiToken.user_id == user_id).returning(ApiToken.id)
    ).all()

    def log() -> None:
        for token_id in gone:
            _log("api_token_revoked", user_id, token_id)

    return log


def token_access(db: Session, raw: str) -> TokenAccess | None:
    """Who ``raw`` speaks for, or ``None`` when it is no token, revoked or expired. Its
    ``last_used_at`` follows, at most once a minute."""
    if not is_api_token(raw):
        return None
    now = datetime.now(UTC)
    found = db.execute(
        select(ApiToken, User)
        .join(User, User.id == ApiToken.user_id)
        .where(
            ApiToken.token_hash == secret_hash(raw),
            _live(now),
        )
        .execution_options(populate_existing=True)
    ).first()
    if found is None:
        return None
    token, user = found
    access = TokenAccess(
        token_id=token.id,
        user=user,
        tool_groups=frozenset(token.tool_groups),
        reach=reach_of(token.account_ids, token.send_ceiling),
        expires_at=token.expires_at,
    )
    if token.last_used_at is None or token.last_used_at < now - _TOUCH_EVERY:
        db.execute(update(ApiToken).where(ApiToken.id == token.id).values(last_used_at=now))
        db.commit()
    return access


def token_reach(db: Session, user_id: int, token_id: int, group: str, account_id: int) -> Reach:
    """How far ``user_id``'s token reaches now, as ``mailroom.oauth.grants.grant_reach`` says it
    of a grant: raises ``GrantRefusal``, ``token_revoked`` once it is gone or expired,
    ``not_approved`` when it never had the group, or no longer the account."""
    token = db.scalar(
        select(ApiToken).where(
            ApiToken.id == token_id,
            ApiToken.user_id == user_id,
            _live(datetime.now(UTC)),
        )
    )
    if token is None:
        raise GrantRefusal(TOKEN_REVOKED)
    reach = reach_of(token.account_ids, token.send_ceiling)
    if group not in token.tool_groups or not reach.includes(account_id):
        raise GrantRefusal(NOT_APPROVED)
    return reach


def _log(event: str, user_id: int, token_id: int) -> None:
    logger.info(event, extra={"event": event, "user_id": user_id, "token_id": token_id})
