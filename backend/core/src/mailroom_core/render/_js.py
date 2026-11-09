"""JavaScript string and regular-expression semantics, where the TypeScript port relies on them.

The render outputs must match `src/render` byte for byte, and they are measured the way the model
and the TypeScript measure them: lengths and offsets in UTF-16 code units, `\\s` and `trim()` over
JavaScript's whitespace, `\\w` over ASCII, `.`, `^` and `$` over JavaScript's line terminators.
"""

import math
import re
from bisect import bisect_left
from typing import Final

WHITESPACE: Final = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
"""JavaScript's `\\s`, and what `trim()` removes."""

_LINE_TERMINATORS: Final = "\n\r\u2028\u2029"

_CLASS_ESCAPES: Final = {
    "s": re.escape(WHITESPACE),
    "w": "A-Za-z0-9_",
    "d": "0-9",
}


def _class_escape(letter: str) -> str:
    return _CLASS_ESCAPES[letter]


def _translate(pattern: str, multiline: bool, *, newline_only: bool) -> str:
    """A JavaScript pattern (the subset the render code and email-reply-parser use) as Python's.
    With `newline_only`, for texts whose only line terminator is `\n`: Python's own `.`, `^`
    and `$`, which its matcher runs about three times faster."""
    out: list[str] = []
    in_class = False
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if not in_class and pattern.startswith(r"[\s\S]", i):
            out.append("(?s:.)")
            i += 6
            continue
        if char == "\\":
            escaped = pattern[i + 1]
            i += 2
            if escaped in "swd":
                body = _class_escape(escaped)
                out.append(body if in_class else f"[{body}]")
            elif escaped in "SWD":
                if in_class:
                    msg = f"\\{escaped} inside a character class: {pattern!r}"
                    raise ValueError(msg)
                out.append(f"[^{_class_escape(escaped.lower())}]")
            elif escaped in "bB":
                msg = f"\\{escaped} is not translated: {pattern!r}"
                raise ValueError(msg)
            else:
                out.append("\\" + escaped)
            continue
        i += 1
        if in_class:
            if char == "]":
                in_class = False
            out.append(char)
        elif char == "[":
            in_class = True
            out.append(char)
            if pattern.startswith("^", i):
                out.append("^")
                i += 1
        elif char == ".":
            out.append("." if newline_only else f"[^{_LINE_TERMINATORS}]")
        elif char == "^":
            if not multiline:
                out.append(r"\A")
            else:
                out.append("^" if newline_only else f"(?<![^{_LINE_TERMINATORS}])")
        elif char == "$":
            if not multiline:
                out.append(r"\Z")
            else:
                out.append("$" if newline_only else f"(?![^{_LINE_TERMINATORS}])")
        else:
            out.append(char)
    return "".join(out)


_OTHER_TERMINATORS: Final = re.compile("[\r\u2028\u2029]")


class JsRegex:
    """`new RegExp(pattern, flags)`, for flags among `i`, `m` and `u`; global matching is the
    method's (`sub` replaces all, `search` finds the first)."""

    __slots__ = ("_exact", "_fast")

    def __init__(self, pattern: str, flags: str = "") -> None:
        unknown = set(flags) - set("imu")
        if unknown:
            msg = f"unsupported flags {''.join(sorted(unknown))!r}"
            raise ValueError(msg)
        multiline = "m" in flags
        options = (re.IGNORECASE if "i" in flags else 0) | (re.MULTILINE if multiline else 0)
        self._exact = re.compile(_translate(pattern, multiline, newline_only=False), options)
        self._fast = re.compile(_translate(pattern, multiline, newline_only=True), options)

    def _for(self, text: str) -> re.Pattern[str]:
        return self._exact if _OTHER_TERMINATORS.search(text) else self._fast

    def for_text(self, text: str) -> re.Pattern[str]:
        """The compiled pattern for `text` and any part of it: for many searches in one text."""
        return self._for(text)

    def count(self, text: str) -> int:
        """How many times the pattern matches, as `text.match(/…/g).length`."""
        return sum(1 for _ in self._for(text).finditer(text))

    def search(self, text: str) -> re.Match[str] | None:
        return self._for(text).search(text)

    def sub(self, replacement: str, text: str, count: int = 0) -> str:
        return self._for(text).sub(replacement, text, count=count)


def js_regex(pattern: str, flags: str = "") -> JsRegex:
    return JsRegex(pattern, flags)


_TRIM_START: Final = re.compile(f"^[{re.escape(WHITESPACE)}]+")


def trim_start(text: str) -> str:
    return _TRIM_START.sub("", text, count=1)


