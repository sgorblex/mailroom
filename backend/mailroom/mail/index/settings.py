"""Which folders the mail index keeps, and how far back.

Each folder of an account gets its row (``app.mail_index_folder``) the first time the index sees
it, switched on but for the Junk role's and, off Gmail, a virtual folder's (``\\All`` or
``\\Flagged``), which only repeats mail kept elsewhere. On Gmail (``X-GM-EXT-1``, never by host
name) only All Mail is indexed, with each message's labels: INBOX and the label folders are read
from it, and Spam and Trash, which All Mail leaves out, stay live. Switching a folder off deletes
its messages' rows; its reads go live as before the index.

The age limit is the user's ``index_max_age_days`` capped by ``MAILROOM_INDEX_MAX_AGE_DAYS``: the
smaller of the two, none when neither is set.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from mailroom.auth.users import INDEX_MAX_AGE_DAYS
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail import service
from mailroom.models import MailIndexFolder, MailIndexMessage
from mailroom_core import mail as core
from mailroom_core.mail import MESSAGES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session

    from mailroom.context import AppContext
    from mailroom_core.mail import Folder, MailConnections

logger = logging.getLogger(__name__)

MAX_AGE_ENV: Final = "MAILROOM_INDEX_MAX_AGE_DAYS"
GMAIL_LABEL: Final = "index_gmail_label"
_GMAIL_LABEL_TEXT: Final = (
    "On Gmail only All Mail is indexed: the other folders are read from it, or live."
)

type IndexedAs = Literal["folder", "label", "live"]


class IndexFolderStatus(BaseModel):
    path: str
    delimiter: str | None
    """The server's hierarchy delimiter for the path, None when it gives none."""
    role: str | None
    indexed_as: IndexedAs
    """``folder``: indexed on its own; ``label``: a Gmail folder read from All Mail's rows by its
    label, which ``enabled`` and the sync fields are All Mail's; ``live``: never indexed."""
    enabled: bool
    total: int | None
    """The server's count (STATUS), None when it failed."""
    state: str | None = None
    """``pending``, ``syncing``, ``ready`` or ``failed``; this and the rest None while the folder
    is off the index."""
    error: str | None = None
    """The ``MailError`` code that failed the last sync."""
    indexed: int | None = None
    """Messages indexed (None for a label); with ``total``, the progress of a first sync."""
    unread: int | None = None
    oldest_indexed: date | None = None
    """The age limit's day the last sync kept to; None: every message."""
    last_synced_at: datetime | None = None


class IndexStatus(BaseModel):
    account_id: int
    gmail: bool
    max_age_days: int | None
    """The age limit in force: the user's, capped by the instance's."""
    max_age_cap_days: int | None
    """The instance's cap (``MAILROOM_INDEX_MAX_AGE_DAYS``), None when unlimited."""
    folders: list[IndexFolderStatus]


class IndexFolderSwitched(BaseModel):
    account_id: int
    folder: str
    enabled: bool


def max_age_cap() -> int | None:
    """``MAILROOM_INDEX_MAX_AGE_DAYS``, clamped to 1-``INDEX_MAX_AGE_DAYS``; None when unset or
    not a whole number: no cap."""
    raw = os.environ.get(MAX_AGE_ENV, "").strip()
    try:
        days = int(raw)
    except ValueError:
        return None
    return min(max(days, 1), INDEX_MAX_AGE_DAYS)


def effective_max_age(user_days: int | None) -> int | None:
    """The age limit in force for a user whose own is ``user_days``."""
    limits = [days for days in (user_days, max_age_cap()) if days is not None]
    return min(limits) if limits else None


def _indexed_as(folder: Folder, gmail: bool, all_mail: bool) -> IndexedAs:
    if not gmail or folder.role == "all":
        return "folder"
    if folder.role in ("junk", "trash") or not all_mail:
        return "live"
    return "label"


def _kinds(gmail: bool, folders: Sequence[Folder]) -> list[tuple[Folder, IndexedAs]]:
    all_mail = any(folder.role == "all" for folder in folders)
    return [(folder, _indexed_as(folder, gmail, all_mail)) for folder in folders]


