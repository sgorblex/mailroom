"""Users: who signs in, and with which password."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mailroom.auth.password import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, hash_password
from mailroom.models import User

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class UserError(ValueError):
    """A request about users that cannot be met; the message is safe to show."""


def normalized_username(raw: str) -> str:
    """Usernames compare case-insensitively, so they are stored lowercase."""
    username = raw.strip().lower()
    if not _USERNAME.fullmatch(username):
        msg = (
            "A username is 1-64 characters: letters, digits, '.', '_' or '-', "
            "starting with a letter or digit."
        )
        raise UserError(msg)
    return username


def _checked_password(password: str) -> str:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        msg = f"A password is {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters."
        raise UserError(msg)
    return password


def user_by_username(db: Session, username: str) -> User | None:
    try:
        normalized = normalized_username(username)
    except UserError:
        return None
    return db.scalar(select(User).where(User.username == normalized))


def user_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def create_user(db: Session, username: str, password: str, *, is_admin: bool = False) -> User:
    user = User(
        username=normalized_username(username),
        password_hash=hash_password(_checked_password(password)),
        is_admin=is_admin,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        msg = f"The username {user.username!r} is taken."
        raise UserError(msg) from exc
    return user
