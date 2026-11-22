"""The addresses a recipient field suggests (``docs/requirements/mail-client.md``, *Compose*), read
from the index's rows: those the user sent to first, then the people who wrote to them directly,
never list mail, no-reply addresses, the user's own identities or another user's rows; ranked by
count and recency, by a prefix of the address, the name or a word of it."""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from mailroom.auth.users import create_user
from mailroom.errors import mail_refusal
from mailroom.mail.index import addresses
from mailroom.mail.service import addresses as service_addresses
from mailroom_core.mail import MailError
from tests.conftest import PASSWORD
from tests.test_api_accounts import added, new_account
from tests.test_mail_index_reads import ready
from tests.test_mail_index_rows import message

if TYPE_CHECKING:
    import pytest
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import MailIndexMessage, User

ME = "me@example.org"
URL = "/api/v1/mail/addresses"
DAY = datetime(2026, 11, 20, tzinfo=UTC)


def person(address: str, name: str = "") -> dict[str, str]:
    return {"name": name, "address": address}


def sent(
    folder: int, user: User, uid: int, *to: dict[str, str], **changes: Any
) -> MailIndexMessage:
    return message(
        folder,
        user.id,
        uid,
        sender=[person(ME, "Me")],
        sender_text=f"Me {ME}",
        recipients={"to": list(to), "cc": changes.pop("cc", [])},
        **changes,
    )


def received(
    folder: int, user: User, uid: int, sender: dict[str, str], **changes: Any
) -> MailIndexMessage:
    return message(
        folder,
        user.id,
        uid,
        sender=[sender],
        sender_text=f"{sender['name']} {sender['address']}".strip(),
        recipients=changes.pop("recipients", {"to": [person(ME)], "cc": []}),
        **changes,
    )


def suggest(db: Session, user: User, prefix: str, **folders: Any) -> list[tuple[str, str, bool]]:
    found = addresses.suggest(db, user.id, prefix, identities={ME}, **folders)
    return [(one.address, one.name, one.sent) for one in found]


def test_sent_to_first_then_direct_senders(db_session: Session, user: User) -> None:
    inbox = ready(db_session, user, 1, "INBOX")
    sent_box = ready(db_session, user, 1, "Sent")
    junk = ready(db_session, user, 1, "Junk")
    all_mail = ready(db_session, user, 2, "[Gmail]/All Mail")
    earlier = DAY - timedelta(days=3)
    db_session.add_all(
        [
            # Sent to: Ann twice (To, then Cc), Andy once but lately, Gmail's Sent label too.
            sent(sent_box, user, 1, person("ann@example.org", "Ann Lee"), received=earlier),
            sent(sent_box, user, 2, person("x@else.org"), cc=[person("ANN@example.org", "Ann")]),
            sent(sent_box, user, 3, person("andy@example.org"), received=DAY + timedelta(1)),
            sent(all_mail, user, 1, person("al@gmail.example", "Al"), labels=["\\Sent"]),
            # Wrote to the user directly; Anna three times, Ana once.
            *(
                received(inbox, user, uid, person("anna@example.org", "Anna B"))
                for uid in (1, 2, 3)
            ),
            received(inbox, user, 4, person("ana@example.org")),
            # Never: what the user sent from elsewhere, list mail, no-reply, Bcc'd bulk mail,
            # Junk's, and Ann as a sender (she is already among the sent-to).
            received(inbox, user, 5, person(ME)),
            received(inbox, user, 6, person("announce@example.org"), list_id="news.example.org"),
            received(inbox, user, 7, person("noreply@example.org")),
            received(inbox, user, 8, person("alerts@noreply.example.org")),
            received(inbox, user, 9, person("do-not-reply@example.org")),
            received(inbox, user, 10, person("ads@example.org"), recipients={"to": [], "cc": []}),
            received(junk, user, 1, person("anyone@spam.example")),
            received(inbox, user, 11, person("ann@example.org", "Ann L")),
            # A Gmail row labelled Inbox is a sender's, not a sent one.
            received(all_mail, user, 2, person("alma@example.org"), labels=["\\Inbox"]),
        ]
    )
    db_session.commit()
    folders = {
        "folders": [inbox, sent_box, junk, all_mail],
        "sent": [sent_box],
        "inbound": [inbox, all_mail],
    }

    assert suggest(db_session, user, "a", **folders) == [
        ("ann@example.org", "Ann", True),
        ("andy@example.org", "", True),
        ("al@gmail.example", "Al", True),
        ("anna@example.org", "Anna B", False),
        ("alma@example.org", "", False),
        ("ana@example.org", "", False),
    ]
    assert suggest(db_session, user, "AN", **folders, limit=2) == [
        ("ann@example.org", "Ann", True),
        ("andy@example.org", "", True),
    ]
    # A prefix of the name, or of a word of it; never the middle of one.
    assert suggest(db_session, user, "lee", **folders) == [("ann@example.org", "Ann", True)]
    assert suggest(db_session, user, "b", **folders) == [("anna@example.org", "Anna B", False)]
    assert suggest(db_session, user, "nn", **folders) == []
    assert suggest(db_session, user, "%", **folders) == []
    assert suggest(db_session, user, "_", **folders) == []
    assert suggest(db_session, user, "me", **folders) == []
    assert suggest(db_session, user, "  ", **folders) == []
    # Without the listing, a folder's rows are no one's Sent: its recipients are not suggested.
    unlisted = folders | {"sent": []}
    assert ("andy@example.org", "", True) not in suggest(db_session, user, "a", **unlisted)


