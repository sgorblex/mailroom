"""An index's batches and rows against the test Dovecot: newest first, the age limit, what a
row holds, a sender's hostile headers, and a plan whose folder changed."""

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest

from mailroom_core.mail import (
    MailConnections,
    MailError,
    MessageRef,
    folder_index_plan,
    gmail_label_ref,
    indexed_messages,
)
from mailroom_core.mail.parsed import MAX_HEADER_BYTES
from mailroom_core.render import Address
from mailroom_core.testing import (
    ImapTestServer,
    MimeAttachment,
    connections_for,
    mime,
    seed,
    test_account,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


@pytest.fixture
def user(imap_server: ImapTestServer) -> str:
    return imap_server.unique_user("index")


@pytest.fixture
def mail(imap_server: ImapTestServer, user: str) -> Iterator[MailConnections]:
    connections = connections_for(imap_server, [test_account(1, user, imap_server)])
    yield connections
    connections.close_all()


def test_plans_newest_first_in_batches_since_a_day(
    imap_server: ImapTestServer, user: str, mail: MailConnections
) -> None:
    old = datetime(2020, 1, 1, 12, tzinfo=UTC)
    seed(imap_server, user, "INBOX", mime("old"), internal_date=old)
    recent = [seed(imap_server, user, "INBOX", mime(f"m{n}")) for n in range(3)]

    everything = folder_index_plan(mail, 1, "INBOX", size=2)
    assert [len(batch) for batch in everything.batches] == [2, 2]
    assert everything.batches[0] == (recent[2], recent[1])
    assert everything.highest_modseq is not None

    since = folder_index_plan(mail, 1, "INBOX", since=date(2021, 1, 1), size=2)
    assert since.batches == ((recent[2], recent[1]), (recent[0],))
    assert since.uid_validity == everything.uid_validity
    assert folder_index_plan(mail, 1, "Trash").batches == ()


def test_fetches_what_an_index_keeps(
    imap_server: ImapTestServer, user: str, mail: MailConnections
) -> None:
    root = "<root@example.org>"
    raw = mime(
        "Re: the plan",
        to="Ale <ale@example.test>, bob@example.test",
        message_id="<reply@example.org>",
        in_reply_to="<parent@example.org>",
        references=f"{root} <parent@example.org>",
        text="Hello there,\r\nthe plan is on.\r\n",
        attachments=[MimeAttachment("a.pdf", "application/pdf", b"%PDF")],
    )
    extra = "Cc: Carla <carla@example.test>\r\nList-Id: Team <team.example.org>\r\n"
    raw = raw.replace("MIME-Version", f"{extra}MIME-Version", 1)
    uid = seed(imap_server, user, "INBOX", raw, flags=["\\Seen", "$MailroomSort"])
    plain = seed(imap_server, user, "INBOX", "Subject: bare\r\n\r\n")

    plan = folder_index_plan(mail, 1, "INBOX")
    rows = indexed_messages(mail, 1, "INBOX", [uid, plain, uid], uid_validity=plan.uid_validity)
    assert [row.uid for row in rows] == [plain, uid]
    bare, row = rows
    assert row.message_ref == MessageRef(1, "INBOX", plan.uid_validity, uid)
    assert row.sender == (Address("Maria Rossi", "maria@example.org"),)
    assert row.to == (Address("Ale", "ale@example.test"), Address(None, "bob@example.test"))
    assert row.cc == (Address("Carla", "carla@example.test"),)
    assert row.subject == "Re: the plan"
    assert row.flags == {"\\Seen", "$MailroomSort"}
    assert row.has_attachments
    assert row.size == len(raw.encode())
    assert row.date is not None
    assert row.received is not None
    assert row.message_id == "<reply@example.org>"
    assert row.in_reply_to == "<parent@example.org>"
    assert row.references_root == root
    assert row.thread_key == root
    assert row.list_id == "team.example.org"
    assert row.snippet == "Hello there, the plan is on."
    assert row.gmail_labels == ()
    assert row.gmail_message_id is None

    assert bare.subject == "bare"
    assert bare.date is None
    assert bare.sender == ()
    assert bare.thread_key is None
    assert not bare.has_attachments


def test_keeps_hostile_headers_bounded_and_storable(
    imap_server: ImapTestServer, user: str, mail: MailConnections
) -> None:
    many = ", ".join(f"r{n}@example.test" for n in range(150))
    references = "\r\n ".join(f"<{n:0>60}@example.org>" for n in range(MAX_HEADER_BYTES // 60))
    raw = (
        "From: =?utf-8?q?Eve=00=0D=0ABcc=3A_x=40evil.test?= <eve@example.org>\r\n"
        f"To: {many}\r\n"
        "Subject: =?utf-7?q?+2AA-?= =?utf-8?b?AAEC?=\u202eok\r\n"
        "Message-ID: <has space@example.org>\r\n"
        "In-Reply-To: <parent@example.org>\r\n"
        "List-Id: <notéascii.example.org>\r\n"
        f"References: {references}\r\n"
        "\r\n"
        "body\r\n"
    ).encode()
    uid = seed(imap_server, user, "INBOX", raw)

    plan = folder_index_plan(mail, 1, "INBOX")
    (row,) = indexed_messages(mail, 1, "INBOX", [uid], uid_validity=plan.uid_validity)
    assert len(row.to) == 100
    assert row.sender == (Address("Eve Bcc: x@evil.test", "eve@example.org"),)
    assert row.subject == "\ufffdok"
    for text in (row.subject, *(a.name or "" for a in row.sender)):
        text.encode("utf-8")
        assert not any(c in text for c in "\x00\r\n\u202e")
    assert row.message_id is None
    assert row.list_id is None
    # The References field is cut past MAX_HEADER_BYTES and dropped whole, In-Reply-To kept.
    assert row.references_root is None
    assert row.thread_key == "<parent@example.org>"


def test_refuses_a_batch_of_another_uidvalidity(
    imap_server: ImapTestServer, user: str, mail: MailConnections
) -> None:
    uid = seed(imap_server, user, "INBOX", mime("one"))
    plan = folder_index_plan(mail, 1, "INBOX")
    with pytest.raises(MailError) as caught:
        indexed_messages(mail, 1, "INBOX", [uid], uid_validity=plan.uid_validity + 1)
    assert caught.value.code == "stale_reference"


def test_gmail_ids_are_refused_elsewhere(
    imap_server: ImapTestServer, user: str, mail: MailConnections
) -> None:
    seed(imap_server, user, "INBOX", mime("one"))
    with pytest.raises(MailError) as caught:
        gmail_label_ref(mail, 1, "INBOX", 1)
    assert caught.value.code == "unsupported"
