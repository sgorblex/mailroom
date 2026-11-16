"""Sign users in and out."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from mailroom.api.deps import CurrentUser, DbSession, SessionCookie
from mailroom.auth.password import MAX_PASSWORD_LENGTH, password_matches
from mailroom.auth.sessions import (
    SESSION_COOKIE_NAME,
    create_session,
    end_session,
    session_cookie_attributes,
    session_ttl,
    session_user,
)
from mailroom.auth.throttle import LoginThrottle, client_key
from mailroom.auth.users import set_time_zone, user_by_username
from mailroom.config.features import Feature, Features
from mailroom.models import User
from mailroom_core.config import TimeZone

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class UserOut(BaseModel):
    username: str
    is_admin: bool
    time_zone: str | None
    """An IANA name; null until the user sets one, and dates are then in UTC."""

    @classmethod
    def of(cls, user: User) -> UserOut:
        return cls(username=user.username, is_admin=user.is_admin, time_zone=user.time_zone)


class TimeZoneChange(BaseModel):
    time_zone: TimeZone | None


class MeResponse(BaseModel):
    user: UserOut | None
    features: list[Feature]
    """What the instance runs (``MAILROOM_FEATURES``), signed in or not."""


@auth_router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: DbSession) -> UserOut:
    throttle: LoginThrottle = request.app.state.login_throttle
    client = client_key(request)
    wait = throttle.begin_attempt(client)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts",
            headers={"Retry-After": str(wait)},
        )
    user = user_by_username(db, body.username)
    if not password_matches(user.password_hash if user else None, body.password) or user is None:
        logger.warning("login_failed", extra={"event": "login_failed"})
        raise HTTPException(status_code=401, detail="Wrong username or password")
    throttle.record_success(client)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session(db, user),
        max_age=int(session_ttl().total_seconds()),
        **session_cookie_attributes(),
    )
    logger.info("login_succeeded", extra={"event": "login_succeeded", "user_id": user.id})
    return UserOut.of(user)


@auth_router.get("/me")
def me(request: Request, db: DbSession, session_cookie: SessionCookie = None) -> MeResponse:
    """Who this browser is signed in as; ``user`` is null rather than a 401, so the sign-in
    screen loads without a failed request."""
    user = session_user(db, session_cookie)
    features: Features = request.app.state.features
    return MeResponse(user=UserOut.of(user) if user else None, features=features.names)


@auth_router.put("/me/time-zone")
def change_time_zone(body: TimeZoneChange, db: DbSession, user: CurrentUser) -> UserOut:
    """The zone the user's dates are written in; any letter case, kept canonical (``europe/rome``
    is ``Europe/Rome``). An unknown zone is a 422; null unsets it."""
    set_time_zone(db, user, body.time_zone)
    logger.info("time_zone_changed", extra={"event": "time_zone_changed", "user_id": user.id})
    return UserOut.of(user)


@auth_router.post("/logout", status_code=204)
def logout(response: Response, db: DbSession, session_cookie: SessionCookie = None) -> None:
    """Idempotent and open, so a tab whose session has already expired can still sign out."""
    if session_cookie:
        end_session(db, session_cookie)
    response.delete_cookie(key=SESSION_COOKIE_NAME, **session_cookie_attributes())
    logger.info("logout", extra={"event": "logout"})