_VIRTUAL_FLAGS: Final = frozenset({"\\all", "\\flagged"})


def virtual(folder: Folder) -> bool:
    """Whether ``folder`` is virtual, off Gmail: All Mail or Flagged (special-use or role), whose
    mail is another folder's too."""
    return folder.role in ("all", "flagged") or any(
        flag.lower() in _VIRTUAL_FLAGS for flag in folder.flags
    )


def _on_by_default(folder: Folder, gmail: bool) -> bool:
    """Junk is off; so, off Gmail, a virtual folder."""
    return folder.role != "junk" and (gmail or not virtual(folder))


def _first_sight(
    db: Session,
    user_id: int,
    account_id: int,
    gmail: bool,
    kinds: Sequence[tuple[Folder, IndexedAs]],
) -> None:
    """A row for each indexable folder that has none yet, switched on as ``_on_by_default``."""
    fresh = [
        {
            "user_id": user_id,
            "account_id": account_id,
            "path": folder.path,
            "enabled": _on_by_default(folder, gmail),
        }
        for folder, kind in kinds
        if kind == "folder"
    ]
    if fresh:
        db.execute(insert(MailIndexFolder).values(fresh).on_conflict_do_nothing())
    db.commit()


def _inbox_subfolders(folders: Sequence[Folder]) -> list[str]:
    """The paths of INBOX's subfolders at any depth, as a list of INBOX with its subfolders reads
    them, but for the virtual ones, which repeat mail kept elsewhere."""
    inbox = next((folder for folder in folders if folder.path.upper() == "INBOX"), None)
    under = [] if inbox is None else core.listed_subfolders(folders, inbox)
    return [folder.path for folder in under if not virtual(folder)]


def discover_folders(
    db: Session, connections: MailConnections, user_id: int, account_id: int
) -> int:
    """Bring the account's rows in step with its folder listing (without STATUS): a row for each
    indexable folder first seen, as ``index_status`` gives it, and none for a folder no longer
    listed (deleted, or renamed: its new name is a folder first seen), its messages' rows going
    with it; and which of them are INBOX's subfolders. Commits; returns how many rows went."""
    gmail = connections.capabilities(account_id).gmail
    folders = connections.folders(account_id)
    listed = [folder for folder in folders if folder.selectable]
    kinds = _kinds(gmail, listed)
    _first_sight(db, user_id, account_id, gmail, kinds)
    indexable = [folder.path for folder, kind in kinds if kind == "folder"]
    gone = db.execute(
        delete(MailIndexFolder).where(
            MailIndexFolder.user_id == user_id,
            MailIndexFolder.account_id == account_id,
            MailIndexFolder.path.not_in(indexable),
        )
    )
    inside = MailIndexFolder.path.in_([] if gmail else _inbox_subfolders(folders))
    db.execute(
        update(MailIndexFolder)
        .where(
            MailIndexFolder.user_id == user_id,
            MailIndexFolder.account_id == account_id,
            MailIndexFolder.inbox_subfolder.is_distinct_from(inside),
        )
        .values(inbox_subfolder=inside)
    )
    db.commit()
    return cast("CursorResult[Any]", gone).rowcount


def _rows(db: Session, user_id: int, account_id: int) -> dict[str, MailIndexFolder]:
    query = select(MailIndexFolder).where(
        MailIndexFolder.user_id == user_id, MailIndexFolder.account_id == account_id
    )
    return {row.path: row for row in db.scalars(query)}


def _status(
    folder: Folder, kind: IndexedAs, row: MailIndexFolder | None, all_mail: MailIndexFolder | None
) -> IndexFolderStatus:
    source = row if kind == "folder" else all_mail if kind == "label" else None
    shown = IndexFolderStatus(
        path=folder.path,
        delimiter=folder.delimiter,
        role=folder.role,
        indexed_as=kind,
        enabled=False,
        total=folder.messages,
    )
    if source is None or not source.enabled:
        return shown
    counted = kind == "folder"
    return shown.model_copy(
        update={
            "enabled": True,
            "state": source.state,
            "error": source.error,
            "indexed": source.message_count if counted else None,
            "unread": source.unread_count if counted else None,
            "oldest_indexed": source.oldest_indexed,
            "last_synced_at": source.last_synced_at,
        }
    )


