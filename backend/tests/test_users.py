from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from mailroom.auth.users import UserError, create_user, user_by_username, user_count

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
