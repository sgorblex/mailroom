"""`src/mail/drafts.ts` and the function-level cases of `draft-tools.it.test.ts` against the test
Dovecot, including what the TypeScript leaves to imapflow: reading an APPEND's raw response, and
what is left after each way a save can fail."""

import imaplib
import secrets
import socket
import threading
import uuid
from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from imapclient import IMAPClient

from mailroom_core.mail import (
    DRAFT_FLAGS,
    AttachmentSource,
    AuditItem,
    Mailbox,
    MailConnections,
    MailError,
    MessageInput,
    MessageRef,
    OutgoingAttachment,
    append_message,
    check_removable,
    compose,
    draft_source,
    find_drafts,
    format_ref,
    list_messages,
    original_message,
    parse_ref,
    read_attachment,
    read_draft,
    recipient_list,
    remove_draft,
    save_draft,
    try_remove_draft,
)
from mailroom_core.mail import (
    drafts as drafts_module,
)
from mailroom_core.mail import (
    threading as thread_of,
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

pytestmark = pytest.mark.integration

CAP = 5 * 1024 * 1024
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


@pytest.fixture
def user(imap_server: ImapTestServer) -> str:
    """A new user whose special folders exist, as CI's Dovecot makes them only on request."""
    name = imap_server.unique_user("draft")
    with raw_client(imap_server, name):
        return name


@pytest.fixture
def account(imap_server: ImapTestServer, user: str) -> AccountConfig:
    return test_account("acc", user, imap_server)


def message(account: AccountConfig, text: str = "Shall we meet?", **fields: object) -> MessageInput:
    values: dict[str, object] = {
        "sender": Mailbox(account.identities[0].address, "Me"),
        "to": recipient_list(["Ana <ana@example.org>"]),
        "subject": "Plans",
        **fields,
    }
    return MessageInput(text=text, **values)  # type: ignore[arg-type]


def save(
    mail: MailConnections,
    account: AccountConfig,
    input_: MessageInput | None = None,
    **options: object,
):
    items: list[AuditItem] = []
    saved = save_draft(
        mail, account, input_ or message(account), cap=CAP, note=items.append, **options
    )  # type: ignore[arg-type]
    return saved, items


def drafts_of(imap_server: ImapTestServer, user: str) -> list[bytes]:
    return [found.source for found in folder_contents(imap_server, user, "Drafts")]


def refs_in(mail: MailConnections, folder: str = "Drafts") -> list[MessageRef]:
    return [parse_ref(m.ref) for m in list_messages(mail, "acc", folder, previews=False).messages]


def drop_response_code(monkeypatch: pytest.MonkeyPatch, code: bytes = b"Append completed.") -> None:
    """As a server without UIDPLUS answers."""
    append = IMAPClient.append

    def plain(self: IMAPClient, *args: object) -> bytes:
        append(self, *args)
        return code

    monkeypatch.setattr(IMAPClient, "append", plain)


def test_takes_the_uid_from_a_real_appenduid(imap_server, harness, user, account) -> None:
    seed(imap_server, user, "Drafts", mime("first"))
    mail = harness(account)
    composed = compose(message(account, draft=True))
    ref = append_message(mail, "acc", "Drafts", composed.stored, DRAFT_FLAGS, composed.message_id)

    [_first, stored] = folder_contents(imap_server, user, "Drafts")
    assert ref is not None
    assert (ref.account, ref.folder, ref.uid) == ("acc", "Drafts", stored.uid)
    assert refs_in(mail)[0] == ref


def test_without_appenduid_a_search_by_message_id_finds_it(
    imap_server, harness, user, account, monkeypatch
) -> None:
    mail = harness(account)
    drop_response_code(monkeypatch)
    first, _ = save(mail, account, compose_id="same-id")
    second, _ = save(mail, account, compose_id="same-id")

    assert first.ref is not None
    assert second.ref is not None
    # The newest of the two sharing a Message-ID.
    assert second.ref.uid > first.ref.uid
    assert second.ref == refs_in(mail)[0]


def test_an_appenduid_of_another_message_is_not_believed(
    imap_server, harness, user, account, monkeypatch
) -> None:
    other = seed(imap_server, user, "Drafts", mime("someone else's"))
    mail = harness(account)
    validity = refs_in(mail)[0].uid_validity
    drop_response_code(monkeypatch, f"[APPENDUID {validity} {other}] Append completed.".encode())
    saved, _ = save(mail, account)

    assert saved.ref is not None
    assert saved.ref.uid != other
    assert saved.ref == refs_in(mail)[0]


def test_a_failed_read_back_gives_no_ref_and_keeps_the_old_version(
    imap_server, harness, user, account, monkeypatch
) -> None:
    mail = harness(account)
    old, _ = save(mail, account, message(account, "v1"), compose_id="same-id")
    drop_response_code(monkeypatch)

    def fails(self: IMAPClient, *args: object) -> None:
        raise imaplib.IMAP4.error("search failed")

    monkeypatch.setattr(IMAPClient, "search", fails)
    new, items = save(mail, account, message(account, "v2"), compose_id="same-id", replaces=old.ref)
    monkeypatch.undo()

    assert new.ref is None
    assert new.parts == ()
    assert new.replaced is None
    assert [item.result for item in items] == ["ok"]
    assert len(drafts_of(imap_server, user)) == 2
    assert find_drafts(mail, "acc", "same-id")[1] == old.ref


def test_a_search_that_only_finds_the_old_version_does_not_replace_it(
    imap_server, harness, user, account, monkeypatch
) -> None:
    """A lagging full-text index: taking the old version for the new one would return a ref to a
    message this very save then removes."""
    mail = harness(account)
    old, _ = save(mail, account, message(account, "v1"), compose_id="same-id")
    assert old.ref is not None
    drop_response_code(monkeypatch)
    monkeypatch.setattr(IMAPClient, "search", lambda self, criteria: [old.ref.uid])
    new, _ = save(mail, account, message(account, "v2"), compose_id="same-id", replaces=old.ref)
    monkeypatch.undo()

    assert new.ref is None
    assert new.replaced is None
    assert len(drafts_of(imap_server, user)) == 2


def test_keeps_the_flags_of_drafts_saved_after_drafts_was_listed(
    imap_server, harness, user, account
) -> None:
    mail = harness(account)
    assert refs_in(mail) == []  # EXAMINE on the connection the saves then use

    for subject in ("first", "second"):
        saved, _ = save(mail, account, message(account, subject=subject))
        assert saved.ref is not None
    drafts = folder_contents(imap_server, user, "Drafts")
    assert [sorted(draft.flags) for draft in drafts] == [["\\Draft", "\\Seen"]] * 2


def test_saves_a_new_draft_marked_as_mailrooms(imap_server, harness, user, account) -> None:
    mail = harness(account)
    saved, items = save(
        mail,
        account,
        message(
            account,
            "Shall we meet?",
            bcc=recipient_list(["hidden@example.org"]),
            subject="Plans\r\nBcc: victim@example.org",
        ),
    )

    assert saved.folder == "Drafts"
    assert saved.autosave is True
    assert saved.replaced is None
    assert saved.parts == ()
    [draft] = folder_contents(imap_server, user, "Drafts")
    assert b"X-Mailroom-Draft: 1" in draft.source
    assert b"Bcc: hidden@example.org" in draft.source
    assert draft.source.lower().count(b"\nbcc:") == 1
    assert draft.subject == "Plans Bcc: victim@example.org"

    [item] = items
    assert item.result == "ok"
    assert item.folder == "Drafts"
    assert item.message_id == saved.message_id
    assert item.recipients is not None
    assert item.recipients.to == ("ana@example.org",)
    assert item.recipients.bcc == ("hidden@example.org",)
    assert "Plans" not in str(item.record)


def test_bcc_threading_and_answers_round_trip(imap_server, harness, user, account) -> None:
    seed(
        imap_server,
        user,
        "INBOX",
        mime(
            "Dinner",
            sender="Ana <ana@example.org>",
            message_id="<dinner@example.org>",
            references="<root@example.org>",
            text="Friday at 8?",
        ),
    )
    mail = harness(account)
    original = parse_ref(list_messages(mail, "acc", "INBOX", previews=False).messages[0].ref)
    thread = thread_of(original_message(mail, original))
    saved, _ = save(
        mail,
        account,
        message(
            account,
            "Yes.\r\n",
            subject="Re: Dinner",
            cc=recipient_list(["Bo <bo@example.org>"]),
            bcc=recipient_list(["Hidden <hidden@example.org>"]),
            in_reply_to=thread.in_reply_to,
            references=thread.references,
            answers=format_ref(original),
        ),
        compose_id="6f1c0a2e-0000-4000-8000-000000000001",
    )
    assert saved.ref is not None

    draft = read_draft(mail, saved.ref)
    assert draft.own is True
    assert draft.compose_id == "6f1c0a2e-0000-4000-8000-000000000001"
    assert draft.message_id == saved.message_id
    assert draft.sender == (Mailbox(account.identities[0].address, "Me"),)
    assert draft.to == (Mailbox("ana@example.org", "Ana"),)
    assert draft.cc == (Mailbox("bo@example.org", "Bo"),)
    assert draft.bcc == (Mailbox("hidden@example.org", "Hidden"),)
    assert draft.subject == "Re: Dinner"
    assert draft.in_reply_to == "<dinner@example.org>"
    assert draft.references == ("<root@example.org>", "<dinner@example.org>")
    assert draft.answers == original
    assert draft.text.rstrip("\n") == "Yes."
    assert draft.lossy is False


def test_answers_in_another_account_does_not_count(imap_server, harness, user, account) -> None:
    elsewhere = format_ref(MessageRef("other", "INBOX", 1, 1))
    raw = mime("by hand").replace(
        "MIME-Version", f"X-Mailroom-Draft: 1; answers={elsewhere}\r\nMIME-Version", 1
    )
    seed(imap_server, user, "Drafts", raw)
    mail = harness(account)
    [ref] = refs_in(mail)

    assert read_draft(mail, ref).answers is None
    with pytest.raises(MailError, match="another account"):
        save(mail, account, message(account, answers=elsewhere))


def test_a_new_version_replaces_the_old_keeping_its_attachments(
    imap_server, harness, user, account
) -> None:
    seed(
        imap_server,
        user,
        "INBOX",
        mime("files", attachments=[MimeAttachment("a.pdf", "application/pdf", PDF)]),
    )
    mail = harness(account)
    with_file = parse_ref(list_messages(mail, "acc", "INBOX", previews=False).messages[0].ref)
    first, _ = save(
        mail,
        account,
        message(account, "first"),
        compose_id="same-id",
        keep=[AttachmentSource(with_file, "2")],
    )
    assert first.ref is not None
    assert [(p.part, p.name, p.type) for p in first.parts] == [("2", "a.pdf", "application/pdf")]

    second, items = save(
        mail,
        account,
        message(account, "second"),
        compose_id="same-id",
        replaces=first.ref,
        keep=[AttachmentSource(first.ref, part.part) for part in first.parts],
    )

    assert second.ref is not None
    assert second.replaced == "expunged"
    assert second.message_id == first.message_id
    assert [(p.part, p.name) for p in second.parts] == [("2", "a.pdf")]
    assert read_attachment(mail, second.ref, "2", max_bytes=CAP).data == PDF
    [draft] = drafts_of(imap_server, user)
    assert b"second" in draft
    with pytest.raises(MailError) as gone:
        read_draft(mail, first.ref)
    assert gone.value.code == "not_found"
    assert [(item.result, item.removed) for item in items] == [("ok", None), ("ok", "expunged")]
    # What was expunged stays identifiable in the audit, without its content.
    assert items[1].message_id == first.message_id
    assert items[1].uid == first.ref.uid
    assert items[0].subject_sha256 is not None


def test_uploads_and_kept_parts_share_the_cap(imap_server, harness, user, account) -> None:
    seed(
        imap_server,
        user,
        "INBOX",
        mime("files", attachments=[MimeAttachment("a.pdf", "application/pdf", PDF)]),
    )
    mail = harness(account)
    source = parse_ref(list_messages(mail, "acc", "INBOX", previews=False).messages[0].ref)
    upload = OutgoingAttachment("b.bin", "application/octet-stream", b"x" * 40)
    items: list[AuditItem] = []
    with pytest.raises(MailError, match="may total 60 B") as raised:
        save_draft(
            mail,
            account,
            message(account, attachments=(upload,)),
            cap=60,
            note=items.append,
            keep=[AttachmentSource(source, "2")],
        )
    assert raised.value.code == "too_large"
    assert drafts_of(imap_server, user) == []
    assert items == []

    saved, _ = save(
        mail,
        account,
        message(account, attachments=(upload,)),
        keep=[AttachmentSource(source, "2")],
    )
    assert [p.name for p in saved.parts] == ["a.pdf", "b.bin"]


def test_removes_only_that_uid_and_sends_a_foreign_draft_to_trash(
    imap_server, harness, user, account
) -> None:
    seed(imap_server, user, "Drafts", mime("mine, by hand"), ["\\Draft"])
    seed(imap_server, user, "Drafts", mime("deleted elsewhere"), ["\\Deleted"])
    mail = harness(account)
    # Newest first: the second is the hand-written draft.
    hand = refs_in(mail)[1]
    items: list[AuditItem] = []
    assert remove_draft(mail, hand, items.append) == "trashed"
    assert [(item.result, item.removed) for item in items] == [("ok", "trashed")]

    saved, _ = save(mail, account, message(account, "tmp", subject="tmp"))
    assert saved.ref is not None
    items.clear()
    assert remove_draft(mail, saved.ref, items.append) == "expunged"
    assert [(item.result, item.removed) for item in items] == [("ok", "expunged")]

    left = folder_contents(imap_server, user, "Drafts")
    assert [m.subject for m in left] == ["deleted elsewhere"]
    assert "\\Deleted" in left[0].flags
    assert [m.subject for m in folder_contents(imap_server, user, "Trash")] == ["mine, by hand"]


def test_replacing_a_foreign_draft_moves_it_to_trash(imap_server, harness, user, account) -> None:
    seed(imap_server, user, "Drafts", mime("by hand", text="my words"), ["\\Draft"])
    mail = harness(account)
    [ref] = refs_in(mail)
    draft = read_draft(mail, ref)
    assert draft.own is False
    assert draft.compose_id is None
    assert draft.text == "my words"

    saved, items = save(mail, account, message(account, "edited"), replaces=ref)

    assert saved.replaced == "trashed"
    assert [item.removed for item in items] == [None, "trashed"]
    assert [m.subject for m in folder_contents(imap_server, user, "Trash")] == ["by hand"]
    assert len(drafts_of(imap_server, user)) == 1


def test_html_reads_as_text_and_as_lossy(imap_server, harness, user, account) -> None:
    seed(imap_server, user, "Drafts", mime("rich", html="<p>Hello <b>there</b></p>"), ["\\Draft"])
    mail = harness(account)
    draft = read_draft(mail, refs_in(mail)[0])

    assert "Hello" in draft.text
    assert "<b>" not in draft.text
    assert draft.lossy is True


def test_a_save_whose_removal_fails_still_stands(
    imap_server, harness, user, account, monkeypatch
) -> None:
    mail = harness(account)
    old, _ = save(mail, account, message(account, "v1"), compose_id="same-id")
    assert old.ref is not None

    def fails(self: IMAPClient, *args: object) -> None:
        raise imaplib.IMAP4.error("no")

    monkeypatch.setattr(IMAPClient, "uid_expunge", fails)
    new, items = save(mail, account, message(account, "v2"), compose_id="same-id", replaces=old.ref)
    monkeypatch.undo()

    assert new.ref is not None
    assert new.replaced == "failed"
    assert [(item.result, item.removed) for item in items] == [
        ("ok", None),
        ("server_error", "failed"),
    ]
    assert read_draft(mail, new.ref).text.startswith("v2")
    assert len(drafts_of(imap_server, user)) == 2


def test_a_failed_move_to_trash_is_tagged_as_failed(
    imap_server, harness, user, account, monkeypatch
) -> None:
    seed(imap_server, user, "Drafts", mime("by hand"), ["\\Draft"])
    mail = harness(account)
    [ref] = refs_in(mail)

    def fails(self: IMAPClient, *args: object) -> None:
        raise imaplib.IMAP4.error("no")

    monkeypatch.setattr(IMAPClient, "move", fails)
    items: list[AuditItem] = []
    with pytest.raises(MailError):
        remove_draft(mail, ref, items.append)
    assert [(item.result, item.removed) for item in items] == [("server_error", "failed")]
    assert len(drafts_of(imap_server, user)) == 1


def test_a_draft_gone_before_its_move_to_trash_is_tagged_as_failed(
    imap_server, harness, user, account, monkeypatch
) -> None:
    seed(imap_server, user, "Drafts", mime("by hand"), ["\\Draft"])
    mail = harness(account)
    [ref] = refs_in(mail)
    trash_it = drafts_module.trash_messages

    def deleted_elsewhere_first(connections: MailConnections, *args: object, **options: object):
        with raw_client(imap_server, user) as client:
            client.select_folder("Drafts")
            client.delete_messages([ref.uid])
            client.expunge()
        return trash_it(connections, *args, **options)

    monkeypatch.setattr(drafts_module, "trash_messages", deleted_elsewhere_first)
    items: list[AuditItem] = []
    with pytest.raises(MailError) as raised:
        remove_draft(mail, ref, items.append)

    assert raised.value.code == "not_found"
    assert [(item.uid, item.result, item.removed) for item in items] == [
        (ref.uid, "not_found", "failed")
    ]


def test_a_removal_that_never_started_is_tagged_as_failed(imap_server, harness, account) -> None:
    mail = harness(account)
    saved, _ = save(mail, account)
    assert saved.ref is not None
    stale = replace(saved.ref, uid_validity=1)

    items: list[AuditItem] = []
    with pytest.raises(MailError):
        remove_draft(mail, stale, items.append)
    assert [(item.result, item.removed) for item in items] == [("stale_reference", "failed")]

    items.clear()
    assert try_remove_draft(mail, stale, items.append) == "failed"
    assert [(item.result, item.removed) for item in items] == [("stale_reference", "failed")]


def test_try_remove_draft_answers_how_it_went(imap_server, harness, user, account) -> None:
    mail = harness(account)
    saved, _ = save(mail, account)
    assert saved.ref is not None
    assert try_remove_draft(mail, saved.ref, lambda _item: None) == "expunged"
    assert drafts_of(imap_server, user) == []


def test_a_bug_in_a_removal_fails_nothing_but_is_on_record(
    harness, account, monkeypatch, caplog
) -> None:
    mail = harness(account)
    saved, _ = save(mail, account)
    assert saved.ref is not None

    def broken(self: MailConnections, *args: object) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(MailConnections, "special_folder", broken)
    items: list[AuditItem] = []
    assert try_remove_draft(mail, saved.ref, items.append) == "failed"
    assert [(item.result, item.removed) for item in items] == [("internal", "failed")]
    assert "imap_error" in caplog.text


def test_an_uploaded_invite_stays_through_the_next_saves(
    imap_server, harness, user, account
) -> None:
    content = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
    invite = OutgoingAttachment("invite.ics", "text/calendar", content)
    mail = harness(account)

    first, _ = save(
        mail, account, message(account, "v1", attachments=(invite,)), compose_id="same-id"
    )
    assert first.ref is not None
    assert [p.name for p in first.parts] == ["invite.ics"]

    second, _ = save(
        mail,
        account,
        message(account, "v2"),
        compose_id="same-id",
        replaces=first.ref,
        keep=[AttachmentSource(first.ref, part.part) for part in first.parts],
    )

    assert second.replaced == "expunged"
    assert second.ref is not None
    [part] = second.parts
    draft = read_draft(mail, second.ref)
    assert draft.lossy is False
    assert [p.name for p in draft.attachments] == ["invite.ics"]
    assert read_attachment(mail, second.ref, part.part, max_bytes=CAP).data == content
    assert len(drafts_of(imap_server, user)) == 1


def test_an_invite_with_no_name_is_a_body_a_save_would_lose(
    imap_server, harness, user, account
) -> None:
    raw = (
        b"From: me@example.org\r\nSubject: Invite\r\nMIME-Version: 1.0\r\n"
        b'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
        b"--b\r\nContent-Type: text/plain\r\n\r\nCome\r\n"
        b"--b\r\nContent-Type: text/calendar\r\n\r\nBEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        b"--b--\r\n"
    )
    seed(imap_server, user, "Drafts", raw, ["\\Draft"])
    mail = harness(account)

    draft = read_draft(mail, refs_in(mail)[0])

    assert draft.lossy is True
    assert draft.attachments == ()


def test_finds_every_version_newest_first_whatever_the_identity(
    imap_server, harness, user, account
) -> None:
    mail = harness(account)
    other = message(account, "v2", sender=Mailbox("alias@elsewhere.example", None))
    compose_id = str(uuid.uuid4())
    first, _ = save(mail, account, message(account, "v1"), compose_id=compose_id)
    second, _ = save(mail, account, other, compose_id=compose_id)
    # Dovecot's search goes by words here: `<same@` matches this one too.
    save(mail, account, message(account, "another draft"), compose_id=f"{compose_id}-2")

    assert first.ref is not None
    assert second.ref is not None
    assert first.message_id != second.message_id
    assert find_drafts(mail, "acc", compose_id) == (second.ref, first.ref)
    assert find_drafts(mail, "acc", str(uuid.uuid4())) == ()


def test_a_save_whose_answer_was_lost_is_found_again(
    imap_server, harness, user, account, monkeypatch
) -> None:
    mail = harness(account)
    old, _ = save(mail, account, message(account, "v1"), compose_id="same-id")
    append = IMAPClient.append

    def lost(self: IMAPClient, *args: object) -> None:
        append(self, *args)
        raise imaplib.IMAP4.abort("connection lost")

    monkeypatch.setattr(IMAPClient, "append", lost)
    with pytest.raises(MailError) as raised:
        save(mail, account, message(account, "v2"), compose_id="same-id", replaces=old.ref)
    monkeypatch.undo()

    assert raised.value.code == "unreachable"
    [newest, oldest] = find_drafts(mail, "acc", "same-id")
    assert oldest == old.ref
    assert read_draft(mail, newest).text.startswith("v2")


class CuttingProxy:
    """Relays TCP to a server. With `budget` set, the connection is cut once that many more bytes
    have gone up it, so a command dies halfway; TLS passes through untouched."""

    def __init__(self, host: str, port: int) -> None:
        self.target = (host, port)
        self.budget: int | None = None
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port: int = self.listener.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                downstream, _ = self.listener.accept()
            except OSError:
                return
            upstream = socket.create_connection(self.target)
            threading.Thread(
                target=self._relay, args=(downstream, upstream, True), daemon=True
            ).start()
            threading.Thread(
                target=self._relay, args=(upstream, downstream, False), daemon=True
            ).start()

    def _relay(self, source: socket.socket, sink: socket.socket, counted: bool) -> None:
        with suppress(OSError):
            while chunk := source.recv(65536):
                if counted and self.budget is not None:
                    self.budget -= len(chunk)
                    if self.budget <= 0:
                        break
                sink.sendall(chunk)
        for end in (source, sink):
            with suppress(OSError):
                end.shutdown(socket.SHUT_RDWR)
            end.close()

    def close(self) -> None:
        self.listener.close()


def test_an_append_cut_mid_literal_leaves_the_old_version_alone(imap_server, harness, user) -> None:
    proxy = CuttingProxy(imap_server.host, imap_server.port)
    account = test_account(
        "acc",
        user,
        imap_server,
        imap={"host": "127.0.0.1", "port": proxy.port, "tls": "implicit"},
    )
    mail = harness(account)
    old, _ = save(mail, account, message(account, "v1"), compose_id="same-id")
    assert old.ref is not None
    big = OutgoingAttachment(
        "big.bin", "application/octet-stream", secrets.token_bytes(3 * 1024 * 1024)
    )
    new = message(account, "v2", attachments=(big,))

    # Past the commands around the APPEND, well short of its literal.
    proxy.budget = 256 * 1024
    with pytest.raises(MailError) as raised:
        save(mail, account, new, compose_id="same-id", replaces=old.ref)
    assert raised.value.code in ("unreachable", "timeout")
    proxy.budget = None

    [kept] = drafts_of(imap_server, user)
    assert b"v1" in kept
    assert read_draft(mail, old.ref).text.startswith("v1")

    # The next save, on a new connection, goes through as if nothing happened.
    saved, _ = save(mail, account, new, compose_id="same-id", replaces=old.ref)
    assert saved.replaced == "expunged"
    [stored] = drafts_of(imap_server, user)
    assert b"v2" in stored
    proxy.close()


def test_refuses_what_a_save_or_removal_must_not_touch(imap_server, harness, user, account) -> None:
    seed(imap_server, user, "INBOX", mime("not a draft"))
    mail = harness(account)
    inbox = parse_ref(list_messages(mail, "acc", "INBOX", previews=False).messages[0].ref)
    saved, _ = save(mail, account)
    assert saved.ref is not None

    for attempt in (
        lambda: remove_draft(mail, inbox, lambda _item: None),
        lambda: read_draft(mail, inbox),
        lambda: draft_source(mail, inbox, CAP),
        lambda: check_removable(mail, inbox),
        lambda: save(mail, account, replaces=inbox),
    ):
        with pytest.raises(MailError, match="not in Drafts"):
            attempt()

    stale = replace(saved.ref, uid_validity=1)
    with pytest.raises(MailError) as raised:
        save(mail, account, replaces=stale)
    assert raised.value.code == "stale_reference"
    missing = replace(saved.ref, uid=saved.ref.uid + 100)
    with pytest.raises(MailError) as raised:
        save(mail, account, replaces=missing)
    assert raised.value.code == "not_found"
    with pytest.raises(MailError, match="another account"):
        save(mail, account, replaces=replace(saved.ref, account="other"))

    off = account.model_copy(update={"send_mode": "off"})
    with pytest.raises(MailError) as raised:
        save(mail, off)
    assert raised.value.code == "send_disabled"
    assert len(drafts_of(imap_server, user)) == 1
    assert len(folder_contents(imap_server, user, "INBOX")) == 1


@pytest.mark.parametrize(
    "attempt",
    [
        lambda mail, ref: remove_draft(mail, ref, lambda _item: None),
        read_draft,
        lambda mail, ref: draft_source(mail, ref, CAP),
    ],
    ids=["remove_draft", "read_draft", "draft_source"],
)
def test_a_stale_ref_fails_and_the_draft_stays(
    imap_server, harness, user, account, attempt
) -> None:
    mail = harness(account)
    saved, _ = save(mail, account)
    assert saved.ref is not None

    with pytest.raises(MailError) as raised:
        attempt(mail, replace(saved.ref, uid_validity=1))

    assert raised.value.code == "stale_reference"
    assert len(drafts_of(imap_server, user)) == 1


def test_a_foreign_draft_with_no_trash_to_go_to_is_not_replaced(
    imap_server, harness, user, account, monkeypatch
) -> None:
    seed(imap_server, user, "Drafts", mime("by hand"), ["\\Draft"])
    mail = harness(account)
    [ref] = refs_in(mail)
    folders = MailConnections.folders
    monkeypatch.setattr(
        MailConnections,
        "folders",
        lambda self, account_id: [f for f in folders(self, account_id) if f.role != "trash"],
    )

    with pytest.raises(MailError) as raised:
        save(mail, account, replaces=ref)

    assert raised.value.code == "unsupported"
    assert len(drafts_of(imap_server, user)) == 1


def test_a_server_that_cannot_move_a_foreign_draft_gets_no_new_version(
    imap_server, harness, user, account, monkeypatch
) -> None:
    seed(imap_server, user, "Drafts", mime("by hand"), ["\\Draft"])
    mail = harness(account)
    [ref] = refs_in(mail)
    capabilities = mail.capabilities("acc")
    monkeypatch.setattr(
        MailConnections,
        "capabilities",
        lambda self, _id: replace(capabilities, move=False, uidplus=False),
    )

    with pytest.raises(MailError) as raised:
        save(mail, account, replaces=ref)

    assert raised.value.code == "unsupported"
    assert len(drafts_of(imap_server, user)) == 1


def test_a_draft_comes_back_exactly_as_stored_and_never_cut(
    imap_server, harness, user, account
) -> None:
    mail = harness(account)
    saved, _ = save(mail, account)
    assert saved.ref is not None

    [stored] = drafts_of(imap_server, user)
    assert draft_source(mail, saved.ref, CAP) == stored
    assert draft_source(mail, saved.ref, len(stored)) == stored
    with pytest.raises(MailError, match="over the attachment size limit") as raised:
        draft_source(mail, saved.ref, len(stored) - 1)
    assert raised.value.code == "too_large"


def test_without_uidplus_versions_go_to_trash_and_autosave_is_off(
    imap_server, harness, user, account, monkeypatch
) -> None:
    mail = harness(account)
    capabilities = mail.capabilities("acc")
    monkeypatch.setattr(
        MailConnections, "capabilities", lambda self, _id: replace(capabilities, uidplus=False)
    )
    has = IMAPClient.has_capability
    monkeypatch.setattr(
        IMAPClient, "has_capability", lambda self, name: name != "UIDPLUS" and has(self, name)
    )

    first, _ = save(mail, account, message(account, "v1", subject="v1"), compose_id="same-id")
    assert first.autosave is False
    second, _ = save(
        mail,
        account,
        message(account, "v2", subject="v2"),
        compose_id="same-id",
        replaces=first.ref,
    )

    assert second.replaced == "trashed"
    assert second.autosave is False
    assert [m.subject for m in folder_contents(imap_server, user, "Trash")] == ["v1"]
    assert [m.subject for m in folder_contents(imap_server, user, "Drafts")] == ["v2"]
