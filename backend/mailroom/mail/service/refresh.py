"""Refreshing what a list shows (``docs/requirements/mail-client.md``, *Index*, *Refresh*): the
indexed folders it reads synced by the worker ahead of every other job of their mailboxes
(``mailroom.worker.tasks.refresh_folders``). A folder off the index has nothing to sync: reading
its list again reads the server."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from functools import partial
from typing import TYPE_CHECKING, Final

from sqlalchemy import select

from mailroom.auth.throttle import RateLimit
from mailroom.errors import Refusal
from mailroom.mail.index.writes import labelled_folders
from mailroom.mail.service._shared import _accounts, _call, _usable, unknown_account
from mailroom.mail.service.read import ALL_ACCOUNTS, ALL_FOLDERS, AllAccounts, check_search
from mailroom.models import MailIndexFolder
from mailroom_core import mail as core
from mailroom_core.mail import SearchQuery, Source

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom_core.mail import MailConnections

logger = logging.getLogger(__name__)

REFRESHES_PER_MINUTE: Final = 12
"""Of one user, in one API process."""
_TOO_MANY: Final = "Too many refreshes in a short time; try again in a minute."


def refresh_limit() -> RateLimit:
    return RateLimit(REFRESHES_PER_MINUTE, 60.0)


def admit_refresh(limit: RateLimit, user_id: int) -> None:
    """``busy`` past the user's ``limit``; a refresh let through counts."""
    if not limit.allow(str(user_id)):
        raise Refusal("busy", "refresh_limited", _TOO_MANY)


def listed_sources(
    ctx: AppContext,
    user_id: int,
    account: int | AllAccounts,
    folder: str,
    *,
    subfolders: bool,
) -> list[Source]:
    """What a list or a search of ``folder`` (``ALL_FOLDERS``: every one) in ``account`` reads.
    The server is asked for nothing but a folder's subfolders, of one account."""
    check_search(folder, SearchQuery(), subfolders=subfolders)
    accounts = _accounts(ctx, user_id)
    if account == ALL_ACCOUNTS:
        wanted = accounts.ids()
    elif accounts.has(account):
        wanted = [account]
    else:
        raise unknown_account()
    if not subfolders:
        return [Source(account_id, folder) for account_id in wanted]
    sources: list[Source] = []
    for account_id in wanted:
        _usable(ctx, user_id, accounts, account_id)
        read = partial(_paths, account_id=account_id, folder=folder)
        paths = _call(ctx, user_id, accounts, "mail_refresh_folders", "web", account_id, read)
        sources.extend(Source(account_id, path) for path in paths)
    return sources


def _paths(connections: MailConnections, *, account_id: int, folder: str) -> list[str]:
    return core.read_folders(connections, account_id, folder, subfolders=True)


def refresh(ctx: AppContext, db: Session, user_id: int, sources: Sequence[Source]) -> str | None:
    """Queue the syncs of the indexed folders ``sources`` read, as one refresh, and commit: its id,
    the same for the same folders, so that asking again while it waits goes on with it; None when
    none of them is on the index. Sources of an account that is not the user's are left out."""
    # Imported here: the worker's tasks import the index's sync, which imports the mail service.
    from mailroom.worker.tasks import queue_refresh

    reached = set(_accounts(ctx, user_id).ids())
    folders = _indexed(db, user_id, [s for s in sources if s.account in reached])
    refresh_id = _refresh_id(folders) if folders else None
    if refresh_id is not None:
        for account_id, folder_ids in sorted(folders.items()):
            queue_refresh(db, user_id, account_id, folder_ids, refresh_id=refresh_id)
    db.commit()
    logger.info(
        "mail_refresh_asked",
        extra={
            "event": "mail_refresh_asked",
            "user_id": user_id,
            "accounts": len(folders),
            "folders": sum(map(len, folders.values())),
        },
    )
    return refresh_id


def _refresh_id(folders: Mapping[int, Sequence[int]]) -> str:
    ids = sorted(folder_id for of_account in folders.values() for folder_id in of_account)
    return str(uuid.uuid5(uuid.NAMESPACE_OID, "refresh:" + ",".join(map(str, ids))))


def refresh_running(db: Session, user_id: int, refresh_id: str) -> bool:
    """Whether the user's refresh still has folders to sync: False too for one that never was."""
    from mailroom.worker import tasks

    return tasks.refresh_running(db, user_id, refresh_id)


def _indexed(db: Session, user_id: int, sources: Sequence[Source]) -> dict[int, list[int]]:
    """The ids of the folders on the index that ``sources`` read, by account, INBOX first: each
    its own row, or on Gmail All Mail's for a folder without one (a label's)."""
    accounts = {source.account for source in sources}
    query = select(MailIndexFolder).where(
        MailIndexFolder.user_id == user_id, MailIndexFolder.account_id.in_(sorted(accounts))
    )
    rows = {(row.account_id, row.path): row for row in db.scalars(query)}
    picked: dict[int, MailIndexFolder] = {}
    labelled: set[int] = set()
    for source in sources:
        if source.folder == ALL_FOLDERS:
            picked.update(
                (row.id, row)
                for (account_id, _), row in rows.items()
                if account_id == source.account
            )
            continue
        path = "INBOX" if source.folder.upper() == "INBOX" else source.folder
        row = rows.get((source.account, path))
        if row is None:
            labelled.add(source.account)
        else:
            picked[row.id] = row
    if labelled:
        picked.update((row.id, row) for row in labelled_folders(db, user_id, labelled))
    folders: dict[int, list[int]] = defaultdict(list)
    for row in sorted(picked.values(), key=lambda row: (row.path != "INBOX", row.id)):
        if row.enabled:
            folders[row.account_id].append(row.id)
    return dict(folders)
