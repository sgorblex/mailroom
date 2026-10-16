"""User sessions: a random token in the cookie, its SHA-256 in ``app.user_session``."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from mailroom.models import User, UserSession

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SESSION_COOKIE_NAME = "mailroom_session"
SESSION_TTL_DAYS_ENV = "MAILROOM_SESSION_TTL_DAYS"
_DEFAULT_TTL_DAYS = 14


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


def create_session(db: Session, user: User) -> str:
    """Start a session for ``user`` and return its raw token. Expired sessions are swept on the
    way."""
    now = datetime.now(UTC)
    db.execute(delete(UserSession).where(UserSession.expires_at <= now))
    raw_token = secrets.token_urlsafe(32)
    db.add(
        UserSession(
            user_id=user.id, token_hash=_token_hash(raw_token), expires_at=now + session_ttl()
        )
    )
    db.commit()
    return raw_token


def session_user(db: Session, raw_token: str | None) -> User | None:
    """The user a live session belongs to, or ``None``."""
    if not raw_token:
        return None
    return db.scalar(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            UserSession.token_hash == _token_hash(raw_token),
            UserSession.expires_at > datetime.now(UTC),
        )
    )


def end_session(db: Session, raw_token: str) -> None:
    db.execute(delete(UserSession).where(UserSession.token_hash == _token_hash(raw_token)))
    db.commit()
