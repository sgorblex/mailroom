"""One part's content, fetched with `BODY.PEEK` (never sets `\\Seen`), capped and decoded
(`src/mail/download.ts`; imapflow's `download` decodes for the TypeScript)."""

import math
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import TransferDecoder, decode_bytes, unflow
from mailroom_core.mail.errors import MailError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from imapclient import IMAPClient

    from mailroom_core.mail.structure import MimePart

_MIN_CHUNK: Final = 64 * 1024
MAX_WINDOW: Final = 1024 * 1024
"""Encoded bytes fetched at most per round trip."""
_TEXT_BODIES: Final = frozenset({"text/plain", "text/html", "text/x-amp-html"})


def _raw_budget(encoding: str, decoded: int) -> int:
    """Encoded bytes enough for `decoded` bytes in most messages: base64 with 76-character lines
    takes 78 bytes per 57, quoted-printable at worst 78 per 25."""
    if encoding == "base64":
        return math.ceil(decoded * 78 / 57) + 8
    if encoding == "quoted-printable":
        return math.ceil(decoded * 78 / 25) + 8
    return decoded


def _section(response: Mapping[bytes, object], part: str) -> bytes | None:
    prefix = f"BODY[{part}]".encode()
    for key, value in response.items():
        if (
            isinstance(key, bytes)
            and key.startswith(prefix)
            and key[len(prefix) :][:1] in (b"", b"<")
        ):
            if value is None:
                return b""
            return value if isinstance(value, bytes) else None
    return None


def part_content(client: IMAPClient, uid: int, part: MimePart, max_bytes: int) -> bytes:
    """At most `max_bytes` of `part`, transfer encoding decoded; `not_found` if the message or
    the part is gone. Fetched in windows of at most `MAX_WINDOW` until the cap or the end, each
    decoded as it arrives, so a big part never comes whole when only its start is wanted and
    memory stays the cap plus one window."""
    decoder = TransferDecoder(part.encoding)
    out = bytearray()
    offset = 0
    want = min(MAX_WINDOW, max(1, _raw_budget(part.encoding, max_bytes)))
    while True:
        key = f"BODY.PEEK[{part.part}]<{offset}.{want}>"
        response = client.fetch([uid], [key]).get(uid)
        chunk = None if response is None else _section(response, part.part)
        if chunk is None:
            raise MailError("not_found")
        offset += len(chunk)
        done = len(chunk) < want
        out.extend(decoder.feed(chunk, final=done))
        if done or len(out) >= max_bytes:
            del out[max_bytes:]
            return bytes(out)
        want = min(MAX_WINDOW, max(_MIN_CHUNK, _raw_budget(part.encoding, max_bytes - len(out))))


def part_contents(
    client: IMAPClient, wanted: Iterable[tuple[int, MimePart]], max_bytes: int
) -> dict[int, bytes]:
    """The start of one part per message, in one FETCH per part number: for previews. A message
    or part that is gone is left out. One window each: a part encoded more loosely than
    `_raw_budget` assumes comes back shorter than `max_bytes`."""
    by_part: dict[tuple[str, str], list[int]] = {}
    for uid, part in wanted:
        by_part.setdefault((part.part, part.encoding), []).append(uid)
    out: dict[int, bytes] = {}
    for (number, encoding), uids in by_part.items():
        want = max(1, _raw_budget(encoding, max_bytes))
        fetched = client.fetch(uids, [f"BODY.PEEK[{number}]<0.{want}>"])
        for uid in uids:
            response = fetched.get(uid)
            chunk = None if response is None else _section(response, number)
            if chunk is None:
                continue
            decoded = TransferDecoder(encoding).feed(chunk, final=len(chunk) < want)
            out[uid] = decoded[:max_bytes]
    return out


def body_text(data: bytes, part: MimePart, *, complete: bool) -> str:
    """A text body's bytes as text: its charset decoded (UTF-8 when unknown), `format=flowed`
    unwrapped. `complete` False means the bytes were cut, so a character cut in half at the end
    is dropped."""
    text = decode_bytes(data, part.charset, final=complete)
    if part.flowed and part.type in _TEXT_BODIES:
        return unflow(text, part.delsp)
    return text