def trim_end(text: str) -> str:
    # Not `$`, which matches before a final newline too: search the end explicitly.
    end = len(text)
    while end > 0 and text[end - 1] in WHITESPACE:
        end -= 1
    return text[:end]


def trim(text: str) -> str:
    return trim_start(trim_end(text))


def replace_first(text: str, search: str, replacement: str) -> str:
    """`text.replace(search, replacement)` with a string `search`: the first occurrence only, and
    the replacement's `$$`, `$&`, `` $` `` and `$'` expanded as JavaScript does."""
    index = text.find(search)
    if index < 0:
        return text
    before, after = text[:index], text[index + len(search) :]
    out: list[str] = []
    i = 0
    while i < len(replacement):
        char = replacement[i]
        following = replacement[i + 1] if i + 1 < len(replacement) else ""
        if char == "$" and following in ("$", "&", "`", "'"):
            out.append({"$": "$", "&": search, "`": before, "'": after}[following])
            i += 2
        else:
            out.append(char)
            i += 1
    return before + "".join(out) + after


def utf16_length(text: str) -> int:
    """`text.length`: astral characters count twice."""
    if text.isascii():
        return len(text)
    return len(text) + sum(1 for char in text if ord(char) > 0xFFFF)


class Utf16Index:
    """Converts between UTF-16 offsets (JavaScript's) and Python's code-point indexes in one
    text."""

    __slots__ = ("_astral", "_starts", "length", "text")

    def __init__(self, text: str) -> None:
        self.text = text
        self._astral = (
            [] if text.isascii() else [i for i, char in enumerate(text) if ord(char) > 0xFFFF]
        )
        # The UTF-16 offset each astral character starts at.
        self._starts = [index + n for n, index in enumerate(self._astral)]
        self.length = len(text) + len(self._astral)

    def splits_pair(self, offset: int) -> bool:
        """Whether `offset` falls between the two halves of a surrogate pair."""
        n = bisect_left(self._starts, offset - 1)
        return n < len(self._starts) and self._starts[n] == offset - 1

    def safe_cut(self, offset: int) -> int:
        """`safeCut`: one unit earlier when `offset` would split a surrogate pair."""
        return offset - 1 if 0 < offset < self.length and self.splits_pair(offset) else offset

    def to_index(self, offset: int) -> int:
        """The code-point index at a UTF-16 offset that splits no pair."""
        return offset - bisect_left(self._starts, offset)

    def to_offset(self, index: int) -> int:
        return index + bisect_left(self._astral, index)

    def slice(self, start: int, end: int) -> str:
        """`text.slice(start, end)` for offsets that split no pair."""
        return self.text[self.to_index(start) : self.to_index(end)]

    def last_index_of(self, search: str, position: int) -> int:
        """`text.lastIndexOf(search, position)`, as a UTF-16 offset (-1 when absent)."""
        found = self.text.rfind(search, 0, self.to_index(position) + len(search))
        return -1 if found < 0 else self.to_offset(found)


def utf16_prefix(text: str, length: int) -> str:
    """`text.slice(0, length)`, without the high half of a pair the cut would split."""
    index = Utf16Index(text)
    return index.slice(0, index.safe_cut(min(length, index.length)))


_DECIMAL: Final = re.compile(r"(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def number(text: str) -> float:
    """`Number(text)` for a string."""
    body = trim(text)
    if body == "":
        return 0.0
    sign, digits = (body[0], body[1:]) if body[0] in "+-" else ("", body)
    if digits == "Infinity":
        return -math.inf if sign == "-" else math.inf
    radix = {"0x": 16, "0X": 16, "0o": 8, "0O": 8, "0b": 2, "0B": 2}.get(body[:2])
    if radix is not None:
        try:
            return float(int(body[2:], radix)) if body[2:].isalnum() else math.nan
        except ValueError:
            return math.nan
    if not _DECIMAL.fullmatch(digits):
        return math.nan
    return float(sign + digits)


def number_text(value: float) -> str:
    """`String(value)` for the numbers a list index can be (the shortest round trip, as
    JavaScript prints it)."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == int(value) and abs(value) < 1e21:
        return str(int(value))
    text = repr(value)
    if "e" in text:
        mantissa, exponent = text.split("e")
        sign = "-" if exponent.startswith("-") else "+"
        return f"{mantissa.removesuffix('.0')}e{sign}{exponent.lstrip('+-').lstrip('0') or '0'}"
    return text


def to_int32(value: float) -> int:
    if not math.isfinite(value):
        return 0
    wrapped = int(value) % 2**32
    return wrapped - 2**32 if wrapped >= 2**31 else wrapped


def to_uint16(value: float) -> int:
    return 0 if not math.isfinite(value) else int(value) % 2**16
