"""A port of email-reply-parser 2.3.9 (https://github.com/crisp-oss/email-reply-parser), the
library `src/render/quotes.ts` uses: its `parse()` and the fragments it returns, with the same
multi-language quote headers and signatures. The patterns are its JavaScript ones, compiled with
JavaScript semantics (`_js.js_regex`), so a text splits into the same fragments.

The MIT License (MIT)

Copyright (c) 2021 Crisp IM SAS

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from dataclasses import dataclass, field
from typing import Final

from mailroom_core.render._js import WHITESPACE, JsRegex, js_regex, replace_first

_QUOTE_HEADERS: Final = [
    js_regex(pattern, flags)
    for pattern, flags in (
        (r"^-*\s*(On\s.+\s.+\n?wrote:{0,1})\s{0,1}-*$", "m"),
        (r"^-*\s*(Le\s.+\s.+\n?écrit\s?:{0,1})\s{0,1}-*$", "m"),
        (r"^-*\s*(El\s.+\s.+\n?escribió:{0,1})\s{0,1}-*$", "m"),
        (r"^-*\s*(Il\s.+\s.+\n?scritto:{0,1})\s{0,1}-*$", "m"),
        (r"^-*\s*(Em\s.+\s.+\n?escreveu:{0,1})\s{0,1}-*$", "m"),
        (r"^\s*(Am\s.+\s)\n?\n?schrieb.+\s?(\[|<).+(\]|>):$", "m"),
        (r"^\s*(Op\s[\s\S]+?\n?schreef[\s\S]+:)$", "m"),
        (r"^\s*((W\sdniu|Dnia)\s[\s\S]+?(pisze|napisał(\(a\))?):)$", "mu"),
        (r"^\s*(Den\s.+\s\n?skrev\s.+:)$", "m"),
        (r"^\s*(pe\s.+\s.+\n?kirjoitti:)$", "m"),
        (r"^\s*(Am\s.+\sum\s.+\s\n?schrieb\s.+:)$", "m"),
        (r"^\s*(ср\,\s.+\n? г\. в\s.+,\s.+[\[|<].+[\]|>]:)$", "m"),  # noqa: RUF001 -- the library's pattern
        (r"^(在[\s\S]+写道：)$", "m"),  # noqa: RUF001 -- the library's pattern
        (r"^(20[0-9]{2}\..+\s작성:)$", "m"),
        (r"^(20[0-9]{2}\/.+のメッセージ:)$", "m"),
        (r"^(.+\s<.+>\sschrieb:)$", "m"),
        (r"^(.+\son.*at.*wrote:)$", "m"),
        (r"^\s*(From\s?:.+\s?\n?\s*[\[|<].+[\]|>])", "m"),
        (r"^\s*(Von\s?:.+\s?\n?\s*[\[|<].+[\]|>])", "m"),
        (r"^\s*(De\s?:.+\s?\n?\s*(\[|<).+(\]|>))", "m"),
        (r"^\s*(Van\s?:.+\s?\n?\s*(\[|<).+(\]|>))", "m"),
        (r"^\s*(Da\s?:.+\s?\n?\s*(\[|<).+(\]|>))", "m"),
        (r"^(20[0-9]{2})-([0-9]{2}).([0-9]{2}).([0-9]{2}):([0-9]{2})\n?(.*)>:$", "m"),
        (r"^\s*([a-z]{3,4}\.\s[\s\S]+\sskrev\s[\s\S]+:)$", "m"),
        (
            r"^([0-9]{2}).([0-9]{2}).(20[0-9]{2})(.*)(([0-9]{2}).([0-9]{2}))(.*)\"( *)<(.*)>( *):$",
            "m",
        ),
        (r"^[0-9]{2}:[0-9]{2}(.*)[0-9]{4}(.*)\"( *)<(.*)>( *):$", ""),
        (r"^(.*)[0-9]{4}(.*)from(.*)<(.*)>:$", ""),
        (r"^-{1,12} ?(O|o)riginal (M|m)essage ?-{1,12}$", "i"),
        (r"^-{1,12} ?(O|o)prindelig (B|b)esked ?-{1,12}$", "i"),
        (r"^-{1,12} ?(M|m)essage d\'origine ?-{1,12}$", "i"),
        (r"^-{1,12} ?(U|u)rsprüngliche (N|n)achricht ?-{0,12}$", "i"),
    )
]

_SIGNATURES: Final = [
    js_regex(pattern, flags)
    for pattern, flags in (
        (r"^\s*-{2,4}$", ""),
        (r"^\s*_{2,4}$", ""),
        (r"^-- $", ""),
        (r"^\+{2,4}$", ""),
        (r"^\={2,4}$", ""),
        (r"^________________________________$", ""),
        (r"^Sent from (?:\s*.+)$", ""),
        (r"^Get Outlook for (?:\s*.+).*", "m"),
        (r"^Cheers,?!?$", "mi"),
        (r"^Best wishes,?!?$", "mi"),
        (r"^\w{0,20}\s?(\sand\s)?Regards,?!?！?$", "mi"),  # noqa: RUF001 -- the library's pattern
        (r"^Von (?:\s*.+) gesendet$", ""),
        (r"^Gesendet von (?:\s*.+) für (?:\s*.+)$", ""),
        (r"^Sendt fra (?:\s*.+)$", ""),
        (r"^Envoyé depuis (?:\s*.+)$", ""),
        (r"^Envoyé de mon (?:\s*.+)$", ""),
        (r"^Envoyé à partir de (?:\s*.+)$", ""),
        (r"^Télécharger Outlook pour (?:\s*.+).*", "m"),
        (r"^Bien . vous,?!?$", "mi"),
        (r"^\w{0,20}\s?cordialement,?!?$", "mi"),
        (r"^Bonne (journ.e|soir.e)!?$", "mi"),
        (r"^Enviado desde (?:\s*.+)$", ""),
        (r"^-*\s*(In\sdata\s.+\s.+\n?scritto:{0,1})\s{0,1}-*$", "m"),
        (r"^Verzonden vanaf (?:\s*.+)$", ""),
        (r"^Verstuurd vanaf (?:\s*.+)$", ""),
    )
]


# Bounds the library does not have. Its patterns backtrack polynomially on long lines, and
# Python's matcher holds the GIL, so one hostile mail would stall every thread of a host. Real
# headers and signatures are far shorter than a line limit, and sit near the end of the text,
# which the parser walks first; past these limits a line is plain text (docs/core.md, *Render*).
MAX_LINE: Final = 256
"""Longer lines are never a quote header or a signature, nor joined into one."""
MAX_TESTS: Final = 2000
"""Distinct lines tested as a header or a signature, from the end of the text."""
MAX_JOIN_LINES: Final = 500
"""Lines, from the end of the text, where a header wrapped over two lines is joined."""


@dataclass(frozen=True, slots=True)
class Fragment:
    content: str
    hidden: bool
    signature: bool
    quoted: bool


@dataclass(slots=True)
class _Draft:
    quoted: bool
    lines: list[str] = field(default_factory=list)
    hidden: bool = False
    signature: bool = False


def _reverse(text: str) -> str:
    # The library reverses UTF-16 units and reverses back before every test that sees the text;
    # code points give the same lines and the same matches.
    return text[::-1]


class _Tests:
    """The library's per-line tests, on reversed lines, remembered per line and bounded by
    `MAX_LINE` and `MAX_TESTS`."""

    __slots__ = ("_budget", "_headers", "_signatures")

    def __init__(self) -> None:
        self._budget = MAX_TESTS
        self._signatures: dict[str, bool] = {}
        self._headers: dict[str, bool] = {}

    def _test(self, known: dict[str, bool], patterns: list[JsRegex], line: str) -> bool:
        result = known.get(line)
        if result is not None:
            return result
        if len(line) > MAX_LINE or self._budget <= 0:
            return False
        self._budget -= 1
        text = _reverse(line)
        result = any(regex.search(text) for regex in patterns)
        known[line] = result
        return result

    def signature(self, line: str) -> bool:
        return self._test(self._signatures, _SIGNATURES, line)

    def quote_header(self, line: str) -> bool:
        return self._test(self._headers, _QUOTE_HEADERS, line)


def _fix_broken_signatures(text: str) -> str:
    """Joins a quote header that a mail client wrapped over two lines.

    The library searches each pattern over the whole text; here the first match is looked for
    in each pair of adjacent lines within `MAX_JOIN_LINES` of the end, so the work is linear.
    """
    lines = text.split("\n")
    for regex in _QUOTE_HEADERS:
        for index in range(max(0, len(lines) - MAX_JOIN_LINES), len(lines) - 1):
            window = f"{lines[index]}\n{lines[index + 1]}"
            if len(window) > MAX_LINE:
                continue
            match = regex.search(window)
            if match is None or match.start() > len(lines[index]):
                continue  # none in this line; the next window has that one
            group = match.group(1)
            if "\n" in group:
                text = replace_first(text, group, group.replace("\n", " "))
                lines = text.split("\n")
            break
    return text


def _rtrim_newlines(line: str) -> str:
    end = len(line)
    while end > 0 and line[end - 1] == "\n":
        end -= 1
    return line[:end]


def parse(text: str) -> list[Fragment]:
    """The fragments of a plain-text mail, in order: what is quoted, signature or empty is
    hidden."""
    text = _fix_broken_signatures(text.replace("\r\n", "\n"))
    tests = _Tests()
    drafts: list[_Draft] = []

    def add(draft: _Draft) -> None:
        if draft.quoted or draft.signature or "".join(draft.lines) == "":
            draft.hidden = True
        drafts.append(draft)

    draft: _Draft | None = None
    for raw in _reverse(text).split("\n"):
        line = _rtrim_newlines(raw)
        if not tests.signature(line):
            line = line.lstrip(WHITESPACE)  # the library's /^\s+/
        if draft is not None:
            last = draft.lines[-1]
            if tests.signature(last):
                draft.signature = True
                add(draft)
                draft = None
            elif line == "" and tests.quote_header(last):
                draft.quoted = True
                add(draft)
                draft = None
        # The library's /(>+)$/ on the reversed line, without its quadratic backtracking.
        quoted = line.endswith(">")
        if draft is None or not (
            draft.quoted == quoted or (draft.quoted and (tests.quote_header(line) or line == ""))
        ):
            if draft is not None:
                add(draft)
            draft = _Draft(quoted=quoted)
        draft.lines.append(line)
    if draft is not None:
        add(draft)

    fragments = []
    for done in reversed(drafts):
        content = _reverse("\n".join(done.lines))
        fragments.append(
            Fragment(content.removeprefix("\n"), done.hidden, done.signature, done.quoted)
        )
    return fragments