def index_status(
    ctx: AppContext,
    db: Session,
    user_id: int,
    account_id: int,
    user_days: int | None,
    *,
    waiting_only: bool = False,
) -> IndexStatus:
    """The account's selectable folders as the index keeps them, in the listing's order; each one
    seen for the first time gets its row. ``waiting_only``: for a page that only follows a first
    sync, the server's count (STATUS) of the folders waiting for or in their sync only, and no
    row written."""
    if waiting_only:
        rows = _rows(db, user_id, account_id)
        db.commit()
        gmail, folders = service.indexable_folders(
            ctx, user_id, account_id, counted=lambda folder: _waiting(rows.get(folder.path))
        )
        kinds = _kinds(gmail, folders)
    else:
        gmail, folders = service.indexable_folders(ctx, user_id, account_id)
        kinds = _kinds(gmail, folders)
        _first_sight(db, user_id, account_id, gmail, kinds)
        rows = _rows(db, user_id, account_id)
    all_mail = next(
        (rows.get(folder.path) for folder, _ in kinds if gmail and folder.role == "all"), None
    )
    return IndexStatus(
        account_id=account_id,
        gmail=gmail,
        max_age_days=effective_max_age(user_days),
        max_age_cap_days=max_age_cap(),
        folders=[_status(folder, kind, rows.get(folder.path), all_mail) for folder, kind in kinds],
    )


def _waiting(row: MailIndexFolder | None) -> bool:
    return row is not None and row.enabled and row.state in ("pending", "syncing")


def _row(db: Session, user_id: int, account_id: int, path: str) -> MailIndexFolder | None:
    query = (
        select(MailIndexFolder)
        .where(
            MailIndexFolder.user_id == user_id,
            MailIndexFolder.account_id == account_id,
            MailIndexFolder.path == path,
        )
        .with_for_update()
    )
    return db.scalar(query)


def set_folder_enabled(
    ctx: AppContext, db: Session, user_id: int, account_id: int, path: str, enabled: bool
) -> IndexFolderSwitched:
    """Switch a folder on or off the index. Off deletes its messages' rows and forgets its sync;
    on leaves it to be built. A folder the index has not seen yet is looked up on the server
    first: one it does not list is ``mailbox_missing``, and on Gmail one but All Mail is
    ``index_gmail_label``."""
    if path.upper() == "INBOX":
        path = "INBOX"
    row = _row(db, user_id, account_id, path)
    if row is None:
        db.rollback()
        gmail, folders = service.indexable_folders(ctx, user_id, account_id)
        kinds = _kinds(gmail, folders)
        _first_sight(db, user_id, account_id, gmail, kinds)
        row = _row(db, user_id, account_id, path)
        if row is None:
            db.rollback()
            if any(folder.path == path for folder, _ in kinds):
                raise Refusal("conflict", GMAIL_LABEL, _GMAIL_LABEL_TEXT)
            raise mail_refusal("mailbox_missing", MESSAGES["mailbox_missing"])
    removed = 0
    if row.enabled != enabled:
        if not enabled:
            deleted = db.execute(
                delete(MailIndexMessage).where(
                    MailIndexMessage.folder_id == row.id, MailIndexMessage.user_id == user_id
                )
            )
            removed = cast("CursorResult[Any]", deleted).rowcount
            row.uid_validity = row.highest_modseq = row.uid_next = None
            row.oldest_indexed = row.error = row.last_synced_at = None
            row.message_count = row.unread_count = 0
        row.enabled = enabled
        row.state = "pending"
    db.commit()
    logger.info(
        "mail_index_folder_switched",
        extra={
            "event": "mail_index_folder_switched",
            "user_id": user_id,
            "account_id": account_id,
            "enabled": enabled,
            "removed": removed,
        },
    )
    return IndexFolderSwitched(account_id=account_id, folder=path, enabled=enabled)
