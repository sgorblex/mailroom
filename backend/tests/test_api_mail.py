"""The mail client's REST routes (``docs/requirements/mail-client.md``) against the Dovecot
``IMAP_TEST_HOST`` names (the dev one: ``IMAP_TEST_PORT=33993``, ``MAILROOM_MAIL_CA_FILE`` its
CA); skipped without it. Each test logs in as a fresh mailbox user, seeded with mailroom-core's
testing helpers."""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import pytest
from mailroom_core.crypto import secrets_subkey
from mailroom_core.mail import (
    MESSAGES,
    MailError,
    MessageRef,
    SearchQuery,
    format_ref,
    parse_ref,
)
from mailroom_core.store import SecretChanges
from mailroom_core.testing import MimeAttachment, folder_contents, mime, seed

from mailroom.accounts.mail import mail_ssl_context
from mailroom.accounts.store import load_accounts, user_store
from mailroom.api.routes.mail import download_name, served_type
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.mail.pool import DeadlineExceeded, MailPool
from tests.conftest import PASSWORD
from tests.test_api_accounts import new_account

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from mailroom_core.testing import ImapTestServer
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User

MAIL = "/api/v1/mail"
PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
# A 1x1 transparent PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
NO_REMOTE_CSP = (
    "sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; "
    "style-src 'unsafe-inline'; img-src data:; frame-ancestors 'self'; base-uri 'none'"
)


