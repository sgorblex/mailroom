"""Reading a view's messages (a saved view, or the built-in *All inboxes*) from the mail index
only (``docs/requirements/mail-client.md``, *Views*): its folders' rows merged newest first, on the
index's cursor. A folder that is not on the index, or not ready, is left out and named in the
first page's ``not_indexed``; nothing is read live, not even the mail an age limit leaves out.
The server is asked only what the index cannot say: on Gmail, which folder is All Mail and which
label a folder is, and for a search over every folder, the account's folders."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from mailroom.auth.users import time_zone_of
from mailroom.errors import mail_refusal
from mailroom.mail.connections import PasswordMissing
from mailroom.mail.index import reads as index_reads
from mailroom.mail.service._shared import _accounts, _call
from mailroom.mail.service.read import (
    ALL_FOLDERS,
    HOST_CODES,
    _password_missing_failure,
    _with_password_failures,
)
from mailroom_core import mail as core
from mailroom_core.mail import MailError, Page, PageFailure, SearchQuery, Source

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from mailroom.accounts.service import Via
    from mailroom.context import AppContext
    from mailroom.mail.index.reads import IndexSource
    from mailroom_core.mail import Folder, MailConnections

_NOT_A_CURSOR = "Not a cursor; use the one from the previous page."
TEXT_REFUSED: Final = "A view reads the mail index, which cannot search a message's text."


@dataclass(frozen=True, slots=True)
class ViewPage:
    """A page of a view; ``not_indexed``: its folders left out (``*``: an account's every
    folder), on the first page only."""

    page: Page
    not_indexed: tuple[Source, ...]


def view_messages(
    ctx: AppContext,
    user_id: int,
    sources: Sequence[Source],
    query: SearchQuery | None,
    *,
    limit: int = 25,
    cursor: str | None = None,
    group: bool = False,
    via: Via = "web",
) -> ViewPage:
    """The messages of ``sources`` (a folder ``ALL_FOLDERS``: every folder of its account, as a
    search reads them) matching ``query``, newest first, from the index only. Sources of an
    account that is not the user's are left out. A source the server must be asked about and
    cannot be is a failure of the page; a cursor whose folder changed is ``stale_reference``.
    ``group``: by conversation (a ``ConversationPage``)."""
    if not index_reads.readable(query):
        raise mail_refusal("invalid_argument", TEXT_REFUSED)
    accounts = _accounts(ctx, user_id)
    mine = [source for source in sources if accounts.has(source.account)]
    resumed = None
    if cursor is not None:
        try:
            resumed = index_reads.decode_cursor(cursor)
        except MailError as error:
            raise mail_refusal(error.code, error.message) from None
        if resumed is None or resumed.mode != index_reads.INDEX:
            raise mail_refusal("invalid_argument", _NOT_A_CURSOR)
    used = list(dict.fromkeys(source.account for source in mine))

    def read(connections: MailConnections) -> ViewPage:
        with ctx.session_maker() as db:
            if resumed is not None:
                picked = index_reads.resumed_sources(db, user_id, resumed, accounts.ids())
                after = resumed.key
                failures = resumed.failures(HOST_CODES)
                not_indexed: list[Source] = []
            else:
                picked, failures, not_indexed = view_sources(db, connections, user_id, mine)
                after = None
            rows = index_reads.read_page_rows(
                db,
                user_id,
                picked,
                query,
                zone=time_zone_of(db, user_id),
                limit=limit + 1,
                after=after,
                group=group,
                listing=connections.folders,
            )
        kept = rows[:limit]
        conversations = [c for _, _, c in kept if c is not None] if group else None
        following = None
        if len(rows) > limit:
            key = rows[limit - 1][1]
            following = index_reads.IndexCursor.index(picked, key, failures, HOST_CODES).encode()
        page = index_reads.page_of(
            [summary for summary, _, _ in kept], conversations, following, failures
        )
        return ViewPage(page, tuple(not_indexed))

    if not used and resumed is None:
        return ViewPage(index_reads.page_of((), [] if group else None, None, ()), ())
    viewed = _call(ctx, user_id, accounts, "mail_view_listed", via, "*", read, on=used)
    return replace(viewed, page=_with_password_failures(ctx, user_id, viewed.page))


def view_sources(
    db: Session, connections: MailConnections, user_id: int, sources: Sequence[Source]
) -> tuple[list[IndexSource], list[PageFailure], list[Source]]:
    """``sources`` as the index reads them, one by one, merged per indexed folder; with the
    failures of those the server could not answer for, and the sources left out."""
    ready = index_reads.ready_rows(db, user_id, [source.account for source in sources])
    indexed = {account for account, _ in ready}
    listed: dict[int, list[Folder]] = {}

    def listing(account: int) -> list[Folder]:
        if account not in listed:
            listed[account] = connections.folders(account)
        return listed[account]

    def gmail(account: int) -> bool:
        return connections.capabilities(account).gmail

    failures: list[PageFailure] = []
    not_indexed: list[Source] = []
    found: list[IndexSource] = []
    for source in dict.fromkeys(sources):
        try:
            if source.folder == ALL_FOLDERS and source.account not in indexed:
                not_indexed.append(source)
                continue
            wanted = [source]
            if source.folder == ALL_FOLDERS:
                paths = core.searchable_folders(connections, source.account)
                wanted = index_reads.every_folder_sources(
                    db,
                    user_id,
                    [Source(source.account, path) for path in paths],
                    indexed,
                    gmail=gmail,
                    listing=listing,
                )
            for one in wanted:
                picked = index_reads.index_sources(
                    db, user_id, [one], gmail=gmail, listing=listing, rows=ready
                )
                if picked is None:
                    not_indexed.append(one)
                else:
                    found.extend(picked)
        except MailError as error:
            failures.append(PageFailure.of(source.account, source.folder, error))
        except PasswordMissing:
            failures.append(_password_missing_failure(source.account, source.folder))
    return _merged(found), failures, not_indexed


def _merged(found: Sequence[IndexSource]) -> list[IndexSource]:
    """One source per indexed folder: on Gmail, the labels of each folder read from All Mail
    together, so that a message carrying two of them comes once."""
    out: dict[int, IndexSource] = {}
    for source in found:
        held = out.get(source.folder_id)
        if held is None:
            out[source.folder_id] = source
            continue
        labels = (
            None
            if held.labels is None or source.labels is None
            else tuple(sorted({*held.labels, *source.labels}))
        )
        live = tuple(dict.fromkeys((*held.live, *source.live)))
        out[source.folder_id] = replace(held, labels=labels, live=live)
    return list(out.values())
