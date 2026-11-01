"""Message refs (`src/mail/ref.ts`), byte for byte: base64url (unpadded) of the JSON array
`[1, account, folder, uidValidity, uid]`, UIDVALIDITY as a decimal string. A JSON array needs no
escaping for folder names, which may contain any character. Not signed: a host checks that the
account is one the caller may use, as for any other account id it is given."""

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Final

from mailroom_core.mail.errors import MailError

_VERSION: Final = 1
_UINT32: Final = 0xFFFFFFFF
_DIGITS: Final = re.compile(r"[0-9]+", re.ASCII)
_SURROGATE: Final = re.compile("[\ud800-\udfff]")


@dataclass(frozen=True, slots=True)
class MessageRef:
    """Where a message lives, exactly: a UID means nothing without its folder's UIDVALIDITY."""

    account: str
    folder: str
    uid_validity: int
    uid: int


def js_json(value: object) -> str:
    """`JSON.stringify(value)` for strings, integers and lists: no spaces, non-ASCII as it is,
    lone surrogates escaped as JavaScript escapes them."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return _SURROGATE.sub(lambda m: f"\\u{ord(m.group(0)):04x}", text)


def b64url_encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).rstrip(b"=").decode("ascii")


def b64url_json(text: str) -> object:
    """`JSON.parse(Buffer.from(text, 'base64url').toString('utf8'))`; ValueError when it is not
    one. Node also takes the standard alphabet and padding."""
    cleaned = text.strip().rstrip("=").replace("+", "-").replace("/", "_")
    try:
        raw = base64.urlsafe_b64decode(cleaned + "=" * (-len(cleaned) % 4))
    except (binascii.Error, ValueError) as error:
        raise ValueError("not base64url") from error
    return json.loads(raw.decode("utf-8", "replace"), parse_constant=_no_constant)


def _no_constant(name: str) -> object:
    raise ValueError(name)


def js_integer(value: object) -> int | None:
    """`Number.isInteger(value)` for a parsed JSON value: the integer, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def format_ref(ref: MessageRef) -> str:
    return b64url_encode(
        js_json([_VERSION, ref.account, ref.folder, str(ref.uid_validity), ref.uid])
    )


def parse_ref(text: str) -> MessageRef:
    """`invalid_argument` unless `text` is a ref `format_ref` (or the TypeScript) made."""
    try:
        value = b64url_json(text)
    except ValueError:
        value = None
    if isinstance(value, list) and len(value) == 5:
        version, account, folder, uid_validity, uid = value
        number = js_integer(uid)
        if (
            js_integer(version) == _VERSION
            and isinstance(account, str)
            and isinstance(folder, str)
            and isinstance(uid_validity, str)
            and _DIGITS.fullmatch(uid_validity)
            # UIDs and UIDVALIDITY are 32-bit unsigned (RFC 3501, 2.3.1.1).
            and len(uid_validity) <= 10
            and number is not None
            and 0 < number <= _UINT32
        ):
            return MessageRef(account, folder, int(uid_validity), number)
    raise MailError("invalid_argument", "Not a message reference: use a ref returned by a listing.")


def check_fresh(ref: MessageRef, uid_validity: int) -> None:
    """`stale_reference` unless the folder still has the UIDVALIDITY the ref was made under."""
    if ref.uid_validity != uid_validity:
        raise MailError("stale_reference")
