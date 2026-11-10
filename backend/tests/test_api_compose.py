"""Drafts and sends over the REST routes (``docs/requirements/mail-client.md``, *Slice C*) against
real Dovecot and Mailpit (``IMAP_TEST_*``, ``SMTP_TEST_*``, ``MAILPIT_TEST_API``), or a scripted
SMTP stub where the server has to misbehave; skipped without them. Each test logs in as a fresh
mailbox user and sends to addresses nobody else uses, so the Mailpit counts are its own."""

from __future__ import annotations

import inspect
import json
import logging
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from typing import TYPE_CHECKING, Any, cast

import pytest
from starlette.datastructures import UploadFile

from mailroom.accounts.mail import mail_ssl_context
from mailroom.accounts.store import load_accounts, user_store
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.context import app_context
from mailroom.mail import service
from mailroom.mail.pool import USER_UPLOADS
from mailroom.mail.sends import SendRunner
from mailroom_core import send as gate
from mailroom_core.mail import MailError, MessageRef, format_ref, parse_ref
from mailroom_core.store import SEND_CEILING
from mailroom_core.testing import (
    HANG_UP,
    MimeAttachment,
    SmtpStub,
    SmtpTestServer,
    create_folders,
    folder_contents,
    mime,
    raw_client,
    seed,
)
from tests.conftest import PASSWORD
from tests.test_api_accounts import new_account

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from email.message import EmailMessage

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import AuditRecord, User
    from mailroom_core.testing import ImapTestServer, RawMessage

MAIL = "/api/v1/mail"
ME = "me@example.org"
ALIAS = "alias@example.org"
PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"

pytestmark = pytest.mark.integration


def address(address: str, name: str = "") -> dict[str, str]:
    return {"name": name, "address": address}


def stranger() -> str:
    return f"to-{uuid.uuid4().hex}@example.org"


