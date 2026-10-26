"""Mail-derived text, wrapped for the model: what a third party wrote is data, never
instructions.

The closing tag carries a random nonce the content cannot guess, and the tag name inside the
content is rewritten (case-insensitively), so a message cannot close the envelope early or open a
fake one. The tag name is looked for once compatibility forms are folded (NFKC: fullwidth letters;
invisible characters between them removed): when it appears there, the content is passed on
folded, every occurrence rewritten, whether or not a plain one appears too. A mitigation, not a
guarantee: homoglyphs from other scripts still pass.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from typing import Any, Final

import regex

TAG: Final = "untrusted_email"

_TAG_NAME: Final = re.compile(r"untrusted[\s_-]*email", re.IGNORECASE)
_NEUTRAL: Final = "untrusted-mail"
_LINE_BREAKS: Final = re.compile(r"\r\n?|[\u2028\u2029]")
_INVISIBLE: Final = regex.compile(
    r"[\p{Default_Ignorable_Code_Point}\p{Cf}\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)
"""Every character a reader cannot see but a model still reads: zero-width and bidi controls, the
tag block, variation selectors, other format characters, C0/C1 controls but tab and newline."""


def _cleaned(text: str) -> str:
    return _INVISIBLE.sub("", _LINE_BREAKS.sub("\n", text))


def _neutralised(content: str) -> str:
    folded = _cleaned(unicodedata.normalize("NFKC", content))
    source = folded if _TAG_NAME.search(folded) else content
    return _TAG_NAME.sub(_NEUTRAL, source)


def _nonce() -> str:
    return secrets.token_urlsafe(6)


def untrusted(content: str) -> str:
    """``content`` in the envelope. Its values are short (a sender, a subject) and sit inside
    structured output, so the server instructions, not the envelope, say what it is."""
    nonce = _nonce()
    return f'<{TAG} nonce="{nonce}">{_neutralised(content)}</{TAG} nonce="{nonce}">'


_MAIL_FIELDS: Final = ("from", "subject")
"""What a run's matched message carries from the mail itself (``MessageSummary``)."""


def wrapped_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """A run's ``result`` (imap-thingy's folder report) with each matched message's sender and
    subject in the envelope; the rest as it is."""
    if result is None:
        return None
    filters = result.get("filters")
    if not isinstance(filters, list):
        return result
    return {**result, "filters": [_wrapped_entry(entry) for entry in filters]}


def _wrapped_entry(entry: Any) -> Any:  # noqa: ANN401 -- a report entry, as stored
    if not isinstance(entry, dict) or not isinstance(entry.get("matched"), list):
        return entry
    return {**entry, "matched": [_wrapped_message(message) for message in entry["matched"]]}


def _wrapped_message(message: Any) -> Any:  # noqa: ANN401 -- a matched message, as stored
    if not isinstance(message, dict):
        return message
    return {
        key: untrusted(value) if key in _MAIL_FIELDS and isinstance(value, str) else value
        for key, value in message.items()
    }
