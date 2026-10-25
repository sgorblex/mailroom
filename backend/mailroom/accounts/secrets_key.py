"""``MAILROOM_SECRETS_KEY``: the key mailbox passwords are sealed under, as mailroom-core does."""

from __future__ import annotations

import logging
import os
import re
from typing import Final

from mailroom_core.crypto import SecretsSubkey, secrets_subkey

logger = logging.getLogger(__name__)

SECRETS_KEY_ENV: Final = "MAILROOM_SECRETS_KEY"
_HEX_KEY = re.compile(r"[0-9a-fA-F]{64}")


def secrets_subkey_from_env() -> SecretsSubkey | None:
    """The sealing key derived from ``MAILROOM_SECRETS_KEY`` (32 bytes as 64 hex digits, as
    mailroom-mcp takes it), or None, with a warning, when it is unset or malformed: accounts then
    stay listed but cannot be added, given a password or used."""
    raw = os.environ.get(SECRETS_KEY_ENV, "").strip()
    if not raw:
        logger.warning(
            "MAILROOM_SECRETS_KEY is not set: mail accounts cannot be added or used "
            "(see .env.example)."
        )
        return None
    if _HEX_KEY.fullmatch(raw) is None:
        logger.warning(
            "MAILROOM_SECRETS_KEY is not 64 hex digits (32 bytes): mail accounts cannot be added "
            "or used (see .env.example)."
        )
        return None
    return secrets_subkey(bytes.fromhex(raw))
