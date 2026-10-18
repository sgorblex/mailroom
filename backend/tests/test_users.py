from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select

from mailroom.auth.password import password_matches
from mailroom.auth.sessions import create_session, session_user
from mailroom.auth.users import (
    UserError,
    create_user,
    list_users,
    remove_user,
    set_password,
    user_by_username,
    user_count,
)
from mailroom.models import UserSession

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def test_create_and_find_a_user(db_session: Session) -> None:
    assert user_count(db_session) == 0
    user = create_user(db_session, "Alice", "correct horse battery staple", is_admin=True)
    assert (user.username, user.is_admin) == ("alice", True)
    assert user.password_hash.startswith("$argon2id$")
    assert user_by_username(db_session, "ALICE") == user
    assert user_by_username(db_session, "bob") is None
    assert user_by_username(db_session, "not a username") is None
    assert user_count(db_session) == 1


def test_usernames_are_unique_whatever_the_case(db_session: Session) -> None:
    create_user(db_session, "alice", "correct horse battery staple")
    with pytest.raises(UserError, match="taken"):
        create_user(db_session, "Alice", "another long password")
    assert user_count(db_session) == 1


def test_short_passwords_are_refused(db_session: Session) -> None:
    with pytest.raises(UserError, match="12-1024 characters"):
        create_user(db_session, "alice", "short")


def test_users_are_listed_by_username(db_session: Session) -> None:
    for name in ("carol", "alice", "bob"):
        create_user(db_session, name, "correct horse battery staple")
    assert [user.username for user in list_users(db_session)] == ["alice", "bob", "carol"]


def test_a_new_password_ends_the_other_sessions(db_session: Session) -> None:
    user = create_user(db_session, "alice", "correct horse battery staple")
    kept, ended = create_session(db_session, user), create_session(db_session, user)
    set_password(db_session, user, "a brand new passphrase", keep_session=kept)
    assert password_matches(user.password_hash, "a brand new passphrase")
    assert session_user(db_session, kept) == user
    assert session_user(db_session, ended) is None
    set_password(db_session, user, "yet another passphrase")
    assert session_user(db_session, kept) is None


def test_a_new_password_is_checked(db_session: Session) -> None:
    user = create_user(db_session, "alice", "correct horse battery staple")
    with pytest.raises(UserError, match="12-1024 characters"):
        set_password(db_session, user, "short")


def test_nobody_removes_themselves(db_session: Session) -> None:
    admin = create_user(db_session, "root", "correct horse battery staple", is_admin=True)
    other = create_user(db_session, "alice", "correct horse battery staple")
    create_session(db_session, other)
    with pytest.raises(UserError, match="yourself"):
        remove_user(db_session, admin, by=admin)
    remove_user(db_session, other, by=admin)
    assert [user.username for user in list_users(db_session)] == ["root"]
    assert db_session.scalar(select(func.count()).select_from(UserSession)) == 0


def test_a_removed_admin_removes_nobody(db_session: Session) -> None:
    first = create_user(db_session, "root", "correct horse battery staple", is_admin=True)
    second = create_user(db_session, "ops", "correct horse battery staple", is_admin=True)
    remove_user(db_session, second, by=first)
    with pytest.raises(UserError, match="You have been removed"):
        remove_user(db_session, first, by=second)
    assert [user.username for user in list_users(db_session)] == ["root"]
