"""A conversation on the mail index (``mailroom.mail.index.threads`` and ``read_conversations``):
a message joins the one it answers only when the index holds it with the same subject, replies
stored first join it once it is, the flag of a sender new to a conversation, and the refs an
action on one takes, which leave out the user's own copies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select

from mailroom.mail.index import reads, threads
from mailroom.mail.index import sync as index_sync
from mailroom.models import MailIndexMessage
from mailroom_core.mail import IndexedMessage, MessageRef, Source, format_ref
from mailroom_core.render import Address
from tests.test_mail_index_reads import ALL_MAIL, GMAIL, UTC_ZONE, folder, ready, unasked
from tests.test_mail_index_rows import message

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom.models import User

DAY = datetime(2026, 11, 20, tzinfo=UTC)
ROOT = "<plan@example.org>"


@pytest.fixture(autouse=True)
def _every_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)


def indexed(uid: int, subject: str, **fields: Any) -> IndexedMessage:
    values: dict[str, Any] = {
        "account": 1,
        "folder": "INBOX",
        "uid_validity": 7,
        "uid": uid,
        "date": None,
        "received": DAY + timedelta(hours=uid),
        "sender": (Address(name="Ann", address="ann@example.org"),),
        "to": (Address(name=None, address="me@example.org"),),
        "cc": (),
        "subject": subject,
        "flags": frozenset(),
        "size": 100,
        "has_attachments": False,
        "message_id": f"<{uid}@example.org>",
        "in_reply_to": None,
        "references_root": None,
        "list_id": None,
        "snippet": None,
    }
    return IndexedMessage(**(values | fields))


def keys(db: Session, folder_id: int | None = None) -> dict[int, str | None]:
    query = select(MailIndexMessage.uid, MailIndexMessage.thread_key)
    if folder_id is not None:
        query = query.where(MailIndexMessage.folder_id == folder_id)
    return {row.uid: row.thread_key for row in db.execute(query)}


def test_a_subject_is_compared_without_its_prefixes() -> None:
    assert threads.normalized_subject("Re: Fwd:  The  PLAN ") == "the plan"
    for prefixed in ("RE[2]: the plan", "AW: the plan", "R: I: the plan", "fw:the plan"):
        assert threads.normalized_subject(prefixed) == "the plan"
    assert threads.normalized_subject("Return of the plan") == "return of the plan"


def test_a_message_joins_what_it_answers_only_when_the_index_holds_it_with_its_subject(
    db_session: Session, user: User
) -> None:
    inbox = ready(db_session, user, 1, "INBOX")
    other = ready(db_session, user, 2, "INBOX")

    def store(folder_id: int, account: int, *messages: IndexedMessage) -> None:
        index_sync._store(db_session, user.id, account, folder_id, messages)  # noqa: SLF001
        db_session.commit()

    store(inbox, 1, indexed(1, "The plan", message_id=ROOT))
    store(
        inbox,
        1,
        indexed(2, "Re: the plan", references_root=ROOT),
        # A References header naming the conversation, with a subject of its own.
        indexed(3, "Invoice overdue", references_root=ROOT),
        indexed(4, "Re: the plan", in_reply_to="<2@example.org>"),
        indexed(5, "Re: lunch", references_root="<unknown@example.org>"),
    )
    # Another account's index does not hold the root.
    store(other, 2, indexed(1, "Re: the plan", account=2, references_root=ROOT))
    assert keys(db_session, inbox) == {
        1: ROOT,
        2: ROOT,
        3: "<3@example.org>",
        4: ROOT,
        5: "<5@example.org>",
    }
    assert keys(db_session, other) == {1: "<1@example.org>"}
    assert threads.thread_keys(
        db_session, user.id, 1, [indexed(9, "Re: x", gmail_thread_id=42, references_root=ROOT)]
    ) == {9: "gmail:42"}


def test_replies_stored_before_what_they_answer_join_it(db_session: Session, user: User) -> None:
    inbox = ready(db_session, user, 1, "INBOX")
    older = ready(db_session, user, 1, "Archive")

    def store(folder_id: int, *messages: IndexedMessage) -> None:
        index_sync._store(db_session, user.id, 1, folder_id, messages)  # noqa: SLF001
        db_session.commit()

    # Newest first, as a folder is indexed: the replies, then (in another folder) the root.
    store(
        inbox,
        indexed(3, "Re: Re: the plan", in_reply_to="<2@example.org>"),
        indexed(4, "Invoice", references_root=ROOT),
    )
    store(inbox, indexed(2, "Re: the plan", references_root=ROOT))
    assert keys(db_session) == {3: "<2@example.org>", 4: "<4@example.org>", 2: "<2@example.org>"}
    store(older, indexed(1, "The plan", message_id=ROOT))
    assert keys(db_session) == {3: ROOT, 4: "<4@example.org>", 2: ROOT, 1: ROOT}


def test_a_sender_new_to_a_conversation_is_flagged(db_session: Session, user: User) -> None:
    inbox = ready(db_session, user, 1, "INBOX")
    stranger = (Address(name="Eve", address="eve@example.net"),)
    index_sync._store(  # noqa: SLF001
        db_session,
        user.id,
        1,
        inbox,
        [
            indexed(1, "The plan", message_id=ROOT, cc=(Address(name=None, address="bob@x.org"),)),
            indexed(2, "Re: the plan", references_root=ROOT, sender=stranger),
            indexed(3, "Pay this", references_root=ROOT, sender=stranger),
            indexed(
                4,
                "Lunch",
                message_id="<lunch@example.org>",
                cc=(Address(name=None, address="bob@x.org"),),
            ),
            indexed(
                5,
                "Re: lunch",
                references_root="<lunch@example.org>",
                sender=(Address(name="Bob", address="BOB@x.org"),),
                cc=(Address(name=None, address="ann@example.org"),),
            ),
        ],
    )
    db_session.commit()
    sources = reads.index_sources(
        db_session, user.id, [Source(1, "INBOX")], gmail=unasked, listing=unasked
    )
    assert sources is not None
    found = reads.read_conversations(db_session, user.id, sources, None, zone=UTC_ZONE, limit=10)
    flagged = {summary.uid: (c.count, c.latest_from_new_sender) for summary, _, c in found}
    # The forged reply with its own subject is a row of its own; the one copying the subject
    # joins, and its sender, Eve, is new to the conversation. Bob, in Cc of lunch, is not.
    assert flagged == {5: (2, False), 3: (1, False), 2: (2, True)}


def test_a_conversations_refs_leave_out_the_users_own_copies(
    db_session: Session, user: User
) -> None:
    inbox = ready(db_session, user, 1, "INBOX")
    sent = ready(db_session, user, 1, "Sent")
    drafts = ready(db_session, user, 1, "Drafts")
    every = ready(db_session, user, 2, ALL_MAIL)

    def at(hours: int) -> datetime:
        return DAY + timedelta(hours=hours)

    db_session.add_all(
        [
            message(inbox, user.id, 1, received=at(1), thread_key="<a>"),
            message(sent, user.id, 1, received=at(2), thread_key="<a>"),
            message(drafts, user.id, 1, received=at(3), thread_key="<a>"),
            message(sent, user.id, 2, received=at(4), thread_key="<b>"),
            message(sent, user.id, 3, received=at(5), thread_key="<b>"),
            message(every, user.id, 1, received=at(1), thread_key="g", labels=["\\Inbox"]),
            message(every, user.id, 2, received=at(2), thread_key="g", labels=["\\Sent"]),
            message(every, user.id, 3, received=at(3), thread_key="g", labels=["Work", "\\Draft"]),
        ]
    )
    db_session.commit()
    listings: list[int] = []

    def listing(account: int) -> list[Any]:
        listings.append(account)
        return [
            folder("INBOX", role="inbox"),
            folder("Sent", role="sent"),
            folder("Drafts", role="drafts"),
        ]

    wanted = [Source(1, "INBOX"), Source(1, "Sent"), Source(1, "Drafts")]
    sources = reads.index_sources(db_session, user.id, wanted, gmail=unasked, listing=unasked)
    assert sources is not None
    found = reads.read_conversations(
        db_session, user.id, sources, None, zone=UTC_ZONE, limit=10, listing=listing
    )
    by_key = {summary.uid: c for summary, _, c in found if summary.folder != "INBOX"}
    mine = found[0][2]  # <b>: only the user's own, all kept
    assert (mine.count, mine.refs) == (
        2,
        (format_ref(MessageRef(1, "Sent", 7, 3)), format_ref(MessageRef(1, "Sent", 7, 2))),
    )
    theirs = by_key[1]  # <a>, latest in Drafts: acted on in INBOX only, counted whole
    assert (theirs.count, theirs.refs) == (3, (format_ref(MessageRef(1, "INBOX", 7, 1)),))
    assert listings == [1]

    gmail = reads.index_sources(
        db_session,
        user.id,
        [Source(2, "INBOX"), Source(2, "Work"), Source(2, "[Gmail]/Sent Mail")],
        gmail=lambda _: True,
        listing=lambda _: GMAIL,
    )
    assert gmail is not None
    [(latest, _, labelled)] = reads.read_conversations(
        db_session, user.id, gmail, None, zone=UTC_ZONE, limit=10
    )
    inbox_ref = format_ref(MessageRef(2, ALL_MAIL, 7, 1))
    assert (latest.uid, labelled.count, labelled.refs) == (3, 3, (inbox_ref,))
    assert labelled.listed_folders == {inbox_ref: "INBOX"}
