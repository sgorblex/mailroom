"""Argon2id password hashes, stored per user."""

from __future__ import annotations

from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

#: Shorter passwords are refused when one is set; sign-in checks whatever is stored.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


@cache
def _dummy_hash() -> str:
    return _hasher.hash("nobody's password")


def password_matches(stored_hash: str | None, password: str) -> bool:
    """Whether ``password`` matches ``stored_hash``. With no hash (an unknown username) a dummy
    one is checked, so the answer takes as long either way, and it is always false."""
    try:
        matched = _hasher.verify(stored_hash or _dummy_hash(), password)
    except Argon2Error, InvalidHashError:
        return False
    return matched and stored_hash is not None
