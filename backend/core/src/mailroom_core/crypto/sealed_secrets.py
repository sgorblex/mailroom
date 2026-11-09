"""Mailbox secrets sealed at rest, byte-compatible with `src/crypto/sealed-secrets.ts`.

The format is in docs/core.md — *Compatibility*.
"""

from secrets import token_bytes
from typing import Literal, NewType

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

type SecretPurpose = Literal["password"]
"""What a stored secret is for; the only kind today is a mailbox password or token."""

SecretsSubkey = NewType("SecretsSubkey", bytes)
"""The key that seals stored secrets. Only `secrets_subkey` makes one: never the raw key."""

_FORMAT = 1
_NONCE_BYTES = 12
_TAG_BYTES = 16


class SealedSecretError(ValueError):
    """A blob that does not open: wrong key, altered, moved, or not a stored secret at all."""


def secrets_subkey(secrets_key: bytes) -> SecretsSubkey:
    """Derive the sealing key from MAILROOM_SECRETS_KEY (its 32 bytes, decoded) for this one use."""
    hkdf = HKDF(algorithm=SHA256(), length=32, salt=None, info=b"mailroom account secrets")
    return SecretsSubkey(hkdf.derive(secrets_key))


def seal_secret(
    subkey: SecretsSubkey, account_id: str, purpose: SecretPurpose, plaintext: str
) -> bytes:
    """AES-256-GCM, as `format ‖ nonce ‖ ciphertext ‖ tag`.

    The account and purpose are bound in as associated data: a blob copied to another account or
    use does not open.
    """
    nonce = token_bytes(_NONCE_BYTES)
    sealed = AESGCM(subkey).encrypt(
        nonce, plaintext.encode(), _associated_data(account_id, purpose)
    )
    return bytes([_FORMAT]) + nonce + sealed


def open_secret(subkey: SecretsSubkey, account_id: str, purpose: SecretPurpose, blob: bytes) -> str:
    """Raise `SealedSecretError` (without the value) unless the blob opens for this account."""
    if len(blob) < 1 + _NONCE_BYTES + _TAG_BYTES or blob[0] != _FORMAT:
        raise SealedSecretError("not a stored secret of a known format")
    nonce = blob[1 : 1 + _NONCE_BYTES]
    try:
        plaintext = AESGCM(subkey).decrypt(
            nonce, blob[1 + _NONCE_BYTES :], _associated_data(account_id, purpose)
        )
    except InvalidTag:
        raise SealedSecretError("stored secret does not open") from None
    return plaintext.decode()


def _associated_data(account_id: str, purpose: SecretPurpose) -> bytes:
    return f"mailroom/v1/{account_id}/{purpose}".encode()
