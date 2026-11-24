"""The senders each user reads as text, without a mailbox: what a read prefers, kept per user,
and forgotten with the user. Over real mail: ``test_api_mail``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from sqlalchemy import delete

from mailroom.auth.users import create_user
from mailroom.mail import sender_views
from mailroom.models import User
from tests.conftest import PASSWORD

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom_core.mail import MessageView


def view(sender: str | None, *, has_html: bool = True) -> MessageView:
    addresses = [] if sender is None else [SimpleNamespace(address=sender)]
    return cast("MessageView", SimpleNamespace(sender=addresses, has_html=has_html))


def test_html_opens_unless_the_sender_is_read_as_text(db_session: Session, user: User) -> None:
    assert sender_views.preferred_view(db_session, user.id, view("News@Shop.example")) == "html"
    assert sender_views.sender(view(" News@Shop.example ")) == "news@shop.example"
    sender_views.remember(db_session, user.id, "news@shop.example")
    sender_views.remember(db_session, user.id, "news@shop.example")
    assert sender_views.preferred_view(db_session, user.id, view("News@Shop.example")) == "text"
    assert sender_views.preferred_view(db_session, user.id, view("other@shop.example")) == "html"
    # Without HTML there is only the text; without a From, nothing to remember.
    assert sender_views.preferred_view(
        db_session, user.id, view("a@b.example", has_html=False)
    ) == ("text")
    assert sender_views.preferred_view(db_session, user.id, view(None)) == "html"
    assert sender_views.sender(view(None)) is None
    assert sender_views.forget(db_session, user.id, "NEWS@shop.example")
    assert not sender_views.forget(db_session, user.id, "news@shop.example")
    assert sender_views.preferred_view(db_session, user.id, view("news@shop.example")) == "html"


def test_each_user_has_their_own(db_session: Session, user: User) -> None:
    other = create_user(db_session, "other", PASSWORD)
    sender_views.remember(db_session, user.id, "news@shop.example")
    assert [row.address for row in sender_views.senders(db_session, user.id)] == [
        "news@shop.example"
    ]
    assert sender_views.senders(db_session, other.id) == []
    assert sender_views.preferred_view(db_session, other.id, view("news@shop.example")) == "html"
    assert not sender_views.forget(db_session, other.id, "news@shop.example")
    assert len(sender_views.senders(db_session, user.id)) == 1


def test_a_users_go_with_them(db_session: Session, user: User) -> None:
    sender_views.remember(db_session, user.id, "news@shop.example")
    db_session.execute(delete(User).where(User.id == user.id))
    db_session.commit()
    assert sender_views.senders(db_session, user.id) == []
