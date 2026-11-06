"""`src/mail/compose.it.test.ts`, and the parts of `draft-tools.it.test.ts` and
`send-tools.it.test.ts` that are compose, identity and threading, against the test Dovecot. Drafts
and the gate have their own tests."""

import base64
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from mailroom_core.mail import (
    DRAFT_HEADER,
    AttachmentSource,
    Mailbox,
    MailConnections,
    MailError,
    MessageInput,
    OutgoingAttachment,
    attachments_from,
    compose,
    draft_answers,
    format_ref,
    forward_subject,
    forwarded_original,
    from_identity,
    is_own_draft,
    list_messages,
    mark_answered,
    original_message,
    parse_ref,
    quoted_original,
    read_attachment,
    read_message,
    recipient_list,
    reply_recipients,
    reply_subject,
    threading,
    trash_messages,
    without_headers,
)
from mailroom_core.testing import (
    ImapTestServer,
    MimeAttachment,
    connections_for,
    folder_contents,
    mime,
    raw_client,
    seed,
    test_account,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mailroom_core.config import AccountConfig
    from mailroom_core.mail import MessageRef

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


@pytest.fixture
def harness(imap_server: ImapTestServer) -> Iterator[object]:
    made: list[MailConnections] = []

    def make(*accounts: AccountConfig) -> MailConnections:
        connections = connections_for(imap_server, accounts)
        made.append(connections)
        return connections

    yield make
    for connections in made:
        connections.close_all()


def with_alias(server: ImapTestServer, user: str) -> AccountConfig:
    return test_account(
        "acc",
        user,
        server,
        identities=[
            {"address": f"{user}@example.test", "name": "Me", "default": True},
            {"address": "alias@example.test", "name": "Alias", "default": False},
        ],
    )


def only_ref(mail: MailConnections, folder: str = "INBOX") -> MessageRef:
    [message] = list_messages(mail, "acc", folder, previews=False).messages
    return parse_ref(message.ref)


def test_collects_threading_addressing_text_and_attachments(imap_server, harness) -> None:
    user = imap_server.unique_user("original")
    raw = mime(
        "Plans",
        sender="Ana <ana@example.org>",
        to="list@example.org",
        message_id="<m1@example.org>",
        references="<root@example.org> <prev@example.org>",
        text="Shall we?",
        attachments=[MimeAttachment("plan.pdf", "application/octet-stream", PDF)],
    ).replace("MIME-Version", f"Delivered-To: {user}@example.test\r\nMIME-Version", 1)
    seed(imap_server, user, "INBOX", raw)
    mail = harness(test_account("acc", user, imap_server))
    ref = only_ref(mail)

    original = original_message(mail, ref)
    assert original.message_id == "<m1@example.org>"
    assert original.references == ("<root@example.org>", "<prev@example.org>")
    assert original.subject == "Plans"
    assert original.sender == (Mailbox("ana@example.org", "Ana"),)
    assert original.addressed.to == ("list@example.org",)
    assert original.addressed.delivered == (f"{user}@example.test",)
    assert original.text == "Shall we?"
    assert [a.part for a in original.attachments] == ["2"]

    [attachment] = attachments_from(mail, [AttachmentSource(ref, "2")], 1024 * 1024)
    assert attachment.filename == "plan.pdf"
    assert attachment.content_type == "application/pdf"
    assert attachment.content == PDF

    with pytest.raises(MailError, match="not an attachment"):
        attachments_from(mail, [AttachmentSource(ref, "1")], 1024 * 1024)
    with pytest.raises(MailError, match="may total") as raised:
        attachments_from(mail, [AttachmentSource(ref, "2")], 10)
    assert raised.value.code == "too_large"
    with pytest.raises(MailError, match="out of date"):
        original_message(mail, replace(ref, uid_validity=1))


def test_caps_attachments_together_and_counts_what_is_carried(imap_server, harness) -> None:
    user = imap_server.unique_user("cap")
    data = bytes(range(256)) * 10
    seed(
        imap_server,
        user,
        "INBOX",
        mime(
            "two",
            text="x",
            attachments=[
                MimeAttachment("a.bin", "application/x-a", data),
                MimeAttachment("b.bin", "application/x-b", data),
            ],
        ),
    )
    mail = harness(test_account("acc", user, imap_server))
    ref = only_ref(mail)
    both = [AttachmentSource(ref, "2"), AttachmentSource(ref, "3")]

    assert [a.content for a in attachments_from(mail, both, 2 * len(data))] == [data, data]
    with pytest.raises(MailError, match="may total"):
        attachments_from(mail, both, 2 * len(data) - 1)
    with pytest.raises(MailError, match="may total"):
        attachments_from(mail, both, -1)


def test_attaches_a_calendar_part_but_not_what_html_shows_in_place(imap_server, harness) -> None:
    user = imap_server.unique_user("parts")
    raw = (
        "From: a@example.org\r\nTo: b@example.org\r\nSubject: parts\r\nMIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="m"\r\n\r\n'
        '--m\r\nContent-Type: multipart/related; boundary="r"\r\n\r\n'
        "--r\r\nContent-Type: text/html\r\n\r\n<img src=cid:one@x>\r\n"
        "--r\r\nContent-Type: image/png\r\nContent-ID: <one@x>\r\n"
        "Content-Transfer-Encoding: base64\r\n\r\niVBORw0KGgo=\r\n"
        "--r--\r\n"
        "--m\r\nContent-Type: text/calendar; method=REQUEST\r\n\r\nBEGIN:VCALENDAR\r\n"
        "--m--\r\n"
    )
    seed(imap_server, user, "INBOX", raw)
    mail = harness(test_account("acc", user, imap_server))
    ref = only_ref(mail)

    [invite] = attachments_from(mail, [AttachmentSource(ref, "2")], 1024)
    assert invite.filename == "part-2"
    assert invite.content.startswith(b"BEGIN:VCALENDAR")
    assert read_attachment(mail, ref, "1.2", max_bytes=1024).size > 0
    with pytest.raises(MailError, match="not an attachment"):
        attachments_from(mail, [AttachmentSource(ref, "1.2")], 1024)


def test_reads_at_most_the_start_of_a_very_long_original(imap_server, harness) -> None:
    user = imap_server.unique_user("long")
    line = "word " * 15 + "\n"
    seed(imap_server, user, "INBOX", mime("long", text=line * 40_000))
    mail = harness(test_account("acc", user, imap_server))

    text = original_message(mail, only_ref(mail)).text
    assert 10_000 < len(text) <= 20_000
    assert text.startswith("word word")


def test_answers_an_html_message_as_text(imap_server, harness) -> None:
    user = imap_server.unique_user("html")
    seed(imap_server, user, "INBOX", mime("h", html="<p>Hello <b>there</b></p>"))
    mail = harness(test_account("acc", user, imap_server))
    assert original_message(mail, only_ref(mail)).text == "Hello there"


def test_drafts_a_threaded_reply_from_the_alias_the_original_went_to(imap_server, harness) -> None:
    user = imap_server.unique_user("reply")
    seed(
        imap_server,
        user,
        "INBOX",
        mime(
            "Dinner",
            sender="Ana <ana@example.org>",
            to="alias@example.test",
            message_id="<dinner@example.org>",
            text="Friday at 8?",
        ),
    )
    account = with_alias(imap_server, user)
    mail = harness(account)
    ref = only_ref(mail)
    original = original_message(mail, ref)
    answer = reply_recipients(original, account)
    threaded = threading(original)
    composed = compose(
        MessageInput(
            sender=from_identity(account, None, original.addressed),
            to=answer.to,
            cc=answer.cc,
            subject=reply_subject(original.subject),
            text=f"Yes, see you there.\n\n{quoted_original(original, 'UTC')}",
            in_reply_to=threaded.in_reply_to,
            references=threaded.references,
            draft=True,
            answers=format_ref(ref),
        )
    )
    seed(imap_server, user, "Drafts", composed.stored, ["\\Draft", "\\Seen"])

    [draft] = folder_contents(imap_server, user, "Drafts")
    assert set(draft.flags) == {"\\Draft", "\\Seen"}
    assert b"From: Alias <alias@example.test>" in draft.source
    assert b"To: Ana <ana@example.org>" in draft.source
    assert b"In-Reply-To: <dinner@example.org>" in draft.source
    assert b"Subject: Re: Dinner" in draft.source

    draft_ref = only_ref(mail, "Drafts")
    shown = read_message(mail, draft_ref, include_quoted=True)
    assert shown.body is not None
    assert "> Friday at 8?" in shown.body.text
    assert shown.message_id == composed.message_id

    with raw_client(imap_server, user) as client:
        client.select_folder("Drafts", readonly=True)
        field = f"BODY.PEEK[HEADER.FIELDS ({DRAFT_HEADER.upper()})]"
        fetched = client.fetch([draft_ref.uid], [field])[draft_ref.uid]
    [block] = [value for key, value in fetched.items() if key.startswith(b"BODY[")]
    assert is_own_draft(block)
    assert draft_answers(block) == ref


def test_round_trips_bcc_attachments_and_non_ascii_through_the_server(imap_server, harness) -> None:
    user = imap_server.unique_user("roundtrip")
    composed = compose(
        MessageInput(
            sender=Mailbox(f"{user}@example.test", "Crème"),
            to=recipient_list(["Ana <ana@example.org>"]),
            bcc=recipient_list(["hidden@example.org"]),
            subject="Perché no?\r\nBcc: victim@example.org",
            text="Più testo\n",
            attachments=(
                OutgoingAttachment("relazione finale è.pdf", "application/pdf", PDF),
                OutgoingAttachment("data.csv", "text/csv", b"a,b\n1,\xff\n"),
            ),
            draft=True,
        )
    )
    seed(imap_server, user, "Drafts", composed.stored, ["\\Draft"])
    mail = harness(test_account("acc", user, imap_server))
    ref = only_ref(mail, "Drafts")

    shown = read_message(mail, ref)
    assert shown.subject == "Perché no? Bcc: victim@example.org"
    assert shown.sender[0].name == "Crème"
    assert shown.body is not None
    assert shown.body.text == "Più testo"
    assert [(a.part, a.name, a.type) for a in shown.attachments] == [
        ("2", "relazione finale è.pdf", "application/pdf"),
        ("3", "data.csv", "text/csv"),
    ]
    assert read_attachment(mail, ref, "2", max_bytes=1024).data == PDF
    assert read_attachment(mail, ref, "3", max_bytes=1024).data == b"a,b\n1,\xff\n"

    [draft] = folder_contents(imap_server, user, "Drafts")
    assert draft.source.count(b"Bcc: hidden@example.org") == 1
    assert not any(line.startswith(b"Bcc: victim") for line in draft.source.split(b"\r\n"))
    assert without_headers(draft.source, ["Bcc", DRAFT_HEADER]) == composed.wire


def test_forwards_with_the_attachments_byte_for_byte(imap_server, harness) -> None:
    user = imap_server.unique_user("forward")
    seed(
        imap_server,
        user,
        "INBOX",
        mime(
            "Invoice",
            sender="Ana <ana@example.org>",
            text="Attached.",
            attachments=[MimeAttachment("invoice.pdf", "application/octet-stream", PDF)],
        ),
    )
    account = test_account("acc", user, imap_server)
    mail = harness(account)
    ref = only_ref(mail)
    original = original_message(mail, ref)
    attachments = attachments_from(
        mail, [AttachmentSource(ref, a.part) for a in original.attachments], 1024 * 1024
    )
    wire = compose(
        MessageInput(
            sender=from_identity(account, None, original.addressed),
            to=recipient_list(["bo@example.org"]),
            subject=forward_subject(original.subject),
            text=f"FYI\n\n{forwarded_original(original, 'UTC')}",
            attachments=attachments,
        )
    ).wire

    assert b"Subject: Fwd: Invoice" in wire
    assert b"---------- Forwarded message ---------" in wire
    assert b'Content-Type: application/pdf; name="invoice.pdf"' in wire
    assert base64.b64encode(PDF) in wire.replace(b"\r\n", b"")


def test_marks_the_original_answered(imap_server, harness) -> None:
    user = imap_server.unique_user("answered")
    seed(imap_server, user, "INBOX", mime("Dinner", text="Friday?"))
    mail = harness(test_account("acc", user, imap_server))
    ref = only_ref(mail)

    mark_answered(mail, ref)
    [message] = folder_contents(imap_server, user, "INBOX")
    assert "\\Answered" in message.flags
    with pytest.raises(MailError, match="out of date"):
        mark_answered(mail, replace(ref, uid_validity=1))


def test_tags_a_removal_by_trash_in_the_audit_items(imap_server, harness) -> None:
    user = imap_server.unique_user("tag")
    seed(imap_server, user, "Drafts", mime("by hand"))
    mail = harness(test_account("acc", user, imap_server))
    ref = format_ref(only_ref(mail, "Drafts"))

    result = trash_messages(mail, [ref], removed="trashed")
    assert [item.record["removed"] for item in result.items] == ["trashed"]
