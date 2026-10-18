"""Users: who signs in, and with which password."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from mailroom.auth.password import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, hash_password
from mailroom.auth.sessions import end_user_sessions
from mailroom.models import User

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class UserError(ValueError):
    """A request about users that cannot be met. The message is safe to show; ``code`` names the
    case for a client that words it itself."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def normalized_username(raw: str) -> str:
    """Usernames compare case-insensitively, so they are stored lowercase."""
    username = raw.strip().lower()
    if not _USERNAME.fullmatch(username):
        msg = (
            "A username is 1-64 characters: letters, digits, '.', '_' or '-', "
            "starting with a letter or digit."
        )
        raise UserError(msg, "username_invalid")
    return username


def _checked_password(password: str) -> str:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        msg = f"A password is {MIN_PASSWORD_LENGTH}-{MAX_PASSWORD_LENGTH} characters."
        raise UserError(msg, "password_length")
    return password


def user_by_username(db: Session, username: str) -> User | None:
    try:
        normalized = normalized_username(username)
    except UserError:
        return None
    return db.scalar(select(User).where(User.username == normalized))


def user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.username)))


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
        raise UserError(msg, "username_taken") from exc
    return user


def set_password(
    db: Session, user: User, password: str, *, keep_session: str | None = None
) -> None:
    """Replace ``user``'s password and end their sessions but ``keep_session`` (a raw token): a
    reset signs them out everywhere, their own change everywhere else."""
    user.password_hash = hash_password(_checked_password(password))
    end_user_sessions(db, user, keep=keep_session)
    db.commit()


def reset_password(db: Session, user: User, password: str, *, by: User) -> None:
    """An admin sets ``user``'s password and signs them out everywhere. Not their own, which needs
    the current password like anyone else's."""
    if user.id == by.id:
        msg = "Change your own password on your profile."
        raise UserError(msg, "cannot_reset_self")
    set_password(db, user, password)


def remove_user(db: Session, user: User, *, by: User) -> None:
    """Their sessions go with them. Nobody removes themselves, so the admin doing it remains and
    there is always one: both rows are locked in id order, so two admins removing each other at
    once leave the second to find themselves gone."""
    if user.id == by.id:
        msg = "You cannot remove yourself."
        raise UserError(msg, "cannot_remove_self")
    locked = db.scalars(
        select(User.id).where(User.id.in_((user.id, by.id))).order_by(User.id).with_for_update()
    ).all()
    if by.id not in locked:
        db.rollback()
        msg = "You have been removed."
        raise UserError(msg, "remover_gone")
    if user.id in locked:  # else someone else removed them first
        db.delete(user)
    db.commit()
