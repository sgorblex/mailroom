"""Listing, searching and following a conversation (`src/mcp/tools/listing.ts` and
`thread.ts`): which sources and criteria, over `read_page`."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom_core.mail._mime import text_value
from mailroom_core.mail.errors import MailError
from mailroom_core.mail.pages import Page, PageFailure, Source, read_page
from mailroom_core.mail.ref import MessageRef, check_fresh, parse_ref

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from mailroom_core.mail.imap import MailboxContext, MailConnections
    from mailroom_core.mail.selected import Criteria

ALL_FOLDERS: Final = "*"
"""A search's folder (or account) meaning all of them."""
_MAX_THREAD_IDS: Final = 20
"""References beyond this many (the most recent ones) are ignored: the OR query grows with each."""
_MESSAGE_ID: Final = re.compile(r"<[!-;=?-~]+>")
"""A Message-ID as it can be searched for: printable ASCII, no space or angle bracket inside."""
_THREAD_HEADERS: Final = ("Message-ID", "References", "In-Reply-To")


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """A search's criteria, all of which must match; none matches everything."""

    sender: str | None = None
    """From contains this (address or name)."""
    to: str | None = None
    subject: str | None = None
    text: str | None = None
    """Headers or body contain this."""
    since: date | None = None
    """Arrived on or after this day: IMAP compares INTERNALDATE (when the server received the
    message, by the server's day), not the Date header."""
    before: date | None = None
    """Arrived before this day, by INTERNALDATE likewise."""
    unread: bool | None = None
    flagged: bool | None = None
    has_attachment: bool | None = None
    """True matches messages with an attachment: Gmail's own `has:attachment`, elsewhere an
    approximation (sent as multipart/mixed). False is ignored."""
    gmail_query: str | None = None
    """Gmail search syntax (X-GM-RAW); Gmail accounts only, else `unsupported`."""

    def criteria(self, *, gmail: bool = False) -> list[object]:
        """As core's own `Criteria` (written by `search_arguments`, not imapclient), imapflow's
        compilation of the TypeScript query; for an account whose capabilities say `gmail`,
        `has_attachment` goes into X-GM-RAW, since Gmail ignores SEARCH HEADER."""
        out: list[object] = []
        for key, value in (
            ("FROM", self.sender),
            ("TO", self.to),
            ("SUBJECT", self.subject),
            ("TEXT", self.text),
        ):
            if value is not None:
                out += [key, value]
        if self.since is not None:
            out += ["SINCE", self.since]
        if self.before is not None:
            out += ["BEFORE", self.before]
        if self.unread is not None:
            out.append("UNSEEN" if self.unread else "SEEN")
        if self.flagged is not None:
            out.append("FLAGGED" if self.flagged else "UNFLAGGED")
        raw = self.gmail_query
        if self.has_attachment is True:
            if gmail:
                raw = f"has:attachment ({raw})" if raw and raw.strip() else "has:attachment"
            else:
                out += ["HEADER", "Content-Type", "multipart/mixed"]
        if raw is not None:
            out += ["X-GM-RAW", raw]
        return out or ["ALL"]


def searchable_folders(connections: MailConnections, account: str) -> list[str]:
    """The folders a search over all folders reads: not Junk or Trash, and on Gmail only All
    Mail (every label at once)."""
    folders = connections.folders(account)
    every = next((f for f in folders if f.role == "all"), None)
    if every is not None:
        return [every.path]
    return [f.path for f in folders if f.selectable and f.role not in ("junk", "trash")]


def list_messages(
    connections: MailConnections,
    account: str,
    folder: str = "INBOX",
    *,
    limit: int = 20,
    cursor: str | None = None,
    previews: bool = True,
) -> Page:
    """The newest messages of one folder. A failing folder is the page's only failure: a host
    showing one folder may raise `MailError(page.failures[0].code)` instead."""
    return read_page(
        connections,
        [Source(account, folder)],
        ["ALL"],
        limit=limit,
        cursor=cursor,
        previews=previews,
    )


