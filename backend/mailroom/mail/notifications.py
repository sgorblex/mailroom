"""The folders whose new mail each user's browser shows as desktop notifications, per account
(``app.mail_notification_folders``): INBOX alone until the user chooses. The browser decides
from the live updates' ``new_mail`` events; the server only keeps the choice."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from mailroom.filters.folders import segments, under
from mailroom.models import MailNotificationFolders

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from sqlalchemy.orm import Session

DEFAULT_FOLDERS: Final = ("INBOX",)
MAX_FOLDERS: Final = 200
"""Of one account."""


def _path(folder: str) -> str:
    return "INBOX" if folder.upper() == "INBOX" else folder


def folders_by_account(
    db: Session, user_id: int, account_ids: Iterable[int]
) -> dict[int, list[str]]:
    """Each account's chosen folders, INBOX alone for one the user never set."""
    stored = {
        row.account_id: row.folders
        for row in db.scalars(
            select(MailNotificationFolders).where(MailNotificationFolders.user_id == user_id)
        )
    }
    return {account_id: list(stored.get(account_id, DEFAULT_FOLDERS)) for account_id in account_ids}


def set_folders(db: Session, user_id: int, account_id: int, folders: Sequence[str]) -> list[str]:
    """Keep ``folders`` (INBOX in capitals, each once, in the order given) as the account's
    choice; the caller checked the account is the user's."""
    chosen = list(dict.fromkeys(_path(folder) for folder in folders))
    statement = insert(MailNotificationFolders).values(
        user_id=user_id, account_id=account_id, folders=chosen
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[MailNotificationFolders.user_id, MailNotificationFolders.account_id],
            set_={"folders": statement.excluded.folders},
        )
    )
    db.commit()
    return chosen


def _rewrite(
    db: Session,
    user_id: int,
    account_id: int,
    folder: str,
    delimiter: str | None,
    change: Callable[[list[str]], str | None],
) -> None:
    """Passes each chosen path that is ``folder`` or under it to ``change``: its new path, or
    None to drop it; committed."""
    row = db.scalars(
        select(MailNotificationFolders)
        .where(
            MailNotificationFolders.user_id == user_id,
            MailNotificationFolders.account_id == account_id,
        )
        .with_for_update()
    ).one_or_none()
    if row is None:
        return
    old = segments(folder, delimiter)
    chosen: list[str] = []
    for path in row.folders:
        split = segments(path, delimiter)
        kept = change(split[len(old) :]) if under(split, old) else path
        if kept is not None:
            chosen.append(kept)
    row.folders = list(dict.fromkeys(chosen))
    db.commit()


def rename_folder(
    db: Session, user_id: int, account_id: int, old: str, new: str, delimiter: str | None
) -> None:
    """The account's choice follows the folder ``old``, and every folder under it, renamed
    ``new``."""
    joiner = delimiter or ""
    _rewrite(
        db,
        user_id,
        account_id,
        old,
        delimiter,
        lambda rest: joiner.join([*segments(new, delimiter), *rest]),
    )


def forget_folder(
    db: Session, user_id: int, account_id: int, folder: str, delimiter: str | None
) -> None:
    """Drops the deleted ``folder``, and every folder under it, from the account's choice."""
    _rewrite(db, user_id, account_id, folder, delimiter, lambda _rest: None)
