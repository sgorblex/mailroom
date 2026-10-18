"""Admins manage users; every user changes their own password."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from mailroom.api.deps import CurrentAdmin, CurrentUser, DbSession, SessionCookie, require_admin
from mailroom.auth.password import MAX_PASSWORD_LENGTH, password_matches
from mailroom.auth.throttle import LoginThrottle, client_key
from mailroom.auth.users import (
    UserError,
    create_user,
    list_users,
    remove_user,
    reset_password,
    set_password,
    user_by_id,
)
from mailroom.models import User

logger = logging.getLogger(__name__)

users_router = APIRouter(prefix="/users", dependencies=[Depends(require_admin)])
own_password_router = APIRouter(prefix="/auth")


class ListedUser(BaseModel):
    id: int
    username: str
    is_admin: bool
    created_at: datetime

    @classmethod
    def of(cls, user: User) -> ListedUser:
        return cls(
            id=user.id, username=user.username, is_admin=user.is_admin, created_at=user.created_at
        )


class NewUser(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    is_admin: bool = False


class NewPassword(BaseModel):
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH)


# A BIGINT, so a larger id is a 422 rather than a database error.
UserId = Annotated[int, Path(ge=1, le=2**63 - 1)]


def _refused(error: UserError) -> HTTPException:
    """``detail`` carries a ``code`` so the web app can word it in the reader's language."""
    return HTTPException(status_code=400, detail={"code": error.code, "message": str(error)})


def _target(db: DbSession, user_id: int) -> User:
    user = user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such user")
    return user


@users_router.get("")
def users(db: DbSession) -> list[ListedUser]:
    return [ListedUser.of(user) for user in list_users(db)]


@users_router.post("", status_code=201)
def add_user(body: NewUser, db: DbSession, admin: CurrentAdmin) -> ListedUser:
    try:
        user = create_user(db, body.username, body.password, is_admin=body.is_admin)
    except UserError as exc:
        raise _refused(exc) from None
    logger.info(
        "user_created",
        extra={"event": "user_created", "user_id": admin.id, "target_user_id": user.id},
    )
    return ListedUser.of(user)


@users_router.delete("/{user_id}", status_code=204)
def delete_user(user_id: UserId, db: DbSession, admin: CurrentAdmin) -> None:
    user = _target(db, user_id)
    try:
        remove_user(db, user, by=admin)
    except UserError as exc:
        raise _refused(exc) from None
    logger.info(
        "user_removed",
        extra={"event": "user_removed", "user_id": admin.id, "target_user_id": user_id},
    )


@users_router.post("/{user_id}/password", status_code=204)
def reset_user_password(
    user_id: UserId, body: NewPassword, db: DbSession, admin: CurrentAdmin
) -> None:
    user = _target(db, user_id)
    try:
        reset_password(db, user, body.password, by=admin)
    except UserError as exc:
        raise _refused(exc) from None
    logger.info(
        "password_reset",
        extra={"event": "password_reset", "user_id": admin.id, "target_user_id": user_id},
    )


@own_password_router.post("/password", status_code=204)
def change_own_password(
    body: PasswordChange,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    session_cookie: SessionCookie = None,
) -> None:
    """Throttled like sign-in, so a borrowed session cannot guess the password; keeps this
    session and ends the others."""
    throttle: LoginThrottle = request.app.state.login_throttle
    client = client_key(request)
    wait = throttle.begin_attempt(client)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts",
            headers={"Retry-After": str(wait)},
        )
    if not password_matches(user.password_hash, body.current_password):
        logger.warning(
            "password_change_failed",
            extra={"event": "password_change_failed", "user_id": user.id},
        )
        raise _refused(UserError("Wrong password", "wrong_password"))
    throttle.record_success(client)
    try:
        set_password(db, user, body.new_password, keep_session=session_cookie)
    except UserError as exc:
        raise _refused(exc) from None
    logger.info("password_changed", extra={"event": "password_changed", "user_id": user.id})
