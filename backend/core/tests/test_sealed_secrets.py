"""Port of `src/crypto/sealed-secrets.test.ts`: sealing and opening, and the store keeping the
sealed blobs with the settings."""

import os

import pytest

from mailroom_core.crypto import (
    SealedSecretError,
    open_secret,
    seal_secret,
    sealed_secrets,
    secrets_subkey,
)
from mailroom_core.store import AccountStore, SecretChanges

key = secrets_subkey(os.urandom(32))

# Sealed by src/crypto/sealed-secrets.ts under MAILROOM_SECRETS_KEY = "ab" * 32, for the account
# then named by the slug "acc".
TS_KEY = bytes.fromhex("ab" * 32)
TS_SUBKEY = "813ead718db448985742728fbda10a1f6918cc5cd6f4fda28a2d64ca46808bb5"
TS_BLOB = bytes.fromhex(
    "01904f5bc84f825f848b0bfb678c8be958e4fc1dc966e2f3113083372c9323266dd55c2af97e2dcae8a2e82dd968"
)


def test_open_with_the_same_key_account_and_purpose_only() -> None:
    blob = seal_secret(key, 1, "password", "hunter2 ünïcode")
    assert b"hunter2" not in blob
    assert open_secret(key, 1, "password", blob) == "hunter2 ünïcode"
    with pytest.raises(SealedSecretError):
        open_secret(key, 2, "password", blob)
    with pytest.raises(SealedSecretError):
        open_secret(secrets_subkey(os.urandom(32)), 1, "password", blob)
    tampered = bytearray(blob)
    tampered[20] ^= 1
    with pytest.raises(SealedSecretError):
        open_secret(key, 1, "password", bytes(tampered))
    with pytest.raises(SealedSecretError):
        open_secret(key, 1, "password", bytes([2]) + blob[1:])


def test_differ_each_time_even_for_the_same_value() -> None:
    assert seal_secret(key, 1, "password", "x") != seal_secret(key, 1, "password", "x")


def test_open_a_blob_the_typescript_code_sealed() -> None:
    subkey = secrets_subkey(TS_KEY)
    assert subkey.hex() == TS_SUBKEY
    assert open_secret(subkey, 1, "password", TS_BLOB, former_slug="acc") == "hunter2 ünïcode"
    for wrong in ({}, {"former_slug": "other"}):
        with pytest.raises(SealedSecretError):
            open_secret(subkey, 1, "password", TS_BLOB, **wrong)


def test_seal_the_same_bytes_as_the_typescript_code(monkeypatch: pytest.MonkeyPatch) -> None:
    nonce = TS_BLOB[1:13]
    monkeypatch.setattr(sealed_secrets, "token_bytes", lambda _: nonce)
    sealed = seal_secret(
        secrets_subkey(TS_KEY), 9, "password", "hunter2 ünïcode", former_slug="acc"
    )
    assert sealed == TS_BLOB


def test_an_id_never_opens_what_a_slug_of_the_same_digits_sealed() -> None:
    by_slug = seal_secret(key, 3, "password", "x", former_slug="7")
    with pytest.raises(SealedSecretError):
        open_secret(key, 7, "password", by_slug)
    with pytest.raises(SealedSecretError):
        open_secret(key, 3, "password", seal_secret(key, 7, "password", "x"), former_slug="7")


def test_are_written_and_cleared_with_the_settings_in_one_save(core_engine) -> None:
    store = AccountStore(core_engine, "u")
    store.save_settings({}, 0, SecretChanges(set={1: b"x"}))
    assert store.secrets() == {1: b"x"}
    store.save_settings({}, 1, SecretChanges(remove=[1]))
    assert store.secrets() == {}