def test_a_name_too_long_for_a_draft_is_suggested_as_none(db_session: Session, user: User) -> None:
    inbox = ready(db_session, user, 1, "INBOX")
    db_session.add_all(
        [
            received(inbox, user, 1, person("long@example.org", "L" * (addresses.MAX_NAME + 1))),
            received(inbox, user, 2, person("lena@example.org", "L" * addresses.MAX_NAME)),
        ]
    )
    db_session.commit()
    found = suggest(db_session, user, "l", folders=[inbox], sent=[], inbound=[inbox])
    assert sorted(found) == [
        ("lena@example.org", "L" * addresses.MAX_NAME, False),
        ("long@example.org", "", False),
    ]


class _Listing:
    """Connections whose folder lists name each account's Sent, account 2's failing."""

    def __init__(self) -> None:
        self.asked: list[int] = []

    def folders(self, account: int) -> list[SimpleNamespace]:
        self.asked.append(account)
        if account == 2:
            raise MailError("unreachable")
        return [SimpleNamespace(path="Sent", role="sent"), SimpleNamespace(path="Mine", role=None)]


def test_folder_roles_are_listed_once_and_kept_while_typing(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    listing = _Listing()

    def run(*args: Any, **_: Any) -> None:
        args[5](listing)

    monkeypatch.setattr(service_addresses, "_run", run)
    accounts = cast("Any", SimpleNamespace(revision=1))
    first = service_addresses.folder_roles(cast("Any", None), user.id, accounts, [1, 2])
    assert first == {1: {"Sent": "sent"}}
    assert service_addresses.folder_roles(cast("Any", None), user.id, accounts, [1, 2]) == first
    # Only a listing that answered is kept; a new revision of the settings lists again.
    assert listing.asked == [1, 2, 2]
    newer = cast("Any", SimpleNamespace(revision=2))
    service_addresses.folder_roles(cast("Any", None), user.id, newer, [1])
    assert listing.asked == [1, 2, 2, 1]


class _StuckError(Exception):
    """The deadline passes while this account is listed."""


def test_a_slow_account_costs_the_others_nothing(
    monkeypatch: pytest.MonkeyPatch, user: User
) -> None:
    def folders(account: int) -> list[SimpleNamespace]:
        if account == 4:
            raise _StuckError
        return [SimpleNamespace(path="Sent", role="sent")]

    def run(*args: Any, **_: Any) -> None:
        with contextlib.suppress(_StuckError):
            args[5](SimpleNamespace(folders=folders))
        raise mail_refusal("timeout", "Too slow.")

    monkeypatch.setattr(service_addresses, "_run", run)
    accounts = cast("Any", SimpleNamespace(revision=1))
    assert service_addresses.folder_roles(cast("Any", None), user.id, accounts, [3, 4]) == {
        3: {"Sent": "sent"}
    }


def test_only_the_users_own_rows(db_session: Session, user: User) -> None:
    other = create_user(db_session, "someone-else", PASSWORD)
    mine = ready(db_session, user, 1, "INBOX")
    theirs = ready(db_session, other, 1, "INBOX")
    db_session.add_all(
        [
            received(mine, user, 1, person("bea@example.org")),
            received(theirs, other, 1, person("bob@example.org")),
        ]
    )
    db_session.commit()
    every = {"folders": [mine, theirs], "sent": [], "inbound": [mine, theirs]}
    assert suggest(db_session, user, "b", **every) == [("bea@example.org", "", False)]


def test_the_route_suggests_from_the_users_index(
    user_client: TestClient,
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    identities = [{"address": "Me@Example.org", "name": "Me", "default": True}]
    account = added(user_client, new_account("work", identities=identities))
    inbox = ready(db_session, user, account, "INBOX")
    sent_box = ready(db_session, user, account, "Sent")
    db_session.add_all(
        [
            sent(sent_box, user, 1, person("cleo@example.org", "Cleo")),
            received(inbox, user, 1, person("carl@example.org", "Carl")),
        ]
    )
    db_session.commit()

    # The test account's server cannot be reached: no folder is known to be its Sent.
    caplog.set_level(logging.INFO)
    answer = user_client.post(URL, json={"q": "C"})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "addresses": [{"name": "Carl", "address": "carl@example.org", "sent": False}]
    }
    assert "example.org" not in caplog.text
    assert "Carl" not in caplog.text

    def roles(*_: object) -> dict[int, dict[str, str]]:
        return {account: {"INBOX": "inbox", "Sent": "sent"}}

    monkeypatch.setattr(service_addresses, "folder_roles", roles)
    assert user_client.post(URL, json={"q": "c"}).json()["addresses"] == [
        {"name": "Cleo", "address": "cleo@example.org", "sent": True},
        {"name": "Carl", "address": "carl@example.org", "sent": False},
    ]
    assert user_client.post(URL, json={"q": "c", "limit": 1}).json()["addresses"] == [
        {"name": "Cleo", "address": "cleo@example.org", "sent": True},
    ]

    assert user_client.post(URL, json={"q": ""}).status_code == 422
    assert user_client.post(URL, json={"q": "c", "limit": 11}).status_code == 422
    assert user_client.post(URL, json={"q": "c", "extra": 1}).status_code == 422
    assert user_client.post(URL, json={"q": "c" * 201}).status_code == 422


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    assert api_client.post(URL, json={"q": "a"}).status_code == 401
