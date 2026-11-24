"""The senders whose remote content each user shows without asking (``app.remote_content_sender``).
A sender is remembered by its From address, and counts only for a message whose DMARC passed at
the account's own authentication server for its one From: anyone can write a From."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from mailroom.models import RemoteContentSender

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom_core.mail import MessageView

MAX_ADDRESS: Final = 998
"""The longest address remembered: a header line's length."""


def from_address(view: MessageView) -> str | None:
    """The message's first From address, lowercase, as a sender is remembered by; None without
    one or for one longer than ``MAX_ADDRESS``."""
    if not view.sender:
        return None
    address = (view.sender[0].address or "").strip().lower()
    return address if 0 < len(address) <= MAX_ADDRESS else None


def verified_sender(view: MessageView) -> str | None:
    """The message's one From address, lowercase, when DMARC passed for it at the account's
    authentication server (``dmarc_pass``); None otherwise."""
    if not view.authentication.dmarc_pass or len(view.sender) != 1:
        return None
    return from_address(view)


def senders(db: Session, user_id: int) -> list[RemoteContentSender]:
    return list(
        db.scalars(
            select(RemoteContentSender)
            .where(RemoteContentSender.user_id == user_id)
            .order_by(RemoteContentSender.address)
        )
    )


def remembered(db: Session, user_id: int, address: str) -> bool:
    return (
        db.scalar(
            select(RemoteContentSender.address).where(
                RemoteContentSender.user_id == user_id, RemoteContentSender.address == address
            )
        )
        is not None
    )


def remember(db: Session, user_id: int, address: str) -> RemoteContentSender:
    """Remember ``address`` (from ``verified_sender``); remembering it again keeps the first. One
    statement writes and reads the row, so a removal in between cannot make it vanish."""
    statement = insert(RemoteContentSender).values(user_id=user_id, address=address)
    row = db.scalars(
        statement.on_conflict_do_update(
            index_elements=[RemoteContentSender.user_id, RemoteContentSender.address],
            set_={"address": statement.excluded.address},
        ).returning(RemoteContentSender),
        execution_options={"populate_existing": True},
    ).one()
    db.commit()
    return row


def forget(db: Session, user_id: int, address: str) -> bool:
    """Forget ``address``, compared lowercase; False when it was not remembered."""
    removed = db.scalar(
        delete(RemoteContentSender)
        .where(
            RemoteContentSender.user_id == user_id,
            RemoteContentSender.address == address.strip().lower(),
        )
        .returning(RemoteContentSender.address)
    )
    db.commit()
    return removed is not None
