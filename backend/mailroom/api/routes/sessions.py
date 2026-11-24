"""The user's signed-in sessions, to list and end. The session cookie only: "this session" is the
one it carries."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response
from pydantic import BaseModel

from mailroom.api.deps import CurrentUser, DbSession, SessionCookie
from mailroom.auth.sessions import (
    end_user_session,
    end_user_sessions,
    is_session_of,
    user_sessions,
)
from mailroom.auth.sign_in_client import recorded_ip
from mailroom.config.instance_settings import SessionIp, stored_settings
from mailroom.errors import Refusal
from mailroom.models import UserSession

logger = logging.getLogger(__name__)

sessions_router = APIRouter(prefix="/auth/sessions")

SessionId = Annotated[int, Path(ge=1, le=2**63 - 1)]


class SessionOut(BaseModel):
    id: int
    current: bool
    """The one this request came with."""
    device: str | None
    """Browser and system (``Firefox · Linux``), or what is known of them."""
    ip: str | None
    """As recorded at sign-in, and no more of it than the instance's ``session_ip`` lets be
    recorded now."""
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime

    @classmethod
    def of(cls, session: UserSession, *, token: str, session_ip: SessionIp) -> SessionOut:
        return cls(
            id=session.id,
            current=is_session_of(session, token),
            device=session.device,
            ip=None if session.ip is None else recorded_ip(session.ip, session_ip),
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            expires_at=session.expires_at,
        )


def _own_token(cookie: str | None) -> str:
    """The cookie's token. Whatever else may ever sign a request in, without one there is no
    session to keep."""
    if not cookie:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return cookie


@sessions_router.get("")
def list_sessions(
    response: Response, db: DbSession, user: CurrentUser, session_cookie: SessionCookie = None
) -> list[SessionOut]:
    """The user's live sessions: this one first, then the one seen last."""
    token = _own_token(session_cookie)
    response.headers["Cache-Control"] = "private, no-store"
    session_ip = stored_settings(db).session_ip
    listed = [
        SessionOut.of(session, token=token, session_ip=session_ip)
        for session in user_sessions(db, user.id)
    ]
    return sorted(listed, key=lambda session: not session.current)


@sessions_router.post("/end-others", status_code=204)
def end_other_sessions(
    db: DbSession, user: CurrentUser, session_cookie: SessionCookie = None
) -> None:
    """End every session of the user but this one."""
    ended = end_user_sessions(db, user, keep=_own_token(session_cookie))
    db.commit()
    logger.info(
        "other_sessions_ended",
        extra={"event": "other_sessions_ended", "user_id": user.id, "count": ended},
    )


@sessions_router.delete("/{session_id}", status_code=204)
def end_one_session(
    session_id: SessionId, db: DbSession, user: CurrentUser, session_cookie: SessionCookie = None
) -> None:
    """End one of the user's other sessions; this one ends by signing out, which clears the
    cookie too."""
    token = _own_token(session_cookie)
    if any(
        session.id == session_id and is_session_of(session, token)
        for session in user_sessions(db, user.id)
    ):
        raise Refusal("conflict", "current_session", "Sign out to end this session")
    if not end_user_session(db, user.id, session_id):
        raise Refusal("not_found", "session_not_found", "No such session")
    logger.info("session_ended", extra={"event": "session_ended", "user_id": user.id})
