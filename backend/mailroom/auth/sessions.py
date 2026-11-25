"""User sessions: a random token in the cookie, its SHA-256 in ``app.user_session``, with when it
was last seen and the browser that signed in."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select, update

from mailroom.auth.sign_in_client import UNKNOWN_CLIENT, SignInClient
from mailroom.models import User, UserSession

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

SESSION_COOKIE_NAME = "mailroom_session"
SESSION_TTL_DAYS_ENV = "MAILROOM_SESSION_TTL_DAYS"
_DEFAULT_TTL_DAYS = 14
SEEN_EVERY = timedelta(minutes=5)
"""``last_seen_at`` is written at most this often."""


def session_ttl() -> timedelta:
    """``MAILROOM_SESSION_TTL_DAYS`` (default 14, clamped to 1-365): an absolute lifetime."""
    raw = os.environ.get(SESSION_TTL_DAYS_ENV, "").strip()
    try:
        days = int(raw) if raw else _DEFAULT_TTL_DAYS
    except ValueError:
        days = _DEFAULT_TTL_DAYS
    return timedelta(days=min(max(days, 1), 365))


def session_cookie_attributes() -> dict[str, Any]:
    """Set alike on sign-in and sign-out. ``Secure`` everywhere but the dev server, which runs
    over plain HTTP."""
    secure = os.environ.get("MAILROOM_ENV", "").strip().lower() != "development"
    return {"path": "/", "secure": secure, "httponly": True, "samesite": "strict"}


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def end_user_sessions(db: Session, user: User, *, keep: str | None = None) -> int:
    """End every session of ``user`` but ``keep`` (a raw token); how many. Leaves the commit to
    the caller."""
    ended = delete(UserSession).where(UserSession.user_id == user.id)
    if keep is not None:
        ended = ended.where(UserSession.token_hash != _token_hash(keep))
    return len(db.scalars(ended.returning(UserSession.id)).all())


def create_session(db: Session, user: User, client: SignInClient = UNKNOWN_CLIENT) -> str:
    """Start a session for ``user``, signing in from ``client``, and return its raw token. Expired
    sessions are swept on the way."""
    now = datetime.now(UTC)
    db.execute(delete(UserSession).where(UserSession.expires_at <= now))
    raw_token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=_token_hash(raw_token),
            expires_at=now + session_ttl(),
            last_seen_at=now,
            device=client.device,
            ip=client.ip,
        )
    )
    db.commit()
    return raw_token


def session_user(db: Session, raw_token: str | None) -> User | None:
    """The user a live session belongs to, or ``None``; the session is seen now, which commits
    when it was not in the last ``SEEN_EVERY``."""
    if not raw_token:
        return None
    now = datetime.now(UTC)
    found = db.execute(
        select(User, UserSession.id, UserSession.last_seen_at)
        .join(UserSession, UserSession.user_id == User.id)
        .where(UserSession.token_hash == _token_hash(raw_token), UserSession.expires_at > now)
    ).first()
    if found is None:
        return None
    user, session_id, last_seen_at = found
    if last_seen_at <= now - SEEN_EVERY:
        db.execute(
            update(UserSession)
            .where(UserSession.id == session_id, UserSession.last_seen_at <= now - SEEN_EVERY)
            .values(last_seen_at=now)
        )
        db.commit()
    return user


def user_sessions(db: Session, user_id: int) -> Sequence[UserSession]:
    """The user's live sessions, the one seen last first."""
    return db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.expires_at > datetime.now(UTC))
        .order_by(UserSession.last_seen_at.desc(), UserSession.id.desc())
        # Read as they are now: a request before this one may have left its own copies.
        .execution_options(populate_existing=True)
    ).all()


def is_session_of(session: UserSession, raw_token: str) -> bool:
    """Whether ``raw_token`` is ``session``'s."""
    return hmac.compare_digest(session.token_hash, _token_hash(raw_token))


def end_user_session(db: Session, user_id: int, session_id: int) -> bool:
    """End the user's session ``session_id``; ``False`` when they have no such session."""
    ended = db.scalar(
        delete(UserSession)
        .where(UserSession.id == session_id, UserSession.user_id == user_id)
        .returning(UserSession.id)
    )
    db.commit()
    return ended is not None


def end_session(db: Session, raw_token: str) -> None:
    db.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(raw_token)))
    db.commit()
