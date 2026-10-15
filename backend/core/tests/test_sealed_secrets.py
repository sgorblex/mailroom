"""Port of the `sealed secrets` block of `src/crypto/sealed-secrets.test.ts`.

Its `stored secrets in the config` block ports with `config` and `store`.
"""

import os

import pytest

from mailroom_core.crypto import (
    SealedSecretError,
    open_secret,
    seal_secret,
    sealed_secrets,
    secrets_subkey,
)

key = secrets_subkey(os.urandom(32))

# Sealed by src/crypto/sealed-secrets.ts under MAILROOM_SECRETS_KEY = "ab" * 32.
TS_KEY = bytes.fromhex("ab" * 32)
TS_SUBKEY = "813ead718db448985742728fbda10a1f6918cc5cd6f4fda28a2d64ca46808bb5"
TS_BLOB = bytes.fromhex(
    "01904f5bc84f825f848b0bfb678c8be958e4fc1dc966e2f3113083372c9323266dd55c2af97e2dcae8a2e82dd968"
)


def test_open_with_the_same_key_account_and_purpose_only() -> None:
    blob = seal_secret(key, "acc", "password", "hunter2 ünïcode")
    assert b"hunter2" not in blob
    assert open_secret(key, "acc", "password", blob) == "hunter2 ünïcode"
    with pytest.raises(SealedSecretError):
        open_secret(key, "other", "password", blob)
    with pytest.raises(SealedSecretError):
        open_secret(secrets_subkey(os.urandom(32)), "acc", "password", blob)
    tampered = bytearray(blob)
    tampered[20] ^= 1
    with pytest.raises(SealedSecretError):
        open_secret(key, "acc", "password", bytes(tampered))
    with pytest.raises(SealedSecretError):
        open_secret(key, "acc", "password", bytes([2]) + blob[1:])


def test_differ_each_time_even_for_the_same_value() -> None:
    assert seal_secret(key, "acc", "password", "x") != seal_secret(key, "acc", "password", "x")


def test_open_a_blob_the_typescript_code_sealed() -> None:
    subkey = secrets_subkey(TS_KEY)
    assert subkey.hex() == TS_SUBKEY
    assert open_secret(subkey, "acc", "password", TS_BLOB) == "hunter2 ünïcode"
    with pytest.raises(SealedSecretError):
        open_secret(subkey, "other", "password", TS_BLOB)


def test_seal_the_same_bytes_as_the_typescript_code(monkeypatch: pytest.MonkeyPatch) -> None:
    nonce = TS_BLOB[1:13]
    monkeypatch.setattr(sealed_secrets, "token_bytes", lambda _: nonce)
    assert seal_secret(secrets_subkey(TS_KEY), "acc", "password", "hunter2 ünïcode") == TS_BLOB
