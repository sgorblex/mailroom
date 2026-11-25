"""What each of a user's accounts keeps the Handled mark as (``app.account_handled``): the star
(no row), an IMAP keyword or a Gmail label. mailroom-core's connections look it up as they read,
search and mark (``user_connections``); the mail index, when it answers in their place."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from mailroom.models import AccountHandled
from mailroom_core.mail import STAR, HandledMark, parse_handled

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.orm import Session


def handled_marks(db: Session | Connection, user_id: int) -> dict[int, HandledMark]:
    """The mark of each of the user's accounts that keeps Handled as something but the star."""
    rows = db.execute(
        select(AccountHandled.account_id, AccountHandled.mark).where(
            AccountHandled.user_id == user_id
        )
    )
    return {account_id: parse_handled(mark) for account_id, mark in rows}


def handled_mark(db: Session | Connection, user_id: int, account_id: int) -> HandledMark:
    """What the account keeps Handled as."""
    mark = db.scalar(
        select(AccountHandled.mark).where(
            AccountHandled.user_id == user_id, AccountHandled.account_id == account_id
        )
    )
    return STAR if mark is None else parse_handled(mark)


def handled_folder(
    db: Session | Connection, user_id: int, account_id: int, folder: str, delimiter: str | None
) -> str | None:
    """The label the account keeps Handled as, when ``folder`` is its folder or holds it."""
    mark = handled_mark(db, user_id, account_id)
    if mark.kind != "label":
        return None
    within = bool(delimiter) and mark.name.startswith(f"{folder}{delimiter}")
    return mark.name if within or mark.name == folder else None


def renamed_handled(
    db: Session | Connection,
    user_id: int,
    account_id: int,
    old: str,
    new: str,
    delimiter: str | None,
) -> HandledMark | None:
    """The account's mark once the folder ``old`` is ``new``; None when it is not a label there.
    ``MailError`` (``invalid_argument``) when Handled cannot be kept as a label so named."""
    label = handled_folder(db, user_id, account_id, old, delimiter)
    return None if label is None else parse_handled(f"label:{new}{label[len(old) :]}")


def store_handled_mark(db: Session, user_id: int, account_id: int, mark: HandledMark) -> None:
    """Keep ``mark`` for the account; the star is no row. The caller commits."""
    if mark == STAR:
        db.execute(
            delete(AccountHandled).where(
                AccountHandled.user_id == user_id, AccountHandled.account_id == account_id
            )
        )
        return
    statement = insert(AccountHandled).values(
        user_id=user_id, account_id=account_id, mark=str(mark)
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[AccountHandled.user_id, AccountHandled.account_id],
            set_={"mark": statement.excluded.mark},
        )
    )
