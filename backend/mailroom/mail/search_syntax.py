"""The search syntax: one line such as ``from:ada is:unread "board meeting"`` as a search's
criteria, its folder and its account, the keys of ``docs/requirements/mail-client.md``
(*Search syntax*). Pure: it names an account as written (an id, ``*`` or a name) and leaves
resolving it to the caller. The web client parses the same grammar in TypeScript; both are held
to ``web/src/test/search-syntax-vectors.json``."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields, replace
from datetime import date
from typing import Any, Final, Literal

SyntaxCode = Literal["unterminated_quote", "empty_value", "bad_value", "bad_date", "conflict"]

_SPACE: Final = frozenset(" \t\n\r\f\v")
_KEY: Final = re.compile(r"([A-Za-z_]+):")
_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_FIELD: Final = {
    "from": "sender",
    "to": "to",
    "subject": "subject",
    "in": "folder",
    "account": "account",
}
_DATES: Final = {"after": "since", "before": "before"}
_IS: Final = {
    "unread": ("unread", True),
    "read": ("unread", False),
    "flagged": ("flagged", True),
    "handled": ("handled", True),
    "to_sort": ("handled", True),
}
_HAS: Final = {"attachment": ("has_attachment", True)}
_FLAGS: Final = {"is": _IS, "has": _HAS}
_FORMER: Final = frozenset({"to_sort"})
"""Values still read under their former name, which nothing offers."""
_TWICE: Final = "Two terms ask for different values of the same criterion."


class SearchSyntaxError(ValueError):
    """``code`` names the fault; ``message`` says it without quoting the query."""

    def __init__(self, code: SyntaxCode, message: str) -> None:
        super().__init__(message)
        self.code: SyntaxCode = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ParsedSearch:
    """What a query asks for; ``None`` is not asked."""

    sender: str | None = None
    to: str | None = None
    subject: str | None = None
    text: str | None = None
    since: date | None = None
    before: date | None = None
    unread: bool | None = None
    flagged: bool | None = None
    has_attachment: bool | None = None
    handled: bool | None = None
    folder: str | None = None
    account: str | None = None

    def as_json(self) -> dict[str, object]:
        """The fields given, named as ``POST /search`` names them (``from``, ``since``...), dates
        as ``YYYY-MM-DD``: the shared test vectors' form."""
        out: dict[str, object] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if value is not None:
                key = "from" if field.name == "sender" else field.name
                out[key] = value.isoformat() if isinstance(value, date) else value
        return out


def _value(query: str, start: int) -> tuple[str, int]:
    """The value from ``start`` up to the next space outside quotes, its quotes dropped, and where
    it ends."""
    out: list[str] = []
    i = start
    while i < len(query) and query[i] not in _SPACE:
        if query[i] == '"':
            end = query.find('"', i + 1)
            if end < 0:
                message = "A quote is not closed."
                raise SearchSyntaxError("unterminated_quote", message)
            out.append(query[i + 1 : end])
            i = end + 1
        else:
            out.append(query[i])
            i += 1
    return "".join(out), i


def _date(key: str, value: str) -> date:
    if _DATE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    message = f"{key}: takes a date as YYYY-MM-DD."
    raise SearchSyntaxError("bad_date", message)


def _set(parsed: ParsedSearch, name: str, value: object, conflict: str = _TWICE) -> ParsedSearch:
    """``parsed`` with ``name`` set to ``value``; refused with ``conflict`` when it already holds
    something else."""
    before = getattr(parsed, name)
    if before is not None and before != value:
        raise SearchSyntaxError("conflict", conflict)
    changes: dict[str, Any] = {name: value}
    return replace(parsed, **changes)


def parse_search(query: str) -> ParsedSearch:
    """``query`` as criteria. Terms are separated by spaces; ``key:value`` with a key of the
    syntax sets that criterion, any other term (an unknown key's too) is text, the terms joined by
    one space. Keys and the values of ``is:`` and ``has:`` ignore case. Double quotes keep spaces
    in a value and drop out of it. Refused (``SearchSyntaxError``): a quote not closed, a key
    without a value, a value ``is:``, ``has:`` or a date key does not take, and a key given twice
    with different values (``is:read is:unread`` too)."""
    parsed = ParsedSearch()
    words: list[str] = []
    i = 0
    while i < len(query):
        if query[i] in _SPACE:
            i += 1
            continue
        match = _KEY.match(query, i)
        key = match.group(1).lower() if match else ""
        if match is None or (key not in _FIELD and key not in _DATES and key not in _FLAGS):
            word, i = _value(query, i)
            if word:
                words.append(word)
            continue
        value, i = _value(query, match.end())
        if not value:
            message = f"{key}: needs a value."
            raise SearchSyntaxError("empty_value", message)
        if key in _FIELD:
            parsed = _set(parsed, _FIELD[key], value)
        elif key in _DATES:
            parsed = _set(parsed, _DATES[key], _date(key, value))
        else:
            flag = _FLAGS[key].get(value.lower())
            if flag is None:
                options = ", ".join(name for name in _FLAGS[key] if name not in _FORMER)
                message = f"{key}: takes {options}."
                raise SearchSyntaxError("bad_value", message)
            parsed = _set(parsed, *flag)
    return replace(parsed, text=" ".join(words)) if words else parsed


def combined(first: ParsedSearch, second: ParsedSearch, conflict: str) -> ParsedSearch:
    """What both ask for; refused with ``conflict`` when they give one criterion different
    values."""
    out = first
    for field in fields(second):
        value = getattr(second, field.name)
        if value is not None:
            out = _set(out, field.name, value, conflict)
    return out
