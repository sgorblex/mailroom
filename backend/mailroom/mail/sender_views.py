"""The senders whose mail each user reads as text rather than HTML
(``app.sender_view_preference``). A message with HTML opens in it unless its From is remembered
here. It changes only how a message is shown, so any From counts: no sender authentication."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from mailroom.mail.remote_senders import from_address
from mailroom.models import SenderViewPreference

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom_core.mail import MessageView

View = Literal["html", "text"]
TEXT: Final = "text"


def sender(view: MessageView) -> str | None:
    """The message's first From address, lowercase; None without one."""
    return from_address(view)


def senders(db: Session, user_id: int) -> list[SenderViewPreference]:
    return list(
        db.scalars(
            select(SenderViewPreference)
            .where(SenderViewPreference.user_id == user_id)
            .order_by(SenderViewPreference.address)
        )
    )


def preferred_view(db: Session, user_id: int, view: MessageView) -> View:
    """``text`` for a message without HTML or from a sender the user reads as text; else
    ``html``."""
    address = sender(view)
    if not view.has_html:
        return "text"
    if address is None:
        return "html"
    remembered = db.scalar(
        select(SenderViewPreference.view).where(
            SenderViewPreference.user_id == user_id, SenderViewPreference.address == address
        )
    )
    return "text" if remembered == TEXT else "html"


def remember(db: Session, user_id: int, address: str) -> None:
    """Read ``address``'s mail as text; remembering it again keeps the first."""
    db.execute(
        insert(SenderViewPreference)
        .values(user_id=user_id, address=address, view=TEXT)
        .on_conflict_do_nothing(
            index_elements=[SenderViewPreference.user_id, SenderViewPreference.address]
        )
    )
    db.commit()


def forget(db: Session, user_id: int, address: str) -> bool:
    """Open ``address``'s mail in HTML again, compared lowercase; False when it was not
    remembered."""
    removed = db.scalar(
        delete(SenderViewPreference)
        .where(
            SenderViewPreference.user_id == user_id,
            SenderViewPreference.address == address.strip().lower(),
        )
        .returning(SenderViewPreference.address)
    )
    db.commit()
    return removed is not None
