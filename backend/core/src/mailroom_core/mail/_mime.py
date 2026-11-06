"""Decoding what IMAP hands over raw: header words (RFC 2047), MIME parameters (RFC 2231),
envelope addresses, and transfer-encoded part content. imapflow (with libmime) does all of this
for the TypeScript; imapclient leaves it to us."""

import base64
import binascii
import codecs
import quopri
import re
from datetime import UTC, datetime, timezone
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote_to_bytes

from mailroom_core.render import Address, clean_header

if TYPE_CHECKING:
    from collections.abc import Iterable


def text_value(value: bytes | str | None) -> str:
    """A raw header or parameter value as text: UTF-8 when it is, else Latin-1 (every byte is
    one), as servers pass 8-bit values on unchanged."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1")


_NOT_CHARSETS: Final = frozenset({"idna", "punycode", "raw_unicode_escape", "unicode_escape"})
"""Text codecs that are not charsets, and whose decoders fail on arbitrary bytes."""


def _codec(charset: str) -> codecs.CodecInfo | None:
    """The codec for a charset a sender named, or None: only text encodings, not Python's
    bytes-to-bytes codecs (`hex`, `base64`, `zlib`, `rot13`...) nor the escape ones."""
    try:
        info = codecs.lookup(charset.split("*", 1)[0].strip())
    except LookupError, ValueError:
        return None
    if not getattr(info, "_is_text_encoding", True) or info.name.replace("-", "_") in _NOT_CHARSETS:
        return None
    return info


def decode_bytes(data: bytes, charset: str | None, *, final: bool = True) -> str:
    """`data` in `charset` (UTF-8 when unknown or absent), bad bytes replaced. With `final`
    False, a character cut at the end is dropped rather than replaced. Never raises: should a
    codec still fail, the bytes are decoded as UTF-8."""
    info = _codec(charset or "utf-8") or codecs.lookup("utf-8")
    try:
        return info.incrementaldecoder(errors="replace").decode(data, final=final)
    except Exception:
        return codecs.getincrementaldecoder("utf-8")(errors="replace").decode(data, final=final)


_ENCODED_WORD: Final = re.compile(r"=\?([^?\s]+)\?([BbQq])\?([^?\s]*)\?=")
_BETWEEN_WORDS: Final = re.compile(r"[ \t\r\n]*")


def _word_bytes(encoding: str, payload: str) -> bytes | None:
    try:
        if encoding in "Bb":
            cleaned = re.sub(r"[^A-Za-z0-9+/]", "", payload)
            return base64.b64decode(cleaned + "=" * (-len(cleaned) % 4))
        return quopri.decodestring(payload.replace("_", " ").encode("latin-1"), header=False)
    except binascii.Error, ValueError, UnicodeEncodeError:
        return None


def decode_words(text: str) -> str:
    """RFC 2047 encoded words decoded, as libmime's `decodeWords`: whitespace between two encoded
    words dropped, adjacent words in one charset decoded together (a character may be split
    across them), a word in an unknown charset left as it is."""
    out: list[str] = []
    pending: tuple[str, bytearray] | None = None
    position = 0

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            out.append(decode_bytes(bytes(pending[1]), pending[0]))
            pending = None

    for match in _ENCODED_WORD.finditer(text):
        charset, encoding, payload = match.groups()
        between = text[position : match.start()]
        data = _word_bytes(encoding, payload) if _codec(charset) is not None else None
        if data is None:
            flush()
            out.append(between + match.group(0))
            position = match.end()
            continue
        adjacent = pending is not None and _BETWEEN_WORDS.fullmatch(between) is not None
        if not adjacent:
            flush()
            out.append(between)
        key = charset.split("*", 1)[0].lower()
        if pending is not None and pending[0] == key:
            pending[1].extend(data)
        else:
            flush()
            pending = (key, bytearray(data))
        position = match.end()
    flush()
    out.append(text[position:])
    return "".join(out)


def header_text(value: bytes | str | None) -> str:
    """A header value as a reader sees it: decoded, then flattened (no line breaks or invisible
    characters, whitespace collapsed, at most 998 characters)."""
    return clean_header(decode_words(text_value(value)))


_FOLD: Final = re.compile(r"\r?\n[ \t]+")


def header_values(headers: bytes | str, name: str) -> list[str]:
    """Every value of the field `name` in a raw header block, unfolded."""
    field = re.compile(
        rf"^{re.escape(name)}:[ \t]*([^\r\n]*(?:\r?\n[ \t][^\r\n]*)*)", re.IGNORECASE | re.MULTILINE
    )
    return [_FOLD.sub(" ", m.group(1)).strip() for m in field.finditer(text_value(headers))]


_PARAMETER: Final = re.compile(r"([^*]+)(?:\*(\d+))?(\*)?")


def parameters(raw: object) -> dict[str, str]:
    """A BODYSTRUCTURE parameter list (`(key, value, key, value…)`) as a dict with lowercase
    keys and decoded values: RFC 2231 continuations and charsets, then RFC 2047 words, which many
    mailers put in quoted parameters although the RFC does not allow it."""
    if not isinstance(raw, tuple | list):
        return {}
    plain: dict[str, str] = {}
    extended: dict[str, list[tuple[int, bool, bytes | str]]] = {}
    for index in range(0, len(raw) - 1, 2):
        key, value = raw[index], raw[index + 1]
        if not isinstance(key, bytes | str):
            continue
        name = text_value(key).strip().lower()
        match = _PARAMETER.fullmatch(name)
        if match is None or (match.group(2) is None and match.group(3) is None):
            plain[name] = text_value(value if isinstance(value, bytes | str) else None)
            continue
        section = int(match.group(2) or 0)
        raw_value = value if isinstance(value, bytes | str) else b""
        extended.setdefault(match.group(1), []).append(
            (section, match.group(3) is not None, raw_value)
        )
    result = {name: decode_words(value) for name, value in plain.items()}
    for name, sections in extended.items():
        result[name] = _join_sections(sorted(sections, key=lambda s: s[0]))
    return result


def _join_sections(sections: Iterable[tuple[int, bool, bytes | str]]) -> str:
    charset: str | None = None
    data = bytearray()
    for index, (_section, encoded, value) in enumerate(sections):
        text = text_value(value)
        if encoded:
            if index == 0:
                parts = text.split("'", 2)
                if len(parts) == 3:
                    charset, text = parts[0] or None, parts[2]
            data.extend(unquote_to_bytes(text))
        else:
            data.extend(text.encode("utf-8"))
    return decode_bytes(bytes(data), charset)


def addresses(raw: object) -> tuple[Address, ...]:
    """An ENVELOPE address list (imapclient `Address`es) as decoded, cleaned Addresses. Group
    markers (RFC 5322 `group: …;`, no host) are left out."""
    if not isinstance(raw, tuple | list):
        return ()
    out: list[Address] = []
    for entry in raw:
        mailbox = getattr(entry, "mailbox", None)
        host = getattr(entry, "host", None)
        if host is None:
            continue
        address = clean_header(f"{text_value(mailbox)}@{text_value(host)}", 254)
        name = header_text(getattr(entry, "name", None))
        out.append(Address(name=name or None, address=address))
    return tuple(out)


# Transfer encodings, decoded incrementally: a part fetched in pieces, or cut at a byte cap, is
# decoded as far as it goes.

_NOT_BASE64: Final = re.compile(rb"[^A-Za-z0-9+/]")


class TransferDecoder:
    """Decodes a part's content as it arrives, for `base64`, `quoted-printable` or anything
    else (passed through)."""

    __slots__ = ("_encoding", "_rest")

    def __init__(self, encoding: str) -> None:
        self._encoding = encoding.lower().strip()
        self._rest = b""

    def feed(self, chunk: bytes, final: bool = False) -> bytes:
        data = self._rest + chunk
        self._rest = b""
        if self._encoding == "base64":
            cleaned = _NOT_BASE64.sub(b"", data)
            whole = len(cleaned) - len(cleaned) % 4
            tail = cleaned[whole:]
            if not final:
                self._rest = tail
            elif len(tail) >= 2:
                # Padding was stripped with the other non-alphabet bytes: put it back.
                whole, cleaned = len(cleaned) + (-len(tail) % 4), cleaned + b"=" * (-len(tail) % 4)
            return binascii.a2b_base64(cleaned[:whole]) if whole else b""
        if self._encoding == "quoted-printable":
            if not final:
                # Keep a possibly cut escape (`=`, `=4`) or soft break (`=\r`) for the next chunk.
                cut = data.rfind(b"=", max(0, len(data) - 2))
                if cut >= 0:
                    data, self._rest = data[:cut], data[cut:]
                if data.endswith(b"\r"):
                    data, self._rest = data[:-1], b"\r" + self._rest
            return quopri.decodestring(data)
        return data


def unflow(text: str, delsp: bool) -> str:
    """RFC 3676 `format=flowed` text as plain lines: soft breaks (a line ending in a space) joined,
    space-stuffing removed, as imapflow's FlowedDecoder does."""
    out: list[str] = []
    current = ""
    lines = text.split("\n")
    for index, line in enumerate(lines):
        bare = line.removesuffix("\r")
        if bare.startswith(" "):
            bare = bare[1:]
        soft = bare.endswith(" ") and bare != "-- " and index < len(lines) - 1
        if soft:
            current += bare[:-1] if delsp else bare
            continue
        out.append(current + bare)
        current = ""
    if current:
        out.append(current)
    return "\n".join(out)


def aware_date(value: object) -> datetime | None:
    """imapclient's dates (with `normalise_times` off) with a plain fixed-offset zone; a naive
    one is taken as UTC."""
    if not isinstance(value, datetime):
        return None
    offset = value.utcoffset()
    if offset is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(timezone(offset) if offset else UTC)
