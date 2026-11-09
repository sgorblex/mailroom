"""The gate against Mailpit and Dovecot: `src/send/gate.it.test.ts`, and the function-level cases of
`src/mcp/tools/send-tools.it.test.ts` (their result text belongs to the tool layers). Mailpit
adds a Bcc header of its own for a recipient in no other, so `SmtpStub` shows the wire copy."""

import re
from email import message_from_bytes, policy
from typing import TYPE_CHECKING, Any, cast

import pytest

from mailroom_core.config import AccountConfig, Limits
from mailroom_core.mail import (
    MAX_DRAFT_BYTES,
    AttachmentSource,
    Mailbox,
    MailConnections,
    MailError,
    MessageInput,
    MessageRef,
    OutgoingAttachment,
    attachments_from,
    compose,
    format_ref,
    forward_subject,
    forwarded_original,
    from_identity,
    original_message,
    quoted_original,
    recipient_list,
    reply_recipients,
    reply_subject,
    save_draft,
)
from mailroom_core.mail import threading as thread_of
from mailroom_core.send import (
    DRAFT_KEPT,
    GateDeps,
    GateResult,
    SmtpSender,
    mark_original,
    prepare_draft,
    send_through_gate,
    settle_draft,
    verify_smtp,
)
from mailroom_core.send import gate as gate_module
from mailroom_core.store import AccountStore
from mailroom_core.testing import (
    ImapTestServer,
    MimeAttachment,
    SmtpStub,
    SmtpTestServer,
    connections_for,
    folder_contents,
    mime,
    raw_client,
    seed,
    test_account,
    unverified_context,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from email.message import EmailMessage

    from sqlalchemy import Engine

    from mailroom_core.mail import AuditItem, Draft

pytestmark = pytest.mark.integration

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
BINARY = bytes(range(256)) * 40


def parsed(raw: bytes) -> EmailMessage:
    return cast("EmailMessage", message_from_bytes(raw, policy=policy.default))


class Live:
    """A gate over the test servers, with what it audited."""

    def __init__(self, deps: GateDeps, mail: MailConnections) -> None:
        self.deps = deps
        self.mail = mail
        self.items: list[AuditItem] = []

    def send(
        self, account: AccountConfig, message: MessageInput, *, by: str = "agent", **options: Any
    ) -> GateResult:
        return send_through_gate(
            self.deps,
            account,
            message,
            self.items.append,
            by=by,  # type: ignore[arg-type]
            **options,
        )

    def send_draft(self, account: AccountConfig, ref: MessageRef, *, by: str = "agent") -> Draft:
        """What `draft_send` does: the draft as stored, then its removal."""
        ready = prepare_draft(self.mail, account, ref, self.deps.limits)
        result = self.send(account, ready.message, by=by, prepared=ready.prepared)
        assert result.filed is True
        assert settle_draft(self.mail, ready.draft, self.items.append, filed=True) == ()
        return ready.draft


@pytest.fixture
def live(
    imap_server: ImapTestServer, smtp_server: SmtpTestServer, core_engine: Engine
) -> Iterator[Callable[..., Live]]:
    made: list[MailConnections] = []

    def make(*accounts: AccountConfig, limits: Limits | None = None) -> Live:
        mail = connections_for(imap_server, accounts)
        made.append(mail)
        known = {account.id: account for account in accounts}
        deps = GateDeps(
            connections=mail,
            smtp=SmtpSender(
                known.get,
                lambda _id: imap_server.password,
                ssl_context=unverified_context(),
            ),
            send_log=AccountStore(core_engine, "user-1"),
            limits=limits or Limits(),
        )
        return Live(deps, mail)

    yield make
    for mail in made:
        mail.close_all()


@pytest.fixture
def user(imap_server: ImapTestServer) -> str:
    """A new user whose special folders exist, as CI's Dovecot makes them only on request."""
    name = imap_server.unique_user("send")
    with raw_client(imap_server, name):
        return name


def direct(server: ImapTestServer, user: str, **overrides: object) -> AccountConfig:
    """An account with an alias identity, sending directly unless told otherwise."""
    fields: dict[str, object] = {
        "send_mode": "direct",
        "identities": [
            {"address": f"{user}@example.test", "default": True},
            {"address": f"{user}-alias@example.test", "name": "Alias"},
        ],
        **overrides,
    }
    return test_account("acc", user, server, **fields)


def from_user(user: str, to: list[str], **fields: Any) -> MessageInput:
    values: dict[str, Any] = {"subject": "Gate test", "text": "Hello.", **fields}
    return MessageInput(sender=Mailbox(f"{user}@example.test"), to=recipient_list(to), **values)


def ref_to(server: ImapTestServer, user: str, folder: str, uid: int) -> MessageRef:
    with raw_client(server, user) as client:
        status = client.folder_status(folder, [b"UIDVALIDITY"])
    return MessageRef("acc", folder, status[b"UIDVALIDITY"], uid)


# gate.it.test.ts


def test_delivers_hides_bcc_and_files_the_full_copy_in_sent(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    account = test_account("acc", user, imap_server, send_mode="direct")
    to = f"{user}-to@example.org"
    hidden = f"{user}-bcc@example.org"
    gate = live(account)
    result = gate.send(
        account,
        from_user(user, [f"Ana <{to}>"], bcc=recipient_list([hidden])),
    )
    assert (result.outcome, result.filed, result.rejected) == ("sent", True, ())

    [delivered] = smtp_server.delivered_to(to)
    assert sorted(delivered.recipients) == sorted([hidden, to])
    assert b"Subject: Gate test" in delivered.raw
    assert not re.search(rb"^To:.*-bcc@", delivered.raw, re.IGNORECASE | re.MULTILINE)

    [filed] = folder_contents(imap_server, user, "Sent")
    assert filed.flags == ("\\Seen",)
    assert f"Bcc: {hidden}".encode() in filed.source


# What Mailpit shows of the wire.


def test_the_wire_copy_has_no_bcc_while_the_envelope_names_it() -> None:
    composed = compose(
        MessageInput(
            sender=Mailbox("me@example.org"),
            to=recipient_list(["ana@example.org"]),
            bcc=recipient_list(["hidden@example.org"]),
            subject="s",
            text="t",
        )
    )
    with SmtpStub() as stub:
        account = AccountConfig.model_validate(
            {
                "id": "acc",
                "login": "me",
                "send_mode": "direct",
                "auth": {"type": "password"},
                "imap": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
                "smtp": stub.smtp(),
                "identities": [{"address": "me@example.org", "default": True}],
            }
        )
        SmtpSender(
            lambda _id: account, lambda _id: "x", ssl_context=SmtpStub.trusted_context()
        ).send("acc", composed.envelope, composed.wire)
    assert any("hidden@example.org" in command for command in stub.commands)
    assert b"hidden" not in stub.messages[0]
    assert b"bcc" not in stub.messages[0].lower()


def test_threading_reaches_the_recipient(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    account = direct(imap_server, user)
    to = f"{user}-to@example.org"
    live(account).send(
        account,
        from_user(
            user,
            [to],
            in_reply_to="<two@example.org>",
            references=("<one@example.org>", "<two@example.org>"),
        ),
    )
    received = parsed(smtp_server.delivered_to(to)[0].raw)
    assert received["In-Reply-To"] == "<two@example.org>"
    assert received["References"] == "<one@example.org> <two@example.org>"


def test_attachments_arrive_byte_for_byte(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    account = direct(imap_server, user)
    to = f"{user}-to@example.org"
    attachment = OutgoingAttachment("data.bin", "application/octet-stream", BINARY)
    live(account).send(account, from_user(user, [to], attachments=(attachment,)))
    [part] = parsed(smtp_server.delivered_to(to)[0].raw).iter_attachments()
    assert part.get_filename() == "data.bin"
    assert part.get_content() == BINARY


def test_non_ascii_headers_arrive_as_written(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    account = direct(imap_server, user)
    to = f"{user}-to@example.org"
    attachment = OutgoingAttachment("Résumé ☕.txt", "text/plain", b"x")
    message = MessageInput(
        sender=Mailbox(f"{user}@example.test", "Zoë Müller"),
        to=(Mailbox(to, "Ana Núñez"),),
        subject="Caffè ☕ alle tre: perché no?",
        text="Città, più è meglio.",
        attachments=(attachment,),
    )
    live(account).send(account, message)
    raw = smtp_server.delivered_to(to)[0].raw
    assert raw.split(b"\r\n\r\n", 1)[0].isascii()
    received = parsed(raw)
    assert received["Subject"] == "Caffè ☕ alle tre: perché no?"
    assert received["From"].addresses[0].display_name == "Zoë Müller"
    assert received["To"].addresses[0].display_name == "Ana Núñez"
    assert received.get_body(("plain",)).get_content().strip() == "Città, più è meglio."
    assert next(received.iter_attachments()).get_filename() == "Résumé ☕.txt"


def test_dot_leading_lines_arrive_as_written(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    account = direct(imap_server, user)
    to = f"{user}-to@example.org"
    text = ".\n..two\n.leading\nmiddle\n.\nlast"
    live(account).send(account, from_user(user, [to], text=text))
    received = parsed(smtp_server.delivered_to(to)[0].raw)
    assert received.get_content().replace("\r\n", "\n").strip() == text


def test_starttls_against_mailpit(
    imap_server: ImapTestServer, smtp_starttls_server: SmtpTestServer, user: str
) -> None:
    account = test_account(
        "acc",
        user,
        imap_server,
        smtp={
            "host": smtp_starttls_server.host,
            "port": smtp_starttls_server.port,
            "tls": "starttls",
        },
    )
    to = f"{user}-to@example.org"
    composed = compose(from_user(user, [to]))
    SmtpSender(lambda _id: account, lambda _id: "any", ssl_context=unverified_context()).send(
        "acc", composed.envelope, composed.wire
    )
    [delivered] = smtp_starttls_server.delivered_to(to)
    assert b"Subject: Gate test" in delivered.raw


def test_implicit_tls_against_mailpit(
    imap_server: ImapTestServer, smtp_server: SmtpTestServer, user: str
) -> None:
    account = test_account("acc", user, imap_server)
    assert account.smtp.tls == "implicit"
    to = f"{user}-to@example.org"
    composed = compose(from_user(user, [to]))
    SmtpSender(lambda _id: account, lambda _id: "any", ssl_context=unverified_context()).send(
        "acc", composed.envelope, composed.wire
    )
    assert len(smtp_server.delivered_to(to)) == 1


def test_mailpit_s_self_signed_certificate_is_refused_by_default(
    imap_server: ImapTestServer, user: str
) -> None:
    account = test_account("acc", user, imap_server)
    composed = compose(from_user(user, [f"{user}-to@example.org"]))
    sender = SmtpSender(lambda _id: account, lambda _id: "any")
    with pytest.raises(MailError) as raised:
        sender.send("acc", composed.envelope, composed.wire)
    assert raised.value.code == "unreachable"


def test_verify_smtp_logs_in_at_mailpit(
    imap_server: ImapTestServer, smtp_server: SmtpTestServer, user: str
) -> None:
    account = test_account("acc", user, imap_server)
    assert verify_smtp(account, "any", ssl_context=unverified_context()) is None
    assert verify_smtp(account, "any") == "unreachable"


# send-tools.it.test.ts


def test_sends_files_the_copy_and_audits_the_delivery(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    to = f"{user}-ana@example.org"
    account = direct(imap_server, user)
    gate = live(account)
    gate.send(
        account,
        from_user(user, [f"Ana <{to}>"], subject="Hello\r\nBcc: victim@example.org", text="Hi."),
    )
    [delivered] = smtp_server.delivered_to(to)
    assert delivered.recipients == [to]
    assert len(re.findall(rb"^Subject:", delivered.raw, re.IGNORECASE | re.MULTILINE)) == 1
    assert b"Subject: Hello Bcc: victim@example.org" in delivered.raw
    assert smtp_server.delivered_to("victim@example.org") == []
    assert [m.subject for m in folder_contents(imap_server, user, "Sent")] == [
        "Hello Bcc: victim@example.org"
    ]
    [item] = gate.items
    assert item.recipients is not None
    assert (item.delivery, item.recipients.to, item.filed) == ("sent", (to,), True)


def test_replies_threaded_from_the_alias_and_marks_the_original_answered(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    ana = f"{user}-ana@example.org"
    uid = seed(
        imap_server,
        user,
        "INBOX",
        mime(
            "Dinner",
            sender=f"Ana <{ana}>",
            to=f"{user}-alias@example.test",
            message_id=f"<{user}@example.org>",
            text="Friday at 8?",
        ),
    )
    account = direct(imap_server, user)
    gate = live(account)
    ref = ref_to(imap_server, user, "INBOX", uid)
    original = original_message(gate.mail, ref)
    answer = reply_recipients(original, account)
    thread = thread_of(original)
    sender = from_identity(account, None, original.addressed)
    assert sender == Mailbox(f"{user}-alias@example.test", "Alias")
    gate.send(
        account,
        MessageInput(
            sender=sender,
            to=answer.to,
            cc=answer.cc,
            subject=reply_subject(original.subject),
            text=f"Yes!\n\n{quoted_original(original, 'UTC')}",
            in_reply_to=thread.in_reply_to,
            references=thread.references,
        ),
    )
    assert mark_original(gate.mail, ref, gate.items.append) is None
    raw = smtp_server.delivered_to(ana)[0].raw
    assert f"In-Reply-To: <{user}@example.org>".encode() in raw
    assert b"Subject: Re: Dinner" in raw
    assert parsed(raw).get_content().count("> Friday at 8?") == 1
    assert "\\Answered" in folder_contents(imap_server, user, "INBOX")[0].flags


def test_forwards_with_the_attachments_byte_for_byte(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    bo = f"{user}-bo@example.org"
    uid = seed(
        imap_server,
        user,
        "INBOX",
        mime(
            "Invoice",
            text="Attached.",
            attachments=[MimeAttachment("invoice.pdf", "application/octet-stream", PDF)],
        ),
    )
    account = direct(imap_server, user)
    gate = live(account)
    ref = ref_to(imap_server, user, "INBOX", uid)
    original = original_message(gate.mail, ref)
    kept = attachments_from(
        gate.mail,
        [AttachmentSource(ref, attachment.part) for attachment in original.attachments],
        1024 * 1024,
    )
    gate.send(
        account,
        from_user(
            user,
            [bo],
            subject=forward_subject(original.subject),
            text=f"FYI\n\n{forwarded_original(original, 'UTC')}",
            attachments=kept,
        ),
    )
    received = parsed(smtp_server.delivered_to(bo)[0].raw)
    assert received["Subject"] == "Fwd: Invoice"
    assert "---------- Forwarded message ---------" in received.get_body(("plain",)).get_content()
    [attachment] = received.iter_attachments()
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_content() == PDF


def test_sends_a_draft_byte_for_byte_without_its_bcc_or_marker(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    dee = f"{user}-dee@example.org"
    hidden = f"{user}-hidden@example.org"
    html = "<p>Hello <b>Dee</b></p>"
    uid = seed(
        imap_server,
        user,
        "Drafts",
        mime(
            "Written elsewhere",
            sender=f"{user}@example.test",
            to=dee,
            message_id=f"<{user}@example.test>",
            html=html,
        ).replace("MIME-Version", f"Bcc: {hidden}\r\nMIME-Version"),
        ["\\Draft"],
    )
    account = direct(imap_server, user)
    gate = live(account)
    gate.send_draft(account, ref_to(imap_server, user, "Drafts", uid))
    [delivered] = smtp_server.delivered_to(dee)
    assert sorted(delivered.recipients) == sorted([dee, hidden])
    assert f"Message-ID: <{user}@example.test>".encode() in delivered.raw
    assert parsed(delivered.raw).get_content() == html
    assert b"X-Mailroom-Draft" not in delivered.raw
    # The Sent copy keeps the Bcc, as the stored draft had it; the draft went to Trash.
    assert f"Bcc: {hidden}".encode() in folder_contents(imap_server, user, "Sent")[0].source
    assert folder_contents(imap_server, user, "Drafts") == []
    assert [m.subject for m in folder_contents(imap_server, user, "Trash")] == ["Written elsewhere"]


def test_refuses_to_send_a_draft_the_mode_would_only_draft_again(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    eve = f"{user}-eve@example.org"
    account = direct(imap_server, user, send_mode="drafts")
    gate = live(account)
    saved = save_draft(
        gate.mail,
        account,
        from_user(user, [eve], subject="Hold", text="x"),
        cap=1024,
        note=lambda _item: None,
    )
    assert saved.ref is not None
    with pytest.raises(MailError, match=r"The draft stays\."):
        gate.send_draft(account, saved.ref)
    assert len(folder_contents(imap_server, user, "Drafts")) == 1
    assert smtp_server.delivered_to(eve) == []
    [item] = gate.items
    assert item.recipients is not None
    assert (item.delivery, item.recipients.to) == ("refused", (eve,))


def test_a_person_sends_that_draft(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    eve = f"{user}-eve@example.org"
    account = direct(imap_server, user, send_mode="drafts")
    gate = live(account)
    saved = save_draft(
        gate.mail,
        account,
        from_user(user, [eve], subject="Hold", text="x"),
        cap=1024,
        note=lambda _item: None,
    )
    assert saved.ref is not None
    gate.send_draft(account, saved.ref, by="user")
    assert len(smtp_server.delivered_to(eve)) == 1
    assert folder_contents(imap_server, user, "Drafts") == []


def test_sends_a_draft_as_it_is_then_removes_it(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    cy = f"{user}-cy@example.org"
    account = direct(imap_server, user)
    gate = live(account)
    saved = save_draft(
        gate.mail,
        account,
        from_user(user, [cy], subject="Agenda", text="Points."),
        cap=1024,
        note=lambda _item: None,
    )
    assert saved.ref is not None
    gate.send_draft(account, saved.ref)
    raw = smtp_server.delivered_to(cy)[0].raw
    assert b"Subject: Agenda" in raw
    assert b"X-Mailroom-Draft" not in raw
    assert folder_contents(imap_server, user, "Drafts") == []
    assert b"X-Mailroom-Draft" not in folder_contents(imap_server, user, "Sent")[0].source


def test_a_sent_draft_marks_what_it_answers(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    uid = seed(imap_server, user, "INBOX", mime("Dinner", text="Friday?"))
    to = f"{user}-to@example.org"
    account = direct(imap_server, user)
    gate = live(account)
    answers = format_ref(ref_to(imap_server, user, "INBOX", uid))
    saved = save_draft(
        gate.mail,
        account,
        from_user(user, [to], answers=answers),
        cap=1024,
        note=lambda _item: None,
    )
    assert saved.ref is not None
    gate.send_draft(account, saved.ref)
    assert b"X-Mailroom-Draft" not in smtp_server.delivered_to(to)[0].raw
    assert "\\Answered" in folder_contents(imap_server, user, "INBOX")[0].flags


def test_a_draft_that_could_not_be_filed_stays_and_cannot_go_twice(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = seed(imap_server, user, "INBOX", mime("Dinner", text="Friday?"))
    to = f"{user}-to@example.org"
    account = direct(imap_server, user)
    gate = live(account)
    answers = format_ref(ref_to(imap_server, user, "INBOX", uid))
    saved = save_draft(
        gate.mail,
        account,
        from_user(user, [to], answers=answers),
        cap=1024,
        note=lambda _item: None,
    )
    assert saved.ref is not None
    ready = prepare_draft(gate.mail, account, saved.ref, gate.deps.limits)

    def refused(*_args: object) -> None:
        raise MailError("server_error")

    monkeypatch.setattr(gate_module, "append_message", refused)
    sent = gate.send(account, ready.message, prepared=ready.prepared, key="the-key")
    monkeypatch.undo()

    assert sent.filed is False
    assert settle_draft(gate.mail, ready.draft, gate.items.append, filed=False) == (DRAFT_KEPT,)
    assert len(folder_contents(imap_server, user, "Drafts")) == 1
    assert folder_contents(imap_server, user, "Sent") == []
    assert "\\Answered" in folder_contents(imap_server, user, "INBOX")[0].flags

    again = gate.send(account, ready.message, prepared=ready.prepared, key="the-key")
    assert (again.outcome, again.filed) == ("sent", False)
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_draft_without_a_message_id_gets_one_so_it_is_audited_and_filed(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    to = f"{user}-to@example.org"
    raw = f"From: {user}@example.test\r\nTo: {to}\r\nSubject: No id\r\n\r\nHello\r\n".encode()
    uid = seed(imap_server, user, "Drafts", raw, ["\\Draft"])
    account = direct(imap_server, user)
    gate = live(account)
    gate.send_draft(account, ref_to(imap_server, user, "Drafts", uid))
    [item] = [i for i in gate.items if i.delivery == "sent"]
    assert item.message_id
    assert item.message_id.endswith("@example.test>")
    assert parsed(smtp_server.delivered_to(to)[0].raw)["Message-ID"] == item.message_id
    assert item.message_id.encode() in folder_contents(imap_server, user, "Sent")[0].source


def test_a_draft_with_attachments_at_the_cap_is_sendable(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    to = f"{user}-to@example.org"
    limits = Limits(attachment_max_mb=1.0)
    account = direct(imap_server, user)
    gate = live(account, limits=limits)
    content = bytes(1024 * 1024)
    saved = save_draft(
        gate.mail,
        account,
        from_user(
            user, [to], attachments=(OutgoingAttachment("big.bin", "application/x-big", content),)
        ),
        cap=1024 * 1024,
        note=lambda _item: None,
    )
    assert saved.ref is not None
    gate.send_draft(account, saved.ref)
    assert next(parsed(smtp_server.delivered_to(to)[0].raw).iter_attachments()).get_content() == (
        content
    )


def test_a_draft_past_what_it_could_hold_is_refused_not_cut(
    live: Callable[..., Live], imap_server: ImapTestServer, user: str
) -> None:
    limits = Limits(attachment_max_mb=0.5)
    account = direct(imap_server, user)
    gate = live(account, limits=limits)
    body = b"x" * (MAX_DRAFT_BYTES + 1024 * 1024)
    raw = f"From: {user}@example.test\r\nTo: a@example.org\r\nSubject: Big\r\n\r\n".encode() + body
    uid = seed(imap_server, user, "Drafts", raw, ["\\Draft"])
    with pytest.raises(MailError) as raised:
        prepare_draft(gate.mail, account, ref_to(imap_server, user, "Drafts", uid), limits)
    assert raised.value.code == "too_large"


def test_a_draft_s_from_must_still_be_an_identity(
    live: Callable[..., Live], imap_server: ImapTestServer, user: str
) -> None:
    raw = b"From: boss@example.test\r\nTo: a@example.org\r\nSubject: s\r\n\r\nt\r\n"
    uid = seed(imap_server, user, "Drafts", raw, ["\\Draft"])
    account = direct(imap_server, user)
    gate = live(account)
    with pytest.raises(MailError, match="cannot send as that address"):
        gate.send_draft(account, ref_to(imap_server, user, "Drafts", uid))


def test_only_drafts_in_drafts_mode_and_in_approval_for_others(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    out = f"{user}-out@example.org"
    team = f"{user}@team.example"
    drafts = direct(imap_server, user, send_mode="drafts")
    gate = live(drafts)
    result = gate.send(drafts, from_user(user, [out], subject="s", text="t"))
    assert result.outcome == "drafted"
    assert result.ref is not None
    assert len(folder_contents(imap_server, user, "Drafts")) == 1

    other = imap_server.unique_user("send-team")
    with raw_client(imap_server, other):
        pass
    approval = test_account(
        "team", other, imap_server, send_mode="approval", auto_approve=["@team.example"]
    )
    approving = live(approval)
    message = from_user(other, [team], subject="s", text="t")
    assert approving.send(approval, message).outcome == "sent"
    wider = from_user(other, [team], cc=recipient_list([out]), subject="s", text="t")
    assert approving.send(approval, wider).outcome == "drafted"
    assert smtp_server.delivered_to(out) == []
    assert len(smtp_server.delivered_to(team)) == 1


def test_refuses_and_audits_as_refused_what_the_gate_does_not_allow(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    account = direct(imap_server, user)
    gate = live(account, limits=Limits(send_per_hour=1, max_recipients=2))
    to = f"{user}-x@example.org"
    with pytest.raises(MailError, match="over the limit of 2"):
        gate.send(account, from_user(user, ["a@x.org", "b@x.org", "c@x.org"]))
    with pytest.raises(MailError, match="cannot send as that address"):
        from_identity(account, "boss@example.test")
    assert gate.send(account, from_user(user, [to], subject="first")).outcome == "sent"
    with pytest.raises(MailError, match="hourly send limit"):
        gate.send(account, from_user(user, [to], subject="second"))
    assert [i.delivery for i in gate.items] == ["refused", "sent", "refused"]
    assert len(smtp_server.delivered_to(to)) == 1

    off = direct(imap_server, user, send_mode="off")
    blocked = f"{user}-blocked@example.org"
    with pytest.raises(MailError, match="send_mode off"):
        gate.send(off, from_user(user, [blocked]))
    assert gate.items[-1].result == "send_disabled"
    assert smtp_server.delivered_to(blocked) == []


def test_a_retried_key_reaches_the_recipient_once(
    live: Callable[..., Live],
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    user: str,
) -> None:
    to = f"{user}-to@example.org"
    account = direct(imap_server, user)
    gate = live(account)
    message = from_user(user, [to])
    first = gate.send(account, message, by="user", key="compose-1")
    again = gate.send(account, message, by="user", key="compose-1")
    assert again.send_id == first.send_id
    assert len(smtp_server.delivered_to(to)) == 1
    assert len(folder_contents(imap_server, user, "Sent")) == 1


def test_a_refused_recipient_comes_back_while_the_others_get_it(
    imap_server: ImapTestServer, user: str, core_engine: Engine
) -> None:
    """With the real connections for Sent and the stub for SMTP, end to end."""
    with SmtpStub(refuse=["bo@example.org"]) as stub:
        account = test_account("acc", user, imap_server, send_mode="direct", smtp=stub.smtp())
        mail = connections_for(imap_server, [account])
        try:
            deps = GateDeps(
                connections=mail,
                smtp=SmtpSender(
                    lambda _id: account, lambda _id: "x", ssl_context=SmtpStub.trusted_context()
                ),
                send_log=AccountStore(core_engine, "user-1"),
                limits=Limits(),
            )
            items: list[AuditItem] = []
            result = send_through_gate(
                deps,
                account,
                from_user(user, ["ana@example.org", "bo@example.org"]),
                items.append,
                by="agent",
            )
        finally:
            mail.close_all()
    assert result.rejected == ("bo@example.org",)
    assert items[0].rejected == ("bo@example.org",)
    assert len(folder_contents(imap_server, user, "Sent")) == 1