def search_messages(
    connections: MailConnections,
    accounts: Sequence[str],
    folder: str,
    query: SearchQuery,
    *,
    limit: int = 20,
    cursor: str | None = None,
    previews: bool = True,
) -> Page:
    """`query` over `accounts` (the ids the caller may use, already resolved from `*`), in one
    folder or all (`ALL_FOLDERS`). With several accounts one that cannot be searched becomes a
    failure with no folder and the others still answer; with one, its MailError is raised."""
    sources: list[Source] = []
    failures: list[PageFailure] = []
    criteria: dict[str, Criteria] = {}
    for account in accounts:
        try:
            gmail = (
                query.gmail_query is not None or query.has_attachment is True
            ) and connections.capabilities(account).gmail
            if query.gmail_query is not None and not gmail:
                raise MailError(
                    "unsupported",
                    f'gmail_query works only on Gmail accounts; "{account}" is not one.',
                )
            folders = (
                searchable_folders(connections, account) if folder == ALL_FOLDERS else [folder]
            )
            sources.extend(Source(account, path) for path in folders)
            criteria[account] = query.criteria(gmail=gmail)
        except MailError as error:
            if len(accounts) == 1:
                raise
            failures.append(PageFailure.of(account, None, error))
    page = read_page(connections, sources, criteria, limit=limit, cursor=cursor, previews=previews)
    return Page(page.messages, page.next_cursor, (*failures, *page.failures))


def _any_of(terms: Sequence[list[object]]) -> list[object]:
    """A balanced tree of IMAP's binary OR over `terms`, so a long list stays shallow."""
    if len(terms) == 1:
        return terms[0]
    middle = len(terms) // 2
    return ["OR", _any_of(terms[:middle]), _any_of(terms[middle:])]


def _thread_ids(headers: str, own: str | None) -> list[str]:
    """The Message-IDs a conversation is searched by: those in `headers` (Message-ID,
    References, In-Reply-To) and the envelope's own, the last `_MAX_THREAD_IDS` of them. An id
    that is not printable ASCII cannot be searched for, so it is left out rather than failing
    the whole conversation."""
    found = _MESSAGE_ID.findall(headers) + ([] if own is None else _MESSAGE_ID.findall(own))
    return list(dict.fromkeys(found))[-_MAX_THREAD_IDS:]


def thread_page(connections: MailConnections, ref: str | MessageRef, *, limit: int = 30) -> Page:
    """The conversation `ref` belongs to, newest first: its folder plus Sent, matched by
    Message-ID, References and In-Reply-To (on Gmail: the Gmail thread, from All Mail)."""
    message = parse_ref(ref) if isinstance(ref, str) else ref
    gmail = connections.capabilities(message.account).gmail
    fields = f"BODY.PEEK[HEADER.FIELDS ({' '.join(h.upper() for h in _THREAD_HEADERS)})]"

    def find(context: MailboxContext) -> tuple[str, str | None, int | None]:
        check_fresh(message, context.uid_validity)
        items = ["ENVELOPE", fields, *(["X-GM-THRID"] if gmail else [])]
        data = context.client.fetch([message.uid], items).get(message.uid)
        if data is None:
            raise MailError("not_found")
        headers = next(
            (v for k, v in data.items() if isinstance(k, bytes) and k.startswith(b"BODY[HEADER")),
            b"",
        )
        own = getattr(data.get(b"ENVELOPE"), "message_id", None)
        thread_id = data.get(b"X-GM-THRID")
        return (
            text_value(headers if isinstance(headers, bytes) else b""),
            None if own is None else text_value(own),
            thread_id if isinstance(thread_id, int) else None,
        )

    headers, own, thread_id = connections.with_mailbox(message.account, message.folder, True, find)
    folders = connections.folders(message.account)
    criteria: Criteria
    if gmail and thread_id is not None:
        every = next((f.path for f in folders if f.role == "all"), message.folder)
        sources = [Source(message.account, every)]
        criteria = ["X-GM-THRID", thread_id]
    else:
        ids = _thread_ids(headers, own)
        if not ids:
            raise MailError("not_found", "This message has no Message-ID to follow.")
        criteria = [_any_of([["HEADER", name, i] for i in ids for name in _THREAD_HEADERS])]
        sent = next((f.path for f in folders if f.role == "sent"), None)
        paths = dict.fromkeys([message.folder, *([] if sent is None else [sent])])
        sources = [Source(message.account, path) for path in paths]
    return read_page(connections, sources, criteria, limit=limit, previews=True)
