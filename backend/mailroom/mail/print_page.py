"""A message as a standalone page for the browser's own print: its headers escaped and its body,
the HTML one as mailroom-core sanitised it (no remote images), else the text one. The page runs
nothing; the route serves it with a CSP that lets it load nothing but its inlined images.

The body is the sender's markup in the page's own document, so it is boxed in: it paints only
inside its own box (a negative margin or an inline-block cannot cover the headers), and the
page's styles name its own elements by class, which the sanitiser never lets the sender use, so
a table in the body does not take the headers' look."""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Final

from mailroom_core.render import clean_header, format_addresses, format_date, format_size

if TYPE_CHECKING:
    from mailroom.mail.service import Printable
    from mailroom_core.render import PartInfo

_PAGE: Final = (
    "<!doctype html>\n"
    '<html><head><meta charset="utf-8">'
    '<meta name="referrer" content="no-referrer">'
    "<title>{title}</title>"
    "<style>:root{{color-scheme:light}}"
    "body{{margin:0 auto;max-width:48rem;padding:16px;background:#fff;color:#000;"
    "font-family:sans-serif;overflow-wrap:anywhere}}"
    ".subject{{font-size:1.25rem;margin:0 0 8px}}"
    ".headers{{border-collapse:collapse;margin-bottom:12px;font-size:.875rem}}"
    ".headers>tbody>tr>th{{text-align:left;vertical-align:top;padding:2px 12px 2px 0;"
    "font-weight:600}}"
    ".headers>tbody>tr>td{{padding:2px 0}}"
    ".rule{{border:0;border-top:1px solid #999;margin:0 0 12px}}"
    ".body{{position:relative;overflow:hidden;contain:paint}}"
    ".body img{{max-width:100%;height:auto}}"
    ".text{{white-space:pre-wrap;font-family:inherit;margin:0}}</style>"
    '</head><body><h1 class="subject">{title}</h1><table class="headers"><tbody>{rows}</tbody>'
    '</table><hr class="rule"><div class="body">{body}</div></body></html>\n'
)


def _row(name: str, value: str) -> str:
    return f"<tr><th>{name}</th><td>{escape(value)}</td></tr>"


def _part(part: PartInfo) -> str:
    return f"{clean_header(part.name or '', 150) or part.part}, {format_size(part.size)}"


def printable_page(printable: Printable) -> str:
    """The printable page of `printable`'s message: the headers a reader looks for (From, To,
    Cc, Date, attachments), then the body."""
    view = printable.view
    subject = clean_header(view.subject, 998) or "(no subject)"
    rows = [
        _row("From", format_addresses(view.sender) or "(unknown)"),
        _row("To", format_addresses(view.to) or "(none)"),
        *([_row("Cc", format_addresses(view.cc))] if view.cc else []),
        _row("Date", format_date(view.date, printable.time_zone)),
        *(_row("Attachment", _part(part)) for part in view.attachments),
    ]
    if printable.html is not None:
        body = printable.html.html
    elif view.body is not None:
        body = f'<pre class="text">{escape(view.body.text)}</pre>'
    else:
        body = ""
    return _PAGE.format(title=escape(subject), rows="".join(rows), body=body)