class Mailbox:
    """The signed-in user's account ``work`` on a fresh Dovecot user."""

    def __init__(self, client: TestClient, server: ImapTestServer, login: str) -> None:
        self.client = client
        self.server = server
        self.login = login

    def seed(self, message: str, folder: str = "INBOX", flags: tuple[str, ...] = ()) -> int:
        return seed(self.server, self.login, folder, message, flags)

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(f"{MAIL}{path}", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def search(self, **body: Any) -> Any:
        response = self.client.post(f"{MAIL}/search", json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def refs(self, folder: str = "INBOX") -> list[str]:
        page = self.get("/accounts/work/messages", folder=folder)
        return [item["ref"] for item in page["messages"]]


def endpoint(server: ImapTestServer) -> dict[str, Any]:
    return {"host": server.host, "port": server.port, "tls": "implicit"}


@pytest.fixture
def mailbox(
    user_client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> Mailbox:
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_ssl_context())
    login = imap_server.unique_user("mailapi")
    account = new_account(login=login, password=imap_server.password, imap=endpoint(imap_server))
    assert user_client.post("/api/v1/accounts", json=account).status_code == 201
    return Mailbox(user_client, imap_server, login)


def detail(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["detail"]["code"]


def test_signed_out_browsers_are_refused(api_client: TestClient) -> None:
    for path in (
        "/accounts/work/folders",
        "/accounts/work/messages",
        "/messages/x",
        "/messages/x/html",
        "/messages/x/parts/2",
    ):
        assert api_client.get(f"{MAIL}{path}").status_code == 401
    assert api_client.post(f"{MAIL}/search", json={}).status_code == 401


def test_folders_carry_roles_and_counts(mailbox: Mailbox) -> None:
    mailbox.seed(mime("one"))
    mailbox.seed(mime("two"), flags=("\\Seen",))
    listed = mailbox.get("/accounts/work/folders")
    assert listed["account_id"] == "work"
    folders = {folder["path"]: folder for folder in listed["folders"]}
    assert folders["INBOX"] == {"path": "INBOX", "role": "inbox", "unread": 1, "total": 2}
    assert folders["Junk"]["role"] == "junk"
    assert listed["folders"][0]["path"] == "INBOX"


def test_a_folder_pages_newest_first(mailbox: Mailbox) -> None:
    for n in range(3):
        mailbox.seed(
            mime(
                f"Message {n}",
                sender="Ada Lovelace <ada@example.org>",
                date=f"Thu, 0{n + 1} Oct 2026 09:00:00 +0200",
                text=f"Body number {n}.",
            ),
            flags=("\\Flagged",) if n == 0 else (),
        )
    first = mailbox.get("/accounts/work/messages", limit=2)
    assert [m["subject"] for m in first["messages"]] == ["Message 2", "Message 1"]
    assert first["failures"] == []
    item = first["messages"][0]
    assert item["from"] == {"name": "Ada Lovelace", "address": "ada@example.org"}
    assert item["date"] == "2026-10-03T09:00:00+02:00"
    assert item["flags"] == {"seen": False, "flagged": False, "answered": False, "draft": False}
    assert item["preview"] == "Body number 2."
    assert (item["account_id"], item["folder"], item["has_attachments"]) == (
        "work",
        "INBOX",
        False,
    )
    assert item["size"] > 0

    rest = mailbox.get("/accounts/work/messages", limit=2, cursor=first["next_cursor"])
    assert [m["subject"] for m in rest["messages"]] == ["Message 0"]
    assert rest["messages"][0]["flags"]["flagged"] is True
    assert rest["next_cursor"] is None


def test_list_refusals(mailbox: Mailbox) -> None:
    client = mailbox.client
    assert detail(client.get(f"{MAIL}/accounts/work/messages", params={"folder": "Nowhere"})) == (
        404,
        "mailbox_missing",
    )
    bad_cursor = client.get(f"{MAIL}/accounts/work/messages", params={"cursor": "nope"})
    assert detail(bad_cursor) == (400, "invalid_argument")
    too_many = client.get(f"{MAIL}/accounts/work/messages", params={"limit": 51})
    assert too_many.status_code == 422
    unknown = client.get(f"{MAIL}/accounts/home/messages")
    assert unknown.json() == {"detail": {"code": "not_found", "message": "No such account."}}
    assert detail(client.get(f"{MAIL}/accounts/home/folders")) == (404, "not_found")


def test_search_reads_the_fields_and_leaves_junk_out(mailbox: Mailbox) -> None:
    mailbox.seed(mime("Invoice March", sender="Shop <shop@example.org>"))
    mailbox.seed(mime("Invoice April", sender="Shop <shop@example.org>"), folder="Junk")
    mailbox.seed(mime("Holiday plans", sender="Ada <ada@example.org>"), flags=("\\Seen",))

    inbox = mailbox.search(account="work", subject="invoice")
    assert [m["subject"] for m in inbox["messages"]] == ["Invoice March"]
    everywhere = mailbox.search(folder="*", **{"from": "shop"})
    assert [m["subject"] for m in everywhere["messages"]] == ["Invoice March"]
    junk = mailbox.search(folder="Junk", subject="invoice")
    assert [m["subject"] for m in junk["messages"]] == ["Invoice April"]
    unread = mailbox.search(unread=True)
    assert [m["subject"] for m in unread["messages"]] == ["Invoice March"]
    # An empty field is no criterion, nor is null.
    assert len(mailbox.search(text="", subject=None)["messages"]) == 2

    def post(**body: Any) -> Any:
        return mailbox.client.post(f"{MAIL}/search", json=body)

    assert detail(post(account="home")) == (404, "not_found")
    assert post(since="2026-02-30").status_code == 422
    assert post(query="shop").status_code == 422  # an unknown field is refused
    assert post(sender="shop").status_code == 422  # the field is "from"
    for field in ("account", "folder", "limit"):
        assert post(**{field: None}).status_code == 422
    assert detail(post(account="work", gmail_query="x")) == (502, "unsupported")
    for term in ("a\r\nb", "a\nDELETE INBOX", "a\x00b"):
        assert detail(post(account="*", text=term)) == (400, "invalid_argument")
    # The criteria never reach the query string, so no access log sees them.
    assert mailbox.client.get(f"{MAIL}/search", params={"subject": "x"}).status_code == 405


def test_a_failing_account_is_a_failure_of_an_all_accounts_search(
    mailbox: Mailbox,
) -> None:
    mailbox.seed(mime("Kept"))
    closed = new_account("closed", login="closed@example.org")
    assert mailbox.client.post("/api/v1/accounts", json=closed).status_code == 201
    page = mailbox.search()
    assert [m["subject"] for m in page["messages"]] == ["Kept"]
    assert page["failures"] == [{"account_id": "closed", "folder": "INBOX", "code": "unreachable"}]
    # Over every folder it failed before one was chosen.
    page = mailbox.search(folder="*")
    assert [m["subject"] for m in page["messages"]] == ["Kept"]
    assert page["failures"] == [{"account_id": "closed", "folder": None, "code": "unreachable"}]
    # Searched alone, a single folder's failure is the answer, as for a list.
    closed_only = mailbox.client.post(f"{MAIL}/search", json={"account": "closed"})
    assert detail(closed_only) == (502, "unreachable")


def test_an_all_accounts_search_with_one_usable_account_still_answers_a_page(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom.mail import service

    closed = new_account("closed", login="closed@example.org")
    assert mailbox.client.post("/api/v1/accounts", json=closed).status_code == 201
    monkeypatch.setattr(
        service, "_missing_password", lambda _ctx, _user, account_id: account_id == "work"
    )
    for folder in ("INBOX", "*"):
        page = mailbox.search(folder=folder)
        assert page["messages"] == []
        assert page["failures"] == [
            {"account_id": "work", "folder": None, "code": "password_missing"},
            {
                "account_id": "closed",
                "folder": None if folder == "*" else "INBOX",
                "code": "unreachable",
            },
        ]


def test_a_slow_account_is_a_timeout_failure_of_an_all_accounts_search(
    mailbox: Mailbox, silent_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom.mail import service

    # Long enough for the healthy account under a loaded test run, far below the silent one's
    # 10 s connect timeout.
    monkeypatch.setattr(service, "SOURCES_SECONDS", 4.0)
    mailbox.seed(mime("Kept"))
    imap = {"host": "127.0.0.1", "port": silent_server, "tls": "implicit"}
    silent = new_account("silent", login="silent@example.org", imap=imap)
    assert mailbox.client.post("/api/v1/accounts", json=silent).status_code == 201
    started = time.monotonic()
    page = mailbox.search(folder="*")
    assert time.monotonic() - started < 9
    assert [m["subject"] for m in page["messages"]] == ["Kept"]
    assert page["failures"] == [{"account_id": "silent", "folder": None, "code": "timeout"}]


def test_a_message_reads_without_marking_it_seen(
    mailbox: Mailbox, caplog: pytest.LogCaptureFixture
) -> None:
    mailbox.seed(
        mime(
            "Notes on the engine",
            sender="Ada Lovelace <ada@example.org>",
            to="Charles <charles@example.org>",
            message_id="<a1b2@example.org>",
            text="Dear Charles,\n\nthe notes are attached.\n\n> old quoted text\n> more\n",
            attachments=[MimeAttachment("notes.pdf", "application/pdf", PDF)],
        )
    )
    [ref] = mailbox.refs()
    with caplog.at_level(logging.INFO, logger="mailroom.mail.service"):
        view = mailbox.get(f"/messages/{ref}")
    assert view["ref"] == ref
    assert view["from"] == {"name": "Ada Lovelace", "address": "ada@example.org"}
    assert view["to"] == [{"name": "Charles", "address": "charles@example.org"}]
    assert view["cc"] == []
    # The server fills an absent Reply-To from From (ENVELOPE, RFC 3501).
    assert view["reply_to"] == [view["from"]]
    assert view["subject"] == "Notes on the engine"
    assert view["message_id"] == "<a1b2@example.org>"
    assert view["text"]["content"].startswith("Dear Charles,")
    assert view["text"]["next_offset"] is None
    # The quote is left out without a marker; the flag says so.
    assert "old quoted text" not in view["text"]["content"]
    assert "quoted text" not in view["text"]["content"]
    assert view["text"]["quoted_omitted"] is True
    full = mailbox.get(f"/messages/{ref}", include_quoted="true")
    assert "> old quoted text" in full["text"]["content"]
    assert full["text"]["quoted_omitted"] is False
    assert (view["has_html"], view["has_remote_content"], view["calendar"]) == (False, False, None)
    assert view["attachments"] == [
        {
            "part": "2",
            "name": "notes.pdf",
            "type": "application/pdf",
            "size": view["attachments"][0]["size"],
        }
    ]
    assert "\\Seen" not in folder_contents(mailbox.server, mailbox.login, "INBOX")[0].flags

    record = next(r for r in caplog.records if r.msg == "mail_read")
    assert (record.__dict__["via"], record.__dict__["account_id"]) == ("web", "work")
    assert "Notes" not in str(record.__dict__)


def test_refs_of_other_accounts_and_stale_refs_are_refused(
    mailbox: Mailbox, db_session: Session, api_client: TestClient
) -> None:
    mailbox.seed(mime("Mine", text="secret"))
    [ref] = mailbox.refs()
    message = parse_ref(ref)
    client = mailbox.client

    stale = format_ref(MessageRef("work", "INBOX", message.uid_validity + 1, message.uid))
    for path in ("", "/html", "/parts/2"):
        assert detail(client.get(f"{MAIL}/messages/{stale}{path}")) == (409, "stale_reference")

    elsewhere = format_ref(MessageRef("home", "INBOX", message.uid_validity, message.uid))
    for path in ("", "/html", "/parts/2"):
        response = client.get(f"{MAIL}/messages/{elsewhere}{path}")
        assert response.json() == {
            "detail": {"code": "not_found", "message": "No such message or part."}
        }
    assert detail(client.get(f"{MAIL}/messages/garbage")) == (400, "invalid_argument")

    # Another user has no account "work": the ref is not theirs to read.
    bob = create_user(db_session, "bob", PASSWORD)
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, bob))
    assert detail(api_client.get(f"{MAIL}/messages/{ref}")) == (404, "not_found")
    assert detail(api_client.get(f"{MAIL}/accounts/work/folders")) == (404, "not_found")
    assert api_client.post(f"{MAIL}/search", json={}).json() == {
        "messages": [],
        "next_cursor": None,
        "failures": [],
    }


def related_html(html: str) -> str:
    boundary = "rel1"
    return (
        "From: Shop <shop@example.org>\r\nTo: b@example.test\r\nSubject: News\r\n"
        "MIME-Version: 1.0\r\n"
        f'Content-Type: multipart/related; boundary="{boundary}"\r\n\r\n'
        f"--{boundary}\r\nContent-Type: text/html; charset=utf-8\r\n"
        f"Content-Transfer-Encoding: quoted-printable\r\n\r\n{html.replace('=', '=3D')}\r\n"
        f"--{boundary}\r\nContent-Type: image/png\r\nContent-ID: <logo@x>\r\n"
        f"Content-Transfer-Encoding: base64\r\n\r\n{base64.b64encode(PNG).decode()}\r\n"
        f"--{boundary}--\r\n"
    )


def test_the_html_view_is_a_sanitised_standalone_document(mailbox: Mailbox) -> None:
    mailbox.seed(
        related_html(
            '<p onclick="steal()">Hi <img src="cid:logo@x"> '
            '<img src="https://t.example/pixel.gif"></p><script>alert(1)</script>'
            '<form action="https://x.example"><input name="p"></form>'
            '<base href="https://evil.example/"><meta http-equiv="refresh" content="0;url=x">'
            '<div style="background:url(https://t.example/bg.png)">styled</div>'
            '<a href="https://example.org/">link</a>'
        )
    )
    [ref] = mailbox.refs()
    view = mailbox.get(f"/messages/{ref}")
    assert (view["has_html"], view["has_remote_content"]) == (True, True)
    # The logo the HTML shows in place is no attachment.
    assert view["attachments"] == []
    assert mailbox.get("/accounts/work/messages")["messages"][0]["has_attachments"] is False

    response = mailbox.client.get(f"{MAIL}/messages/{ref}/html")
    assert response.status_code == 200
    headers = response.headers
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert headers["content-security-policy"] == NO_REMOTE_CSP
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "private, no-store"
    page = response.text
    assert page.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in page
    assert '<base target="_blank">' in page
    body = page.split("<body>", 1)[1]
    assert "data:image/png;base64," in body
    for dropped in ("t.example", "<script", "alert(1)", "onclick", "<form", "<input", "evil."):
        assert dropped not in body
    assert "refresh" not in body
    assert "styled" in body
    assert 'href="https://example.org/"' in body

    shown = mailbox.client.get(f"{MAIL}/messages/{ref}/html", params={"remote": "1"})
    assert shown.headers["content-security-policy"] == NO_REMOTE_CSP.replace(
        "img-src data:;", "img-src data: http: https:;"
    )
    assert "https://t.example/pixel.gif" in shown.text
    assert "bg.png" not in shown.text


def test_a_message_without_html_has_no_html_view(mailbox: Mailbox) -> None:
    mailbox.seed(mime("plain", text="hello"))
    [ref] = mailbox.refs()
    assert detail(mailbox.client.get(f"{MAIL}/messages/{ref}/html")) == (404, "not_found")


def test_parts_download_as_files(mailbox: Mailbox, migrated_engine: Any, user: User) -> None:
    mailbox.seed(
        mime(
            "Files",
            text="see attached",
            attachments=[
                MimeAttachment("Résumé.pdf", "application/pdf", PDF),
                MimeAttachment("page.html", "text/html", b"<script>alert(1)</script>"),
                MimeAttachment("logo.svg", "image/svg+xml", b"<svg onload='x()'></svg>"),
                MimeAttachment("fake.png", "image/png", b"<html>not a png</html>"),
                MimeAttachment("notes.txt", "text/plain", b"plain words"),
            ],
        )
    )
    [ref] = mailbox.refs()
    client = mailbox.client

    pdf = client.get(f"{MAIL}/messages/{ref}/parts/2")
    assert pdf.status_code == 200
    assert pdf.content == PDF
    assert pdf.headers["content-disposition"] == "attachment; filename*=UTF-8''R%C3%A9sum%C3%A9.pdf"
    assert pdf.headers["content-type"] == "application/octet-stream"
    assert pdf.headers["x-content-type-options"] == "nosniff"
    assert pdf.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert pdf.headers["cache-control"] == "private, no-store"
    for part in ("3", "4", "5"):
        response = client.get(f"{MAIL}/messages/{ref}/parts/{part}")
        assert response.headers["content-type"] == "application/octet-stream", part
    assert (
        client.get(f"{MAIL}/messages/{ref}/parts/6")
        .headers["content-type"]
        .startswith("text/plain")
    )

    assert detail(client.get(f"{MAIL}/messages/{ref}/parts/1")) == (404, "not_found")
    assert detail(client.get(f"{MAIL}/messages/{ref}/parts/9")) == (404, "not_found")
    assert client.get(f"{MAIL}/messages/{ref}/parts/2a").status_code == 422

    store = user_store(migrated_engine, user.id)
    current = load_accounts(store)
    store.save_settings({**current.doc, "limits": {"attachment_max_mb": 0.00001}}, current.revision)
    assert detail(client.get(f"{MAIL}/messages/{ref}/parts/2")) == (413, "too_large")


def test_download_names_and_types() -> None:
    assert download_name("../../etc/passwd", "2") == "_.._etc_passwd"
    # Nothing a header could break on: control characters go, the rest is percent-encoded.
    tricky = download_name('a\r\nSet-Cookie: x"; b.pdf', "2")
    assert tricky == 'aSet-Cookie: x"; b.pdf'
    assert quote(tricky, safe="") == "aSet-Cookie%3A%20x%22%3B%20b.pdf"
    assert download_name("a" + chr(0x202E) + "exe.pdf\x00", "2") == "aexe.pdf"
    assert download_name(None, "1.2") == "attachment-1.2"
    assert download_name("  ..  ", "3") == "attachment-3"
    assert len(download_name("x" * 400, "2")) == 150
    assert served_type("image/png") == "image/png"
    for kind in ("text/html", "image/svg+xml", "application/pdf", "application/xml"):
        assert served_type(kind) == "application/octet-stream"


def test_an_unreachable_server_is_refused(user_client: TestClient) -> None:
    """Port 9 on loopback is closed."""
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    response = user_client.get(f"{MAIL}/accounts/work/folders")
    assert detail(response) == (502, "unreachable")
    assert response.json()["detail"]["message"] == (
        "The mail server for this account could not be reached."
    )


def test_a_silent_server_times_out_without_stalling_the_api(
    user_client: TestClient,
    application: FastAPI,
    silent_server: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool: MailPool = application.state.mail_pool
    monkeypatch.setattr(pool, "deadline_seconds", 1.5)
    imap = {"host": "127.0.0.1", "port": silent_server, "tls": "implicit"}
    assert user_client.post("/api/v1/accounts", json=new_account(imap=imap)).status_code == 201

    answers: dict[str, Any] = {}

    def slow() -> None:
        answers["folders"] = user_client.get(f"{MAIL}/accounts/work/folders")

    started = time.monotonic()
    waiting = threading.Thread(target=slow)
    waiting.start()
    time.sleep(0.3)
    healthz = user_client.get("/healthz")
    assert healthz.status_code == 200
    assert time.monotonic() - started < 1.2
    waiting.join(10)
    assert detail(answers["folders"]) == (504, "timeout")
    assert (
        answers["folders"].json()["detail"]["message"] == "The mail server did not answer in time."
    )


def test_changing_an_account_drops_its_connections(mailbox: Mailbox) -> None:
    mailbox.seed(mime("one"))
    client = mailbox.client
    assert client.get(f"{MAIL}/accounts/work/folders").status_code == 200
    account = new_account(login=mailbox.login, imap=endpoint(mailbox.server))
    change = {k: v for k, v in account.items() if k not in {"id", "password"}}
    change["password"] = "wrong password"
    assert client.put("/api/v1/accounts/work", json=change).status_code == 200
    # The open connection logged in with the old password; it went with the change.
    assert detail(client.get(f"{MAIL}/accounts/work/folders")) == (409, "auth_failed")

    change["password"] = mailbox.server.password
    assert client.put("/api/v1/accounts/work", json=change).status_code == 200
    assert client.get(f"{MAIL}/accounts/work/folders").status_code == 200
    assert client.delete("/api/v1/accounts/work").status_code == 204
    assert detail(client.get(f"{MAIL}/accounts/work/folders")) == (404, "not_found")


class _Connections:
    def __init__(self) -> None:
        self.closed: list[str | None] = []

    def close(self, account_id: str) -> None:
        self.closed.append(account_id)

    def close_all(self) -> None:
        self.closed.append(None)


def test_the_pool_closes_idle_users_and_rebuilds_on_a_new_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = MailPool(idle_seconds=60)
    made: list[_Connections] = []

    def connections(_ctx: object, _user_id: int) -> _Connections:
        made.append(_Connections())
        return made[-1]

    monkeypatch.setattr(pool, "_connections", connections)
    monkeypatch.setattr(pool, "_start_sweeper", lambda: None)
    ctx: Any = object()
    try:
        with pool.lease(ctx, 1, revision=3) as first:
            assert pool.close_idle(now=time.monotonic() + 3600) == 0  # in use
        with pool.lease(ctx, 1, revision=3) as again:
            assert again is first
        with pool.lease(ctx, 1, revision=4) as rebuilt:
            assert rebuilt is not first
        # A caller that read the settings before revision 4 reuses its connections.
        with pool.lease(ctx, 1, revision=3) as older:
            assert older is rebuilt
        assert made[0].closed == [None]
        assert len(made) == 2
        pool.invalidate(1, "work")
        assert made[1].closed == ["work"]
        assert pool.close_idle() == 0
        assert pool.close_idle(now=time.monotonic() + 61) == 1
        assert made[1].closed == ["work", None]
    finally:
        pool.close()


class _Cuttable:
    """Connections whose ``interrupt`` ends the work blocked on ``released``."""

    def __init__(self) -> None:
        self.released = threading.Event()
        self.interrupted: list[str] = []

    def interrupt(self, account_id: str) -> int:
        self.interrupted.append(account_id)
        self.released.set()
        return 1


def test_the_pool_gates_a_users_calls_before_taking_a_thread() -> None:
    pool = MailPool(gate_seconds=0.2, workers=16)
    connections: Any = _Cuttable()
    started = threading.Semaphore(0)

    def blocked() -> str:
        started.release()
        connections.released.wait(5)
        return "done"

    def call(account: str) -> Any:
        try:
            return pool.call(connections, 1, [account], blocked)
        except MailError as error:
            return error.code

    try:
        # The account's two connections are in use: a third call is busy, without a thread.
        running = [threading.Thread(target=call, args=("a",)) for _ in range(2)]
        for thread in running:
            thread.start()
        for _ in running:
            assert started.acquire(timeout=5)
        assert call("a") == "busy"
        # The user may still use another account, up to four calls in all.
        others = [threading.Thread(target=call, args=(account,)) for account in ("b", "c")]
        for thread in others:
            thread.start()
        for _ in others:
            assert started.acquire(timeout=5)
        assert call("d") == "busy"
        # Another user is not held up.
        assert pool.call(connections, 2, ["a"], lambda: "other") == "other"
        connections.released.set()
        for thread in running + others:
            thread.join(5)
        assert pool.call(connections, 1, ["a"], lambda: "free again") == "free again"
    finally:
        pool.close()


def test_the_deadline_cuts_the_accounts_connections_and_frees_the_gate() -> None:
    pool = MailPool(deadline_seconds=0.3)
    connections: Any = _Cuttable()
    ended = threading.Event()

    def stuck() -> None:
        connections.released.wait(5)
        ended.set()

    try:
        with pytest.raises(DeadlineExceeded):
            pool.call(connections, 1, ["a"], stuck)
        assert connections.interrupted == ["a"]
        assert ended.wait(5)
        for _ in range(2):  # both of the account's gate places are free again
            assert pool.call(connections, 1, ["a"], lambda: 1) == 1
    finally:
        pool.close()


def test_the_process_gate_bounds_every_users_calls_at_once() -> None:
    pool = MailPool(process_calls=2)
    connections: Any = _Cuttable()
    started = threading.Semaphore(0)

    def blocked() -> str:
        started.release()
        connections.released.wait(5)
        return "done"

    try:
        running = [
            threading.Thread(target=pool.call, args=(connections, user_id, ["a"], blocked))
            for user_id in (1, 2)
        ]
        for thread in running:
            thread.start()
        for _ in running:
            assert started.acquire(timeout=5)
        began = time.monotonic()
        with pytest.raises(MailError) as refused:
            pool.call(connections, 3, ["a"], lambda: "never")
        assert refused.value.code == "busy"
        assert time.monotonic() - began < 0.5  # at once, without waiting
        connections.released.set()
        for thread in running:
            thread.join(5)
        assert pool.call(connections, 3, ["a"], lambda: "free again") == "free again"
    finally:
        pool.close()


def test_both_gates_share_one_wait() -> None:
    pool = MailPool(gate_seconds=1.0)
    connections: Any = _Cuttable()
    started = threading.Semaphore(0)
    releases = {account: threading.Event() for account in ("a", "b", "c")}

    def blocked(account: str) -> Callable[[], str]:
        def work() -> str:
            started.release()
            releases[account].wait(5)
            return account

        return work

    try:
        # The user's four places: two on "a" (its gate full too), one on "b", one on "c".
        running = [
            threading.Thread(target=pool.call, args=(connections, 1, [account], blocked(account)))
            for account in ("a", "a", "b", "c")
        ]
        for thread in running:
            thread.start()
        for _ in running:
            assert started.acquire(timeout=5)
        # The user's gate opens after 0.6 s; "a"'s never does. Waiting 1 s at each gate would
        # answer after 1.6 s.
        threading.Timer(0.6, releases["b"].set).start()
        began = time.monotonic()
        with pytest.raises(MailError) as refused:
            pool.call(connections, 1, ["a"], lambda: "never")
        waited = time.monotonic() - began
        assert refused.value.code == "busy"
        assert 0.9 < waited < 1.4
    finally:
        for release in releases.values():
            release.set()
        for thread in running:
            thread.join(5)
        pool.close()


def test_the_pool_forgets_the_gates_of_users_it_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = MailPool(idle_seconds=60)
    monkeypatch.setattr(pool, "_connections", lambda _ctx, _user_id: _Connections())
    monkeypatch.setattr(pool, "_start_sweeper", lambda: None)
    connections: Any = _Cuttable()
    ctx: Any = object()
    try:
        for user_id in (1, 2):
            with pool.lease(ctx, user_id, revision=1):
                assert pool.call(connections, user_id, ["a"], lambda: 1) == 1
        assert pool.gate_count() == 4  # each user's, and each one's account "a"
        pool.invalidate(1)
        assert pool.gate_count() == 2
        assert pool.close_idle(now=time.monotonic() + 61) == 1
        assert pool.gate_count() == 0

        # A gate still in use is kept: its holder lets it go on that same gate.
        release = threading.Event()
        held = threading.Thread(target=pool.call, args=(connections, 3, ["a"], release.wait))
        held.start()
        deadline = time.monotonic() + 5
        while pool.gate_count() < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        pool.invalidate(3)
        assert pool.gate_count() == 2
        release.set()
        held.join(5)
    finally:
        pool.close()


class _SearchConnections:
    """Two accounts: ``fast`` answers at once, ``slow`` blocks until it is interrupted."""

    def __init__(self) -> None:
        self.cut = threading.Event()
        self.interrupted: list[str] = []

    def with_mailbox(self, account_id: str, _path: str, _read_only: bool, _fn: Any) -> Any:
        if account_id == "slow":
            self.cut.wait(5)
            raise MailError("unreachable")
        return []

    def interrupt(self, account_id: str) -> int:
        self.interrupted.append(account_id)
        if account_id == "slow":
            self.cut.set()
        return 1


def test_an_all_accounts_search_cuts_only_the_accounts_still_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mailroom.mail import service

    monkeypatch.setattr(service, "ACCOUNT_SECONDS", 0.3)
    connections: Any = _SearchConnections()
    page = service._search_accounts(  # noqa: SLF001 - the per-account deadline, without IMAP
        connections, ["fast", "slow"], "INBOX", SearchQuery(), limit=10, cursor=None
    )
    # "fast" was done long before: another call of the user on it is not cut.
    assert connections.interrupted == ["slow"]
    assert [(f.account, f.folder, f.code) for f in page.failures] == [("slow", "INBOX", "timeout")]


def test_an_unexpected_failure_is_a_failed_refusal(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from mailroom.mail import service

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        msg = "a bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(service.core, "list_messages", broken)
    with caplog.at_level(logging.INFO, logger="mailroom.mail.service"):
        response = mailbox.client.get(f"{MAIL}/accounts/work/messages")
    assert detail(response) == (500, "failed")
    [record] = [r for r in caplog.records if r.msg == "mail_listed"]
    assert record.__dict__["outcome"] == "failed"


def test_a_password_removed_once_the_call_began_is_password_missing(
    mailbox: Mailbox, migrated_engine: Any, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mailroom-core reports the pool's ``PasswordMissing`` from inside a page read as the
    source's ``server_error``; the service answers what it is."""
    from mailroom.mail import pool

    real = pool.account_password

    def removed_meanwhile(store: Any, subkey: Any, account_id: str) -> str | None:
        current = load_accounts(store)
        store.save_settings(current.doc, current.revision, SecretChanges(remove=[account_id]))
        return real(store, subkey, account_id)

    monkeypatch.setattr(pool, "account_password", removed_meanwhile)
    response = mailbox.client.get(f"{MAIL}/accounts/work/messages")
    assert detail(response) == (409, "password_missing")
    assert user_store(migrated_engine, user.id).secrets() == {}


def test_an_all_accounts_search_reports_a_password_removed_meanwhile(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom.mail import pool

    real = pool.account_password

    def removed_meanwhile(store: Any, subkey: Any, account_id: str) -> str | None:
        current = load_accounts(store)
        store.save_settings(current.doc, current.revision, SecretChanges(remove=[account_id]))
        return real(store, subkey, account_id)

    monkeypatch.setattr(pool, "account_password", removed_meanwhile)
    page = mailbox.search()
    assert page["failures"] == [
        {"account_id": "work", "folder": "INBOX", "code": "password_missing"}
    ]


def test_a_password_that_does_not_open_is_refused_before_connecting(
    mailbox: Mailbox, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(application.state, "secrets_subkey", secrets_subkey(os.urandom(32)))
    for path in ("/accounts/work/folders", "/accounts/work/messages"):
        response = mailbox.client.get(f"{MAIL}{path}")
        assert detail(response) == (409, "password_missing")
    one = mailbox.client.post(f"{MAIL}/search", json={"account": "work"})
    assert detail(one) == (409, "password_missing")


def test_without_the_secrets_key_mail_is_unavailable(
    mailbox: Mailbox, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox.seed(mime("one"))
    [ref] = mailbox.refs()
    monkeypatch.setattr(application.state, "secrets_subkey", None)
    for path in ("/accounts/work/folders", "/accounts/work/messages", f"/messages/{ref}"):
        assert detail(mailbox.client.get(f"{MAIL}{path}")) == (503, "secrets_key_missing")
    assert detail(mailbox.client.post(f"{MAIL}/search", json={})) == (503, "secrets_key_missing")


def test_a_full_process_answers_busy(
    mailbox: Mailbox, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = MailPool(process_calls=0)
    monkeypatch.setattr(application.state, "mail_pool", full)
    try:
        response = mailbox.client.get(f"{MAIL}/accounts/work/folders")
    finally:
        full.close()
    assert detail(response) == (429, "busy")
    assert response.json()["detail"]["message"] == MESSAGES["busy"]


def test_mail_answers_are_never_cached(mailbox: Mailbox) -> None:
    mailbox.seed(mime("one", text="hello"))
    [ref] = mailbox.refs()
    client = mailbox.client
    for response in (
        client.get(f"{MAIL}/accounts/work/folders"),
        client.get(f"{MAIL}/accounts/work/messages"),
        client.post(f"{MAIL}/search", json={}),
        client.get(f"{MAIL}/messages/{ref}"),
    ):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
    refused = client.get(f"{MAIL}/messages/{ref}/html")
    assert detail(refused) == (404, "not_found")
    assert refused.headers["cache-control"] == "private, no-store"
    missing = client.get(f"{MAIL}/accounts/home/folders")
    assert missing.json() == {"detail": {"code": "not_found", "message": "No such account."}}
    assert missing.headers["cache-control"] == "private, no-store"