def detail(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["detail"]["code"]


def parsed(raw: bytes) -> EmailMessage:
    return cast("EmailMessage", message_from_bytes(raw, policy=policy.default))


class Composer:
    """The signed-in user's account ``work``, on a fresh Dovecot user with Drafts, Sent and
    Trash."""

    def __init__(
        self, client: TestClient, server: ImapTestServer, login: str, application: FastAPI
    ) -> None:
        self.client = client
        self.server = server
        self.login = login
        self.application = application

    def seed(self, message: str, folder: str = "INBOX", flags: tuple[str, ...] = ()) -> str:
        """Seeds a message; its ref."""
        uid = seed(self.server, self.login, folder, message, flags)
        with raw_client(self.server, self.login) as raw:
            validity = raw.select_folder(folder, readonly=True)[b"UIDVALIDITY"]
        return format_ref(MessageRef("work", folder, validity, uid))

    def contents(self, folder: str) -> list[RawMessage]:
        return folder_contents(self.server, self.login, folder)

    def post_draft(self, *, files: tuple[tuple[str, bytes, str], ...] = (), **fields: Any) -> Any:
        draft = {"account": "work", "compose_id": str(uuid.uuid4()), **fields}
        parts: list[Any] = [("draft", (None, json.dumps(draft)))]
        parts += [("files[]", file) for file in files]
        return self.client.post(f"{MAIL}/drafts", files=parts)

    def save(self, **fields: Any) -> Any:
        response = self.post_draft(**fields)
        assert response.status_code == 201, response.text
        assert response.headers["cache-control"] == "private, no-store"
        return response.json()

    def read(self, ref: str) -> Any:
        response = self.client.get(f"{MAIL}/drafts/{ref}")
        assert response.status_code == 200, response.text
        return response.json()

    def post_send(self, ref: str, **body: Any) -> Any:
        return self.client.post(f"{MAIL}/drafts/{ref}/send", json=body)

    def settled(self) -> None:
        """Waits for the runner's sends, filing and draft removal included."""
        runner: SendRunner = self.application.state.send_runner
        deadline = time.monotonic() + 30
        while runner.held.total() > 0:
            assert time.monotonic() < deadline, "a send is still running"
            time.sleep(0.02)

    def sent(self, ref: str, **body: Any) -> Any:
        """Sends ``ref``, waits for all of it; the send's result."""
        response = self.post_send(ref, **body)
        assert response.status_code == 202, response.text
        self.settled()
        return self.follow(response.json()["send_id"])

    def follow(self, send_id: str) -> Any:
        response = self.client.get(f"{MAIL}/sends/{send_id}")
        assert response.status_code == 200, response.text
        return response.json()

    def set_limits(self, engine: Engine, user_id: int, **limits: Any) -> None:
        store = user_store(engine, user_id)
        current = load_accounts(store)
        store.save_settings({**current.doc, "limits": limits}, current.revision)


def foreign_draft(composer: Composer, to: str) -> str:
    """A draft another client wrote, so with no ``compose_id``: its ref."""
    return composer.seed(
        mime("Elsewhere", sender=f"Me <{ME}>", to=to, text="Hello"), "Drafts", ("\\Draft",)
    )


def smtp_endpoint(server: SmtpTestServer) -> dict[str, Any]:
    return {"host": server.host, "port": server.port, "tls": "implicit"}


def imap_endpoint(server: ImapTestServer) -> dict[str, Any]:
    return {"host": server.host, "port": server.port, "tls": "implicit"}


def add_account(composer: Composer, account_id: str = "work", **changes: Any) -> None:
    created = composer.client.post(
        "/api/v1/accounts", json=account_fields(composer, account_id) | changes
    )
    assert created.status_code == 201, created.text


def account_fields(composer: Composer, account_id: str = "work") -> dict[str, Any]:
    return new_account(
        account_id,
        login=composer.login,
        password=composer.server.password,
        send_mode="direct",
        imap=imap_endpoint(composer.server),
        identities=[
            {"address": ME, "name": "Me", "default": True},
            {"address": ALIAS, "name": "Alias"},
        ],
    )


@pytest.fixture
def composer(
    user_client: TestClient,
    imap_server: ImapTestServer,
    smtp_server: SmtpTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> Composer:
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_ssl_context())
    login = imap_server.unique_user("compose")
    create_folders(imap_server, login, "Drafts", "Sent", "Trash")
    mine = Composer(user_client, imap_server, login, application)
    add_account(mine, smtp=smtp_endpoint(smtp_server))
    return mine


@pytest.fixture
def stubbed(composer: Composer, application: FastAPI, monkeypatch: pytest.MonkeyPatch) -> Composer:
    """``composer`` with a TLS context that trusts the SMTP stub too; IMAP keeps working."""
    context = mail_ssl_context()
    for certificate in SmtpStub.trusted_context().get_ca_certs(binary_form=True):
        context.load_verify_locations(cadata=certificate)
    monkeypatch.setattr(application.state, "mail_ssl_context", context)
    return composer


def point_at(composer: Composer, stub: SmtpStub) -> None:
    fields = account_fields(composer)
    fields.pop("id")
    fields.pop("password")
    changed = composer.client.put("/api/v1/accounts/work", json=fields | {"smtp": stub.smtp()})
    assert changed.status_code == 200, changed.text


def test_signed_out_browsers_are_refused_uncached(api_client: TestClient) -> None:
    refused = [
        api_client.post(f"{MAIL}/drafts/prefill", json={"ref": "x", "mode": "reply"}),
        api_client.post(f"{MAIL}/drafts", files=[("draft", (None, "{}"))]),
        api_client.get(f"{MAIL}/drafts/x"),
        api_client.get(f"{MAIL}/drafts/find", params={"account": "a", "compose_id": "b"}),
        api_client.delete(f"{MAIL}/drafts/x"),
        api_client.post(f"{MAIL}/drafts/x/send", json={}),
        api_client.get(f"{MAIL}/sends/1"),
    ]
    for response in refused:
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"


def test_cross_site_writes_are_refused(composer: Composer) -> None:
    ref = composer.save(subject="Mine")["ref"]
    cross_site = {"Sec-Fetch-Site": "cross-site"}
    refused = [
        composer.client.post(f"{MAIL}/drafts", files=[("draft", (None, "{}"))], headers=cross_site),
        composer.client.post(
            f"{MAIL}/drafts/prefill", json={"ref": ref, "mode": "reply"}, headers=cross_site
        ),
        composer.client.delete(f"{MAIL}/drafts/{ref}", headers=cross_site),
        composer.client.post(f"{MAIL}/drafts/{ref}/send", json={}, headers=cross_site),
    ]
    for response in refused:
        assert response.status_code == 403
        assert response.headers["cache-control"] == "private, no-store"
    assert len(composer.contents("Drafts")) == 1


def test_a_reply_starts_from_its_original_and_writes_nothing(composer: Composer) -> None:
    ref = composer.seed(
        mime(
            "Notes",
            sender="Ada <ada@example.org>",
            to=f"Alias <{ALIAS}>, Bob <bob@example.org>",
            message_id="<one@example.org>",
            text="Hello\nthere",
        )
    )
    answer = composer.client.post(f"{MAIL}/drafts/prefill", json={"ref": ref, "mode": "reply"})
    assert answer.status_code == 200, answer.text
    started = answer.json()
    assert started["account"] == "work"
    assert started["from"] == ALIAS
    assert started["to"] == [address("ada@example.org", "Ada")]
    assert started["cc"] == started["bcc"] == started["attachments"] == []
    assert started["subject"] == "Re: Notes"
    assert started["answers"] == ref
    assert started["text"].startswith("\n\nOn ")
    assert "> Hello\n> there" in started["text"]

    everyone = composer.client.post(
        f"{MAIL}/drafts/prefill", json={"ref": ref, "mode": "reply_all"}
    ).json()
    assert [mailbox["address"] for mailbox in everyone["to"]] == [
        "ada@example.org",
        "bob@example.org",
    ]
    assert composer.contents("Drafts") == []


def test_a_forward_offers_the_attachments(composer: Composer) -> None:
    ref = composer.seed(
        mime(
            "Notes",
            text="Hello",
            attachments=[MimeAttachment("notes.pdf", "application/pdf", PDF)],
        )
    )
    started = composer.client.post(
        f"{MAIL}/drafts/prefill", json={"ref": ref, "mode": "forward"}
    ).json()
    assert started["from"] == ME
    assert started["to"] == []
    assert started["answers"] is None
    assert started["subject"] == "Fwd: Notes"
    assert "---------- Forwarded message" in started["text"]
    [offered] = started["attachments"]
    assert offered["ref"] == ref
    assert offered["name"] == "notes.pdf"

    saved = composer.save(to=[address(stranger())], keep=[{"ref": ref, "part": offered["part"]}])
    assert [part["name"] for part in saved["parts"]] == ["notes.pdf"]


def test_a_recipient_no_send_could_reach_is_left_out_of_a_prefill(composer: Composer) -> None:
    ref = composer.seed(mime("Notes", sender="root@localhost", to=f"Me <{ME}>", text="Hello"))
    started = composer.client.post(
        f"{MAIL}/drafts/prefill", json={"ref": ref, "mode": "reply"}
    ).json()
    assert started["to"] == []


def test_saving_replaces_the_previous_version(composer: Composer) -> None:
    compose_id = str(uuid.uuid4())
    bcc = address(stranger(), "Hidden")
    first = composer.save(
        compose_id=compose_id,
        to=[address("ada@example.org", "Ada")],
        bcc=[bcc],
        subject="First",
        text="One",
        files=(("notes.pdf", PDF, "application/pdf"),),
    )
    assert first["replaced"] is False
    assert first["autosave"] is True
    [part] = first["parts"]
    assert part["name"] == "notes.pdf"

    second = composer.save(
        compose_id=compose_id,
        ref=first["ref"],
        **{"from": ALIAS},
        to=[address("ada@example.org", "Ada")],
        bcc=[bcc],
        subject="Second",
        text="Two",
        keep=[{"ref": first["ref"], "part": part["part"]}],
    )
    assert second["replaced"] is True
    assert second["ref"] != first["ref"]
    assert [p["name"] for p in second["parts"]] == ["notes.pdf"]
    [stored] = composer.contents("Drafts")
    assert stored.subject == "Second"
    assert set(stored.flags) >= {"\\Draft", "\\Seen"}
    assert not composer.contents("Trash")

    draft = composer.read(second["ref"])
    assert draft["compose_id"] == compose_id
    assert draft["from"] == ALIAS
    assert draft["to"] == [address("ada@example.org", "Ada")]
    assert draft["bcc"] == [bcc]
    assert draft["subject"] == "Second"
    assert draft["text"].strip() == "Two"
    assert draft["lossy"] is False
    assert draft["send"] is None
    assert draft["answers"] is None

    found = composer.client.get(
        f"{MAIL}/drafts/find", params={"account": "work", "compose_id": compose_id}
    )
    assert found.status_code == 200
    assert found.json() == {"ref": second["ref"], "parts": second["parts"]}
    assert detail(
        composer.client.get(
            f"{MAIL}/drafts/find", params={"account": "work", "compose_id": str(uuid.uuid4())}
        )
    ) == (404, "not_found")


def test_a_draft_is_valid_without_recipients_but_a_send_is_not(composer: Composer) -> None:
    ref = composer.save(subject="To whom?", text="Later")["ref"]
    assert detail(composer.post_send(ref)) == (400, "invalid_argument")
    assert composer.client.get(f"{MAIL}/sends/1").status_code == 404


def test_saved_headers_stay_one_line(composer: Composer) -> None:
    ref = composer.save(
        to=[address(stranger(), "Name\r\nBcc: evil@example.org")],
        subject="Hello\r\nBcc: evil@example.org",
    )["ref"]
    [stored] = composer.contents("Drafts")
    headers = parsed(stored.source)
    assert headers.get_all("Bcc") is None
    assert "\n" not in str(headers["Subject"])
    assert composer.read(ref)["bcc"] == []


def test_a_draft_written_elsewhere_keeps_its_thread_and_goes_to_trash(composer: Composer) -> None:
    ref = composer.seed(
        mime(
            "Mine",
            sender=f"Me <{ME}>",
            to="ada@example.org",
            message_id="<elsewhere@example.org>",
            references="<thread@example.org>",
            in_reply_to="<thread@example.org>",
            text="Hello",
        ),
        "Drafts",
        ("\\Draft",),
    )
    draft = composer.read(ref)
    assert draft["compose_id"] is None

    # Saving replaces it: the old version is not ours to expunge.
    saved = composer.save(ref=ref, to=draft["to"], subject="Mine", text="Better")
    assert saved["replaced"] is True
    assert [message.subject for message in composer.contents("Trash")] == ["Mine"]
    [stored] = composer.contents("Drafts")
    assert parsed(stored.source)["In-Reply-To"] == "<thread@example.org>"

    other = composer.seed(mime("Other", text="x"), "Drafts", ("\\Draft",))
    assert composer.client.delete(f"{MAIL}/drafts/{other}").json() == {"removed": "trashed"}
    assert composer.client.delete(f"{MAIL}/drafts/{saved['ref']}").json() == {"removed": "expunged"}
    assert composer.contents("Drafts") == []


def test_a_version_keeps_the_compose_id_of_the_one_it_replaces(composer: Composer) -> None:
    compose_id = str(uuid.uuid4())
    ref = composer.save(compose_id=compose_id, subject="One", text="Hello")["ref"]

    refused = composer.post_draft(ref=ref, subject="Two", text="Hello")

    assert detail(refused) == (400, "invalid_argument")
    assert "compose_id" in refused.json()["detail"]["message"]
    assert [message.subject for message in composer.contents("Drafts")] == ["One"]
    assert composer.read(ref)["compose_id"] == compose_id


def test_a_new_compose_id_cannot_get_round_an_unknown_delivery(stubbed: Composer) -> None:
    with SmtpStub(end_reply=HANG_UP) as stub:
        point_at(stubbed, stub)
        ref = stubbed.save(to=[address(stranger())], subject="Maybe", text="Hello")["ref"]
        assert stubbed.sent(ref)["state"] == "unknown"

        assert detail(stubbed.post_draft(ref=ref, text="Again")) == (400, "invalid_argument")

        assert detail(stubbed.post_send(ref)) == (409, "delivery_unknown")
        assert len(stub.messages) == 1


def test_a_draft_written_elsewhere_cannot_be_saved_past_its_unknown_send(
    stubbed: Composer,
) -> None:
    """Its key is its ref, so a new version would be free to go out again, with nobody asked."""
    with SmtpStub(end_reply=HANG_UP) as stub:
        point_at(stubbed, stub)
        ref = foreign_draft(stubbed, stranger())
        assert stubbed.sent(ref)["state"] == "unknown"

        refused = stubbed.post_draft(ref=ref, to=[address(stranger())], text="Better")

        assert detail(refused) == (409, "draft_not_editable")
        assert [message.subject for message in stubbed.contents("Drafts")] == ["Elsewhere"]
        assert detail(stubbed.post_send(ref)) == (409, "delivery_unknown")
        assert len(stub.messages) == 1


def test_a_draft_written_elsewhere_cannot_be_saved_after_it_was_sent(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    to = stranger()
    ref = foreign_draft(composer, to)
    monkeypatch.setattr(gate, "file_sent", lambda *_args: False)
    assert composer.sent(ref)["state"] == "sent"

    refused = composer.post_draft(ref=ref, to=[address(to)], text="Better")

    assert detail(refused) == (409, "draft_not_editable")
    assert composer.post_send(ref).status_code == 200
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_draft_written_elsewhere_cannot_be_saved_while_it_goes(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    to = stranger()
    ref = foreign_draft(composer, to)
    real = gate.deliver
    entered = threading.Event()
    release = threading.Event()

    def held_back(*args: Any) -> Any:
        entered.set()
        assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(gate, "deliver", held_back)
    assert composer.post_send(ref).status_code == 202
    assert entered.wait(10)

    assert detail(composer.post_draft(ref=ref, to=[address(to)], text="Better")) == (
        409,
        "draft_not_editable",
    )
    release.set()
    composer.settled()
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_draft_written_elsewhere_can_be_saved_after_a_failed_send(stubbed: Composer) -> None:
    """Nothing left, so no send holds it, and its next version is an own draft."""
    with SmtpStub(end_reply=b"554 no\r\n") as stub:
        point_at(stubbed, stub)
        ref = foreign_draft(stubbed, stranger())
        assert stubbed.sent(ref)["state"] == "failed"

        saved = stubbed.save(ref=ref, to=[address(stranger())], text="Better")

        assert saved["replaced"] is True
        assert stubbed.read(saved["ref"])["send"] is None


def test_a_delivered_draft_written_elsewhere_stays_held_once_it_is_gone(stubbed: Composer) -> None:
    """Delivery moved it to Trash, so its ref reads as nothing, and only the send under that ref
    can stop a second delivery: a save or a retry from a client that held the ref."""
    with SmtpStub() as stub:
        point_at(stubbed, stub)
        ref = foreign_draft(stubbed, stranger())
        sent = stubbed.sent(ref)
        assert sent["state"] == "sent"
        assert stubbed.contents("Drafts") == []
        assert [message.subject for message in stubbed.contents("Trash")] == ["Elsewhere"]

        refused = stubbed.post_draft(ref=ref, to=[address(stranger())], text="Better")

        assert detail(refused) == (409, "draft_not_editable")
        held = {"send_id": sent["send_id"], "state": "sent", "code": None}
        assert refused.json()["detail"]["send"] == held
        assert stubbed.contents("Drafts") == []

        replay = stubbed.post_send(ref)

        assert replay.status_code == 200
        assert replay.json()["send_id"] == sent["send_id"]
        assert replay.json()["state"] == "sent"
        stubbed.settled()
        assert len(stub.messages) == 1


def test_a_gone_draft_written_elsewhere_with_an_unknown_send_goes_no_further(
    stubbed: Composer,
) -> None:
    with SmtpStub(end_reply=HANG_UP) as stub:
        point_at(stubbed, stub)
        ref = foreign_draft(stubbed, stranger())
        send = stubbed.sent(ref)
        assert send["state"] == "unknown"
        assert stubbed.client.delete(f"{MAIL}/drafts/{ref}").status_code == 200

        refused = stubbed.post_draft(ref=ref, to=[address(stranger())], text="Better")
        assert detail(refused) == (409, "draft_not_editable")
        assert refused.json()["detail"]["send"]["state"] == "unknown"
        assert detail(stubbed.post_send(ref)) == (409, "delivery_unknown")
        assert detail(stubbed.post_send(ref, confirm_resend=True)) == (404, "not_found")

        assert stubbed.follow(send["send_id"])["state"] == "unknown"
        assert stubbed.contents("Drafts") == []
        assert len(stub.messages) == 1


def test_a_gone_draft_written_elsewhere_still_going_answers_the_same_send(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    to = stranger()
    ref = foreign_draft(composer, to)
    real = gate.deliver
    entered = threading.Event()
    release = threading.Event()

    def held_back(*args: Any) -> Any:
        entered.set()
        assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(gate, "deliver", held_back)
    started = composer.post_send(ref)
    assert started.status_code == 202
    assert entered.wait(10)
    assert composer.client.delete(f"{MAIL}/drafts/{ref}").status_code == 200

    retried = composer.post_send(ref)

    assert retried.status_code == 202
    assert retried.json() == started.json()
    release.set()
    composer.settled()
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_gone_draft_written_elsewhere_after_a_failed_send_is_just_gone(
    stubbed: Composer,
) -> None:
    with SmtpStub(end_reply=b"554 no\r\n") as stub:
        point_at(stubbed, stub)
        ref = foreign_draft(stubbed, stranger())
        assert stubbed.sent(ref)["state"] == "failed"
        assert stubbed.client.delete(f"{MAIL}/drafts/{ref}").status_code == 200

        saved = stubbed.post_draft(ref=ref, to=[address(stranger())], text="Better")

        assert detail(saved) == (404, "not_found")
        assert detail(stubbed.post_send(ref)) == (404, "not_found")


def test_a_gone_own_draft_is_just_gone(composer: Composer) -> None:
    """Its key survives in its ``compose_id``, which a new version carries, so no send holds it."""
    to = stranger()
    compose_id = str(uuid.uuid4())
    ref = composer.save(compose_id=compose_id, to=[address(to)], subject="Mine")["ref"]
    assert composer.sent(ref)["state"] == "sent"

    refused = composer.post_draft(ref=ref, compose_id=compose_id, to=[address(to)], text="Better")

    assert detail(refused) == (404, "not_found")
    assert detail(composer.post_send(ref)) == (404, "not_found")


def test_another_users_send_holds_nothing_under_the_same_ref(
    composer: Composer,
    smtp_server: SmtpTestServer,
    db_session: Session,
    imap_server: ImapTestServer,
) -> None:
    """A ref is no secret and account ids repeat between users, so the same text can name a
    draft of theirs: the key is claimed per user, and another's send must not answer for it."""
    to = stranger()
    ref = foreign_draft(composer, to)
    assert composer.sent(ref)["state"] == "sent"

    bob = create_user(db_session, "bob", PASSWORD)
    composer.client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, bob))
    login = imap_server.unique_user("bob")
    create_folders(imap_server, login, "Drafts", "Sent", "Trash")
    theirs = Composer(composer.client, imap_server, login, composer.application)
    add_account(theirs, smtp=smtp_endpoint(smtp_server))

    saved = theirs.post_draft(ref=ref, to=[address(to)], text="Mine")
    sent = theirs.post_send(ref)

    assert detail(saved) in ((404, "not_found"), (409, "stale_reference"))
    assert detail(sent) == detail(saved)
    assert theirs.contents("Drafts") == []
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_stored_bare_cr_refuses_the_send_and_says_saving_fixes_it(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dovecot turns one into a line break on APPEND, and other servers need not, hence the
    source stood in for it."""
    to = stranger()
    ref = foreign_draft(composer, to)
    source = f"From: {ME}\r\nTo: {to}\rFrom: boss@example.org\r\n\r\nHello\r\n".encode()
    monkeypatch.setattr("mailroom_core.send.gate.draft_source", lambda *_args: source)

    refused = composer.post_send(ref)

    assert detail(refused) == (400, "invalid_argument")
    assert "saving it again" in refused.json()["detail"]["message"]
    assert smtp_server.delivered_to(to) == []


def test_only_drafts_are_read_sent_or_deleted_here(composer: Composer) -> None:
    ref = composer.seed(mime("Inbox", text="Keep me"))
    assert detail(composer.client.get(f"{MAIL}/drafts/{ref}")) == (400, "invalid_argument")
    assert detail(composer.client.delete(f"{MAIL}/drafts/{ref}")) == (400, "invalid_argument")
    assert detail(composer.post_send(ref)) == (400, "invalid_argument")
    assert [message.subject for message in composer.contents("INBOX")] == ["Inbox"]


def test_caps_are_refused_by_code(composer: Composer) -> None:
    many = [address(f"a{i}@example.org") for i in range(51)]
    cases: list[tuple[dict[str, Any], tuple[int, str]]] = [
        ({"to": many}, (400, "invalid_argument")),
        ({"subject": "x" * 999}, (400, "invalid_argument")),
        ({"text": "x" * 100_001}, (400, "invalid_argument")),
        ({"to": [address(f"a{i}@example.org") for i in range(11)]}, (400, "invalid_argument")),
        ({"to": [address("not an address")]}, (422, "recipient_refused")),
        ({"bcc": [address("two@example.org,three@example.org")]}, (422, "recipient_refused")),
        ({"from": "someone@example.org"}, (400, "invalid_argument")),
        ({"account": "elsewhere"}, (404, "not_found")),
    ]
    for fields, refusal in cases:
        assert detail(composer.post_draft(**fields)) == refusal, fields
    assert composer.contents("Drafts") == []
    undeliverable = composer.post_draft(to=[address("not an address")])
    assert undeliverable.json()["detail"]["message"] == (
        "A recipient is not a deliverable address, or the mail server refused the message; "
        "nothing was sent."
    )


def test_malformed_saves_are_refused(composer: Composer) -> None:
    unknown = composer.post_draft(extra=1)
    assert unknown.status_code == 422
    assert isinstance(unknown.json()["detail"], list)
    assert composer.post_draft(compose_id="not one!").status_code == 422
    assert composer.post_draft(keep=[{"ref": "x", "part": "2a"}]).status_code == 422
    assert composer.client.post(f"{MAIL}/drafts", json={}).status_code == 422
    assert (
        composer.client.post(f"{MAIL}/drafts", files=[("draft", (None, "nope"))]).status_code == 422
    )
    assert composer.client.get(f"{MAIL}/drafts/find", params={"account": "work"}).status_code == 422
    assert composer.client.post(f"{MAIL}/drafts/x/send", json={"x": 1}).status_code == 422
    assert detail(composer.post_draft(ref="garbage")) == (400, "invalid_argument")


def test_upload_caps(composer: Composer, migrated_engine: Engine, user: User) -> None:
    eleven = tuple((f"f{i}.txt", b"x", "text/plain") for i in range(11))
    assert detail(composer.post_draft(files=eleven)) == (400, "invalid_argument")

    composer.set_limits(migrated_engine, user.id, attachment_max_mb=0.001)
    too_big = (("big.bin", b"x" * 2000, "application/octet-stream"),)
    refused = composer.post_draft(files=too_big)
    assert detail(refused) == (413, "too_large")
    assert refused.json()["detail"]["message"].startswith(
        "Too large: the message, attachment or upload is over its limit. Attachments may total"
    )
    assert composer.contents("Drafts") == []


def test_a_save_past_the_users_few_is_busy_before_its_body_is_read(
    composer: Composer, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = composer.application.state.mail_pool

    def unread(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("the body was read")

    places = [pool.upload(user.id) for _ in range(USER_UPLOADS)]
    for place in places:
        place.__enter__()
    try:
        with monkeypatch.context() as held:
            held.setattr("mailroom.api.routes.mail.Request.form", unread)
            refused = composer.post_draft(subject="One too many")
        assert detail(refused) == (429, "busy")
    finally:
        for place in places:
            place.__exit__(None, None, None)
    assert composer.contents("Drafts") == []
    assert composer.save(subject="My turn")["replaced"] is False


def test_a_save_gives_its_place_back_however_it_ends(composer: Composer) -> None:
    for _ in range(USER_UPLOADS + 1):
        assert composer.post_draft(ref="garbage").status_code == 400
        malformed = composer.client.post(
            f"{MAIL}/drafts",
            content=b"--x\r\nno end",
            headers={"Content-Type": "multipart/form-data; boundary=x"},
        )
        assert malformed.status_code in (400, 422)
    assert composer.save(subject="Still free")["replaced"] is False


def test_the_spool_is_closed_before_the_save_runs(
    composer: Composer, monkeypatch: pytest.MonkeyPatch
) -> None:
    spooled: list[Any] = []
    real_read = UploadFile.read
    real_save = service.save_draft

    async def recorded(upload: UploadFile, size: int = -1) -> bytes:
        spooled.append(upload.file)
        return await real_read(upload, size)

    def checked(*args: Any, **kwargs: Any) -> Any:
        assert [file.closed for file in spooled] == [True]
        return real_save(*args, **kwargs)

    monkeypatch.setattr(UploadFile, "read", recorded)
    monkeypatch.setattr(service, "save_draft", checked)
    saved = composer.save(files=(("notes.pdf", PDF, "application/pdf"),))
    assert [part["name"] for part in saved["parts"]] == ["notes.pdf"]


def test_a_file_without_a_name_still_saves(composer: Composer) -> None:
    saved = composer.save(files=((" ", b"plain", "text/plain"),))
    assert [part["name"] for part in saved["parts"]] == ["attachment"]


def test_a_declared_size_is_refused_before_reading(composer: Composer) -> None:
    response = composer.client.post(
        f"{MAIL}/drafts",
        content=b"",
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "Content-Length": str(30 * 1024 * 1024),
        },
    )
    assert detail(response) == (413, "too_large")
    assert "over its limit" in response.json()["detail"]["message"]


def test_a_body_that_declares_nothing_is_cut_at_the_limit(
    composer: Composer, migrated_engine: Engine, user: User
) -> None:
    composer.set_limits(migrated_engine, user.id, attachment_max_mb=0.001)
    boundary = "x" * 10
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="files[]"; filename="a"\r\n\r\n'
    ).encode()

    def chunks() -> Iterator[bytes]:
        yield head
        for _ in range(8):
            yield b"x" * (512 * 1024)

    response = composer.client.post(
        f"{MAIL}/drafts",
        content=chunks(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert detail(response) == (413, "too_large")


def test_the_attachment_cap_is_clamped_and_listed(
    composer: Composer, migrated_engine: Engine, user: User
) -> None:
    [account] = composer.client.get("/api/v1/accounts").json()
    assert account["attachment_max_mb"] == 10
    assert account["max_recipients"] == 10
    assert [identity["address"] for identity in account["identities"]] == [ME, ALIAS]
    assert account["identities"][0]["default"] is True

    composer.set_limits(migrated_engine, user.id, attachment_max_mb=100.0, max_recipients=3)
    [account] = composer.client.get("/api/v1/accounts").json()
    assert account["attachment_max_mb"] == 25
    assert account["max_recipients"] == 3
    ctx = app_context(composer.application.state)
    assert ctx is not None
    assert service.upload_limit(ctx, user.id) == (25 + 2) * 1024 * 1024


def test_sending_delivers_files_and_removes_the_draft(
    composer: Composer, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    original = composer.seed(
        mime("Notes", sender="ada@example.org", to=ME, message_id="<one@example.org>", text="Hi")
    )
    to, bcc = stranger(), stranger()
    compose_id = str(uuid.uuid4())
    ref = composer.save(
        compose_id=compose_id,
        answers=original,
        to=[address(to, "To")],
        bcc=[address(bcc)],
        subject="Re: Notes",
        text="Thanks",
        files=(("notes.pdf", PDF, "application/pdf"),),
    )["ref"]

    result = composer.sent(ref)

    assert result["state"] == "sent"
    assert result["code"] is None
    assert result["filed"] is True
    assert result["settled"] is True
    assert result["rejected"] == result["notes"] == result["note_codes"] == []
    smtp = SmtpTestServer.from_env()
    assert smtp is not None
    [delivered] = smtp.delivered_to(to)
    wire = parsed(delivered.raw)
    assert "X-Mailroom-Draft" not in wire
    assert wire["In-Reply-To"] == "<one@example.org>"
    assert wire["Message-ID"] == f"<{compose_id}@example.org>"
    assert [part.get_filename() for part in wire.iter_attachments()] == ["notes.pdf"]
    assert len(smtp.delivered_to(bcc)) == 1

    assert composer.contents("Drafts") == []
    [filed] = composer.contents("Sent")
    assert "\\Seen" in filed.flags
    assert parsed(filed.source)["Bcc"] == bcc
    assert "X-Mailroom-Draft" not in parsed(filed.source)
    inbox = composer.client.get(f"{MAIL}/messages/{original}").json()
    assert inbox["flags"]["answered"] is True

    # The web's draft saves are the user typing: only the send is recorded, its draft's removal
    # with it.
    records = audited(user.id)
    assert {(record.actor, record.action) for record in records} == {("user", "send")}
    [delivered_record] = [r for r in records if r.detail.get("delivery") == "sent"]
    assert delivered_record.result == "ok"
    assert delivered_record.detail["filed"] is True
    assert delivered_record.detail["bcc"] == [bcc]
    assert "subject" not in delivered_record.detail
    assert any(r.detail.get("removed") == "expunged" for r in records)

    # A retry reads the draft before it reaches the key, and the send removed it.
    assert detail(composer.post_send(ref)) == (404, "not_found")
    assert len(smtp.delivered_to(to)) == 1


def test_the_same_compose_id_on_two_accounts_is_two_sends(
    composer: Composer, smtp_server: SmtpTestServer
) -> None:
    login = composer.server.unique_user("home")
    create_folders(composer.server, login, "Drafts", "Sent", "Trash")
    add_account(
        composer,
        "home",
        login=login,
        identities=[{"address": "home@example.org"}],
        smtp=smtp_endpoint(smtp_server),
    )
    compose_id = str(uuid.uuid4())
    to_work, to_home = stranger(), stranger()
    work = composer.save(compose_id=compose_id, to=[address(to_work)], subject="W", text="x")
    home = composer.save(
        account="home", compose_id=compose_id, to=[address(to_home)], subject="H", text="x"
    )

    sent = composer.sent(work["ref"])

    assert composer.read(home["ref"])["send"] is None
    other = composer.sent(home["ref"])
    assert other["send_id"] != sent["send_id"]
    assert len(smtp_server.delivered_to(to_work)) == 1
    assert len(smtp_server.delivered_to(to_home)) == 1


def test_the_route_sends_as_the_user_and_no_caller_can_leave_that_out() -> None:
    """``by`` waives the send mode, so a caller has to say who it is."""
    by = inspect.signature(service.send_draft).parameters["by"]
    assert by.kind is inspect.Parameter.KEYWORD_ONLY
    assert by.default is inspect.Parameter.empty


def test_drafts_mode_limits_agents_not_the_user(
    composer: Composer, smtp_server: SmtpTestServer
) -> None:
    fields = account_fields(composer)
    fields.pop("id")
    fields.pop("password")
    changed = composer.client.put(
        "/api/v1/accounts/work",
        json=fields | {"send_mode": "drafts", "smtp": smtp_endpoint(smtp_server)},
    )
    assert changed.status_code == 200, changed.text
    to = stranger()
    ref = composer.save(to=[address(to)], subject="Mine", text="Hello")["ref"]
    assert composer.sent(ref)["state"] == "sent"
    assert len(smtp_server.delivered_to(to)) == 1


def test_off_refuses_everything_that_writes(
    composer: Composer, smtp_server: SmtpTestServer
) -> None:
    ref = composer.save(to=[address(stranger())], subject="Mine", text="Hello")["ref"]
    original = composer.seed(mime("Notes", text="Hi"))
    fields = account_fields(composer)
    fields.pop("id")
    fields.pop("password")
    changed = composer.client.put(
        "/api/v1/accounts/work",
        json=fields | {"send_mode": "off", "smtp": smtp_endpoint(smtp_server)},
    )
    assert changed.status_code == 200, changed.text

    refused = (409, "send_disabled")
    assert detail(composer.post_send(ref)) == refused
    assert detail(composer.post_draft(ref=ref, text="More")) == refused
    assert detail(composer.client.delete(f"{MAIL}/drafts/{ref}")) == refused
    assert (
        detail(
            composer.client.post(f"{MAIL}/drafts/prefill", json={"ref": original, "mode": "reply"})
        )
        == refused
    )
    assert composer.read(ref)["subject"] == "Mine"
    assert len(composer.contents("Drafts")) == 1


def test_a_retried_send_delivers_once(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    to = stranger()
    ref = composer.save(to=[address(to)], subject="Once", text="Hello")["ref"]
    real = gate.deliver
    entered = threading.Event()
    release = threading.Event()

    def held_back(*args: Any) -> Any:
        entered.set()
        assert release.wait(30)
        return real(*args)

    monkeypatch.setattr(gate, "deliver", held_back)
    first = composer.post_send(ref)
    assert first.status_code == 202
    assert entered.wait(10)
    second = composer.post_send(ref)
    assert second.status_code == 202
    assert second.json() == first.json()
    assert composer.follow(first.json()["send_id"])["state"] == "sending"
    assert composer.read(ref)["send"] == {
        "send_id": first.json()["send_id"],
        "state": "sending",
        "code": None,
    }
    release.set()
    composer.settled()
    assert composer.follow(first.json()["send_id"])["state"] == "sent"
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_sent_key_replays_and_a_leftover_draft_goes_nowhere(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing the draft failed, so it is still there to be sent again: the message has gone,
    says so, and does not go twice."""
    to = stranger()
    ref = composer.save(to=[address(to)], subject="Once", text="Hello")["ref"]

    monkeypatch.setattr("mailroom_core.send.gate.try_remove_draft", lambda *_args: "failed")
    result = composer.sent(ref)
    assert result["state"] == "sent"
    assert result["filed"] is True
    assert result["notes"] == ["The draft could not be removed from Drafts."]
    assert result["note_codes"] == ["draft_not_removed"]
    assert len(composer.contents("Drafts")) == 1
    assert len(composer.contents("Sent")) == 1

    replayed = composer.post_send(ref)
    assert replayed.status_code == 200
    assert replayed.json() == result
    assert composer.read(ref)["send"]["state"] == "sent"
    assert len(smtp_server.delivered_to(to)) == 1


def test_nothing_after_delivery_makes_it_a_failure(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    to = stranger()
    ref = composer.save(to=[address(to)], subject="Once", text="Hello")["ref"]

    def broken(*_args: Any) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(gate, "file_sent", broken)
    monkeypatch.setattr(gate, "settle_draft", broken)
    result = composer.sent(ref)
    assert result["state"] == "sent"
    assert result["filed"] is False
    assert result["settled"] is True
    assert result["notes"] == [
        "Sent, but not filed in Sent.",
        "The draft was kept as the only copy of the message.",
    ]
    assert result["note_codes"] == ["draft_kept"]
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_settling_step_that_breaks_after_filing_only_leaves_the_draft(
    composer: Composer, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = composer.save(to=[address(stranger())], subject="Once", text="Hello")["ref"]
    original = composer.seed(mime("Notes", sender="ada@example.org", to=ME, text="Hi"))
    reply = composer.save(answers=original, to=[address(stranger())], text="Thanks")["ref"]

    def broken(*_args: Any, **_kwargs: Any) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(gate, "settle_draft", broken)
    result = composer.sent(ref)
    assert result["filed"] is True
    assert result["notes"] == ["The draft could not be removed from Drafts."]
    assert result["note_codes"] == ["draft_not_removed"]

    answered = composer.sent(reply)
    assert answered["note_codes"] == ["original_not_marked", "draft_not_removed"]
    assert len(answered["notes"]) == 2


def test_a_send_that_was_not_filed_keeps_its_draft_and_still_answers(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The draft is then the only copy of the message in the account."""
    original = composer.seed(mime("Notes", sender="ada@example.org", to=ME, text="Hi"))
    to = stranger()
    ref = composer.save(answers=original, to=[address(to)], subject="Re: Notes", text="Thanks")[
        "ref"
    ]

    monkeypatch.setattr(gate, "file_sent", lambda *_args: False)
    result = composer.sent(ref)

    assert result["state"] == "sent"
    assert result["filed"] is False
    assert result["notes"] == [
        "Sent, but not filed in Sent.",
        "The draft was kept as the only copy of the message.",
    ]
    assert result["note_codes"] == ["draft_kept"]
    assert len(composer.contents("Drafts")) == 1
    inbox = composer.client.get(f"{MAIL}/messages/{original}").json()
    assert inbox["flags"]["answered"] is True

    replayed = composer.post_send(ref)
    assert replayed.status_code == 200
    assert replayed.json() == result
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_send_is_settled_only_once_all_its_notes_are_in(
    composer: Composer, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = composer.save(to=[address(stranger())], subject="Once", text="Hello")["ref"]
    real = gate.settle_draft
    entered = threading.Event()
    release = threading.Event()

    def held_back(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(gate, "settle_draft", held_back)
    response = composer.post_send(ref)
    assert response.status_code == 202
    assert entered.wait(10)
    during = composer.follow(response.json()["send_id"])
    assert during["state"] == "sent"
    assert during["settled"] is False
    release.set()
    composer.settled()
    assert composer.follow(response.json()["send_id"])["settled"] is True


def test_a_deadline_after_delivery_only_loses_the_filing(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The step is refused by ``_Stoppable``, the way a slow server would have it, and a message
    that has gone stays sent."""
    to = stranger()
    ref = composer.save(to=[address(to)], subject="Once", text="Hello")["ref"]
    pool = composer.application.state.mail_pool
    real_deliver = gate.deliver
    real_filing = gate.file_sent

    def delivered(*args: Any) -> Any:
        sent = real_deliver(*args)
        monkeypatch.setattr(pool, "deadline_seconds", 0.05)
        return sent

    def slow_filing(*args: Any) -> bool:
        time.sleep(0.3)
        return real_filing(*args)

    monkeypatch.setattr(gate, "deliver", delivered)
    monkeypatch.setattr(gate, "file_sent", slow_filing)
    response = composer.post_send(ref)
    assert response.status_code == 202
    composer.settled()
    monkeypatch.setattr(pool, "deadline_seconds", 30.0)
    result = composer.follow(response.json()["send_id"])
    assert result["state"] == "sent"
    assert result["filed"] is False
    assert "Sent, but not filed in Sent." in result["notes"]
    assert composer.contents("Sent") == []
    assert len(composer.contents("Drafts")) == 1
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_stale_sending_reads_unknown_and_resends_only_when_confirmed(
    composer: Composer, smtp_server: SmtpTestServer, migrated_engine: Engine, user: User
) -> None:
    """A restart during a send leaves it ``sending``, with nobody working on it."""
    to = stranger()
    compose_id = str(uuid.uuid4())
    ref = composer.save(compose_id=compose_id, to=[address(to)], subject="Lost", text="Hello")[
        "ref"
    ]
    long_ago = datetime.now(UTC) - SEND_CEILING - timedelta(seconds=1)
    store = user_store(migrated_engine, user.id)
    send_id = str(store.claim_send(f"work/{compose_id}", "work", 20, now=long_ago).send.id)

    assert composer.follow(send_id) == {
        "send_id": send_id,
        "state": "unknown",
        "code": None,
        "filed": None,
        "rejected": [],
        "notes": [],
        "note_codes": [],
        "settled": False,
    }
    assert composer.read(ref)["send"] == {"send_id": send_id, "state": "unknown", "code": None}
    refused = composer.post_send(ref)
    assert detail(refused) == (409, "delivery_unknown")
    assert refused.json()["detail"]["message"] == (
        "Delivery could not be confirmed: the message may already have gone out, so it was not "
        "sent again."
    )
    assert smtp_server.delivered_to(to) == []
    assert len(composer.contents("Drafts")) == 1

    assert composer.sent(ref, confirm_resend=True)["state"] == "sent"
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_dropped_line_after_data_is_unknown(stubbed: Composer) -> None:
    with SmtpStub(end_reply=HANG_UP) as stub:
        point_at(stubbed, stub)
        ref = stubbed.save(to=[address(stranger())], subject="Maybe", text="Hello")["ref"]
        result = stubbed.sent(ref)
        assert result["state"] == "unknown"
        assert result["code"] is None
        assert result["filed"] is None
        assert len(stubbed.contents("Drafts")) == 1
        assert stubbed.contents("Sent") == []
        assert detail(stubbed.post_send(ref)) == (409, "delivery_unknown")
        assert len(stub.messages) == 1


def test_a_refusal_is_failed_and_can_go_again(stubbed: Composer) -> None:
    with SmtpStub(end_reply=b"554 no\r\n") as stub:
        point_at(stubbed, stub)
        ref = stubbed.save(to=[address(stranger())], subject="No", text="Hello")["ref"]
        result = stubbed.sent(ref)
        assert result["state"] == "failed"
        assert result["code"] == "server_error"
        assert len(stubbed.contents("Drafts")) == 1
        assert stubbed.read(ref)["send"]["state"] == "failed"
    with SmtpStub() as stub:
        point_at(stubbed, stub)
        again = stubbed.sent(ref)
        assert again["state"] == "sent"
        assert again["send_id"] == result["send_id"]
        assert len(stub.messages) == 1


def test_partly_refused_recipients_are_reported(stubbed: Composer) -> None:
    good, bad = stranger(), stranger()
    with SmtpStub(refuse=[bad]) as stub:
        point_at(stubbed, stub)
        ref = stubbed.save(to=[address(good), address(bad)], subject="Some", text="Hello")["ref"]
        result = stubbed.sent(ref)
        assert result["state"] == "sent"
        assert result["rejected"] == [bad]


def test_a_full_runner_is_busy_before_anything_is_claimed(
    composer: Composer, smtp_server: SmtpTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    to = stranger()
    compose_id = str(uuid.uuid4())
    ref = composer.save(compose_id=compose_id, to=[address(to)], subject="Wait", text="Hello")[
        "ref"
    ]
    runner: SendRunner = composer.application.state.send_runner
    with monkeypatch.context() as held:
        held.setattr(runner, "_per_user", 0)
        assert detail(composer.post_send(ref)) == (429, "busy")
    assert composer.read(ref)["send"] is None
    assert composer.sent(ref)["state"] == "sent"
    assert len(smtp_server.delivered_to(to)) == 1


def test_the_hourly_limit_is_refused_before_smtp(
    composer: Composer, smtp_server: SmtpTestServer, migrated_engine: Engine, user: User
) -> None:
    composer.set_limits(migrated_engine, user.id, send_per_hour=1)
    first, second = stranger(), stranger()
    ref = composer.save(to=[address(first)], subject="One", text="Hello")["ref"]
    assert composer.sent(ref)["state"] == "sent"
    ref = composer.save(to=[address(second)], subject="Two", text="Hello")["ref"]
    refused = composer.post_send(ref)
    assert detail(refused) == (429, "rate_limited")
    assert "the next send is possible at" in refused.json()["detail"]["message"]
    assert smtp_server.delivered_to(second) == []


def test_a_stored_draft_with_a_bad_address_is_refused_by_the_contract(composer: Composer) -> None:
    ref = composer.seed(
        mime("Elsewhere", sender=f"Me <{ME}>", to="root@localhost", text="x"),
        "Drafts",
        ("\\Draft",),
    )
    assert detail(composer.post_send(ref)) == (422, "recipient_refused")


def test_another_users_refs_parts_and_sends_are_not_found(
    composer: Composer,
    smtp_server: SmtpTestServer,
    db_session: Session,
    imap_server: ImapTestServer,
) -> None:
    to = stranger()
    ref = composer.save(to=[address(to)], subject="Mine", text="Hello")["ref"]
    attached = composer.seed(
        mime(
            "Notes",
            text="Hello",
            attachments=[MimeAttachment("notes.pdf", "application/pdf", PDF)],
        )
    )
    send_id = composer.sent(composer.save(to=[address(to)], subject="Sent")["ref"])["send_id"]

    bob = create_user(db_session, "bob", PASSWORD)
    composer.client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, bob))
    login = imap_server.unique_user("bob")
    create_folders(imap_server, login, "Drafts", "Sent", "Trash")
    theirs = Composer(composer.client, imap_server, login, composer.application)
    add_account(theirs, "home", smtp=smtp_endpoint(smtp_server))

    gone = (404, "not_found")
    client = composer.client
    assert detail(client.get(f"{MAIL}/drafts/{ref}")) == gone
    assert detail(client.delete(f"{MAIL}/drafts/{ref}")) == gone
    assert detail(client.post(f"{MAIL}/drafts/{ref}/send", json={})) == gone
    assert detail(client.post(f"{MAIL}/drafts/prefill", json={"ref": ref, "mode": "reply"})) == gone
    assert detail(client.get(f"{MAIL}/messages/{attached}/parts/2")) == gone
    assert detail(client.get(f"{MAIL}/sends/{send_id}")) == gone
    assert detail(client.get(f"{MAIL}/sends/not-a-number")) == gone
    assert detail(client.get(f"{MAIL}/sends/99999999999999999999")) == gone
    assert (
        detail(
            client.get(
                f"{MAIL}/drafts/find", params={"account": "work", "compose_id": str(uuid.uuid4())}
            )
        )
        == gone
    )
    own = {"account": "home"}
    assert detail(theirs.post_draft(account="work")) == gone
    assert detail(theirs.post_draft(**own, ref=ref)) == gone
    assert detail(theirs.post_draft(**own, answers=attached)) == gone
    assert detail(theirs.post_draft(**own, keep=[{"ref": attached, "part": "2"}])) == gone
    assert theirs.contents("Drafts") == []
    assert len(smtp_server.delivered_to(to)) == 1


def test_a_ref_of_another_account_is_refused(composer: Composer) -> None:
    ref = composer.save(subject="Mine")["ref"]
    message = parse_ref(ref)
    add_account(
        composer,
        "home",
        login=f"{composer.login}-home",
        identities=[{"address": "home@example.org"}],
    )
    elsewhere = format_ref(MessageRef("home", "Drafts", message.uid_validity, message.uid))
    assert detail(composer.post_draft(ref=elsewhere)) == (400, "invalid_argument")
    assert detail(composer.post_draft(answers=elsewhere)) == (400, "invalid_argument")


def test_logs_hold_counts_and_codes_only(
    composer: Composer, caplog: pytest.LogCaptureFixture
) -> None:
    to = stranger()
    secret = "A-very-private-subject"
    with caplog.at_level(logging.INFO):
        ref = composer.save(to=[address(to)], subject=secret, text=secret)["ref"]
        composer.sent(ref)
    events = {record.message: record for record in caplog.records}
    finished = events["mail_send_finished"]
    assert finished.__dict__["state"] == "sent"
    assert finished.__dict__["recipient_count"] == 1
    assert finished.__dict__["rejected_count"] == 0
    assert finished.__dict__["filed"] is True
    assert "mail_draft_saved" in events
    assert "mail_send_requested" in events
    ours = [r for r in caplog.records if r.name.startswith("mailroom")]
    for record in ours:
        logged = json.dumps({key: str(value) for key, value in record.__dict__.items()})
        assert secret not in logged
        assert to not in logged
        # The access log has the path, as for every message route.
        assert ref not in logged or record.name == "mailroom.http"


def test_the_runner_limits_sends_and_leaves_no_claimed_send_behind() -> None:
    runner = SendRunner(threads=2, per_user=1, grace_seconds=0.05)
    release = threading.Event()
    ran: list[int] = []

    def job(number: int) -> Any:
        def run(_transfer: gate.Transfer) -> None:
            assert release.wait(5)
            ran.append(number)

        return run

    def boom(_transfer: gate.Transfer) -> None:
        raise RuntimeError

    first = runner.reserve(1)
    with pytest.raises(MailError) as busy:
        runner.reserve(1)
    assert busy.value.code == "busy"
    second = runner.reserve(2)
    with pytest.raises(MailError):
        runner.reserve(3)

    # Given back, the place is free again; twice is once, and a started slot stays taken.
    second.release()
    second.release()
    third = runner.reserve(3)
    third.start(boom)
    first.start(job(1))
    first.release()
    deadline = time.monotonic() + 5
    while runner.held[3] > 0:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert runner.held[1] == 1

    # A send whose caller gave up on it still runs.
    late = runner.reserve(4)
    late.release()
    assert runner.held[4] == 0
    late.start(job(4))
    with pytest.raises(ValueError, match="started"):
        late.start(job(5))
    release.set()
    runner.close()
    deadline = time.monotonic() + 5
    while len(ran) < 2:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert sorted(ran) == [1, 4]
    with pytest.raises(MailError):
        runner.reserve(5)


def test_closing_the_runner_cuts_what_is_still_running() -> None:
    runner = SendRunner(grace_seconds=0.01)
    cut = threading.Event()

    def stuck(transfer: gate.Transfer) -> None:
        deadline = time.monotonic() + 5
        while not transfer.aborted and time.monotonic() < deadline:
            time.sleep(0.01)
        if transfer.aborted:
            cut.set()

    runner.reserve(1).start(stuck)
    runner.close()
    assert cut.wait(5)
