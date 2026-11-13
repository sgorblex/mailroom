"""The mail client's REST routes (``docs/requirements/mail-client.md``) against the Dovecot
``IMAP_TEST_HOST`` names (``mise run test-backend-mail`` starts one; ``tests/support/mail.py``
says which TLS it trusts); skipped without it. Each test logs in as a fresh mailbox user, seeded
with mailroom-core's testing helpers."""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import pytest

from mailroom.accounts.store import load_accounts, user_store
from mailroom.api.routes.mail.read import download_name, served_type
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.mail.pool import DeadlineExceeded, MailPool
from mailroom.models import Filter, RuleSet
from mailroom_core.crypto import secrets_subkey
from mailroom_core.mail import (
    MESSAGES,
    Capabilities,
    MailConnections,
    MailError,
    MessageRef,
    Page,
    SearchQuery,
    format_ref,
    parse_ref,
)
from mailroom_core.store import SecretChanges
from mailroom_core.testing import (
    MimeAttachment,
    create_folders,
    folder_contents,
    mime,
    raw_client,
    seed,
)
from tests.conftest import PASSWORD
from tests.support.mail import mail_servers_ssl_context
from tests.test_api_accounts import new_account

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import AuditRecord, User
    from mailroom_core.mail import Folder
    from mailroom_core.testing import ImapTestServer

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

    def organize(self, action: str, **body: Any) -> Any:
        """``POST /messages/<action>``, answered 200."""
        response = self.client.post(f"{MAIL}/messages/{action}", json=body)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        return response.json()["results"]


def endpoint(server: ImapTestServer) -> dict[str, Any]:
    return {"host": server.host, "port": server.port, "tls": "implicit"}


@pytest.fixture
def mailbox(
    user_client: TestClient,
    imap_server: ImapTestServer,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> Mailbox:
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    login = imap_server.unique_user("mailapi")
    account = new_account(login=login, password=imap_server.password, imap=endpoint(imap_server))
    assert user_client.post("/api/v1/accounts", json=account).status_code == 201
    return Mailbox(user_client, imap_server, login)


def detail(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["detail"]["code"]


def test_signed_out_browsers_are_refused_uncached(api_client: TestClient) -> None:
    refused = [
        api_client.get(f"{MAIL}{path}")
        for path in (
            "/accounts/work/folders",
            "/accounts/work/messages",
            "/messages/x",
            "/messages/x/html",
            "/messages/x/parts/2",
        )
    ]
    refused += [
        api_client.post(f"{MAIL}{path}", json={"refs": ["x"], "folder": "Archive"})
        for path in ("/search", "/messages/move", "/messages/archive", "/messages/trash")
    ]
    refused.append(api_client.post(f"{MAIL}/messages/flag", json={"refs": ["x"], "seen": True}))
    for response in refused:
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"
    # Only the mail routes: the rest of the API keeps its own caching.
    assert "cache-control" not in api_client.get("/api/v1/accounts").headers


def test_folders_carry_roles_and_counts(mailbox: Mailbox) -> None:
    mailbox.seed(mime("one"))
    mailbox.seed(mime("two"), flags=("\\Seen",))
    listed = mailbox.get("/accounts/work/folders")
    assert listed["account_id"] == "work"
    folders = {folder["path"]: folder for folder in listed["folders"]}
    assert folders["INBOX"] == {
        "path": "INBOX",
        "role": "inbox",
        "unread": 1,
        "total": 2,
        "delimiter": "/",
        "selectable": True,
    }
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
    from mailroom.mail.service import read as service_read

    closed = new_account("closed", login="closed@example.org")
    assert mailbox.client.post("/api/v1/accounts", json=closed).status_code == 201
    monkeypatch.setattr(
        service_read, "_missing_password", lambda _ctx, _user, account_id: account_id == "work"
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
    from mailroom.mail.service import read as service_read

    # Long enough for the healthy account under a loaded test run, far below the silent one's
    # 10 s connect timeout.
    monkeypatch.setattr(service_read, "SOURCES_SECONDS", 4.0)
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

    cut_before: list[list[str]] = []
    try:
        with pytest.raises(DeadlineExceeded):
            pool.call(
                connections,
                1,
                ["a"],
                stuck,
                on_deadline=lambda: cut_before.append(list(connections.interrupted)),
            )
        assert cut_before == [[]]  # told before anything is cut
        assert connections.interrupted == ["a"]
        assert ended.wait(5)
        for _ in range(2):  # both of the account's gate places are free again
            assert pool.call(connections, 1, ["a"], lambda: 1) == 1
    finally:
        pool.close()


def test_a_call_may_have_a_deadline_of_its_own() -> None:
    pool = MailPool(deadline_seconds=30.0)
    connections: Any = _Cuttable()
    try:
        started = time.monotonic()
        with pytest.raises(DeadlineExceeded):
            pool.call(connections, 1, ["a"], lambda: connections.released.wait(5), deadline=0.2)
        assert time.monotonic() - started < 5
        assert connections.interrupted == ["a"]
    finally:
        pool.close()


def test_the_work_s_own_timeout_error_is_no_deadline() -> None:
    """A filter's MatchTimeoutError is a TimeoutError, as concurrent.futures' own is."""
    pool = MailPool(deadline_seconds=30.0)
    connections: Any = _Cuttable()

    def times_out() -> None:
        msg = "the work's own"
        raise TimeoutError(msg)

    try:
        with pytest.raises(TimeoutError, match="the work's own"):
            pool.call(connections, 1, ["a"], times_out)
        assert connections.interrupted == []
    finally:
        pool.close()


def test_the_account_gate_is_as_wide_as_the_pool_s_connections_per_account() -> None:
    pool = MailPool(gate_seconds=0.2, connections_per_account=1)
    connections: Any = _Cuttable()
    started = threading.Event()

    def blocked() -> None:
        started.set()
        connections.released.wait(5)

    running = threading.Thread(target=pool.call, args=(connections, 1, ["a"], blocked))
    try:
        running.start()
        assert started.wait(5)
        with pytest.raises(MailError) as busy:
            pool.call(connections, 1, ["a"], lambda: None)
        assert busy.value.code == "busy"
    finally:
        connections.released.set()
        running.join(5)
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
    from mailroom.mail.service import read as service_read

    monkeypatch.setattr(service_read, "ACCOUNT_SECONDS", 0.3)
    connections: Any = _SearchConnections()
    page = service_read._search_accounts(  # noqa: SLF001 - the per-account deadline, without IMAP
        connections, ["fast", "slow"], "INBOX", SearchQuery(), limit=10, cursor=None
    )
    # "fast" was done long before: another call of the user on it is not cut.
    assert connections.interrupted == ["slow"]
    assert [(f.account, f.folder, f.code) for f in page.failures] == [("slow", "INBOX", "timeout")]


class _GmailConnections:
    """``gmail`` has Gmail's extensions and ``plain`` has not; any other call fails the test."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def capabilities(self, account_id: str) -> Capabilities:
        self.asked.append(account_id)
        gmail = account_id == "gmail"
        return Capabilities(
            gmail=gmail, thread=gmail, move=True, uidplus=True, condstore=True, special_use=True
        )


def test_an_all_accounts_search_asks_gmail_for_attachments_its_own_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mailroom.mail.service import read as service_read

    read: dict[str, Any] = {}

    def read_page(_connections: Any, sources: Any, criteria: Any, **_kwargs: Any) -> Page:
        read.update(sources=list(sources), criteria=criteria)
        return Page((), None, ())

    monkeypatch.setattr(service_read.core, "read_page", read_page)
    connections: Any = _GmailConnections()
    service_read._search_accounts(  # noqa: SLF001 - the criteria per account, without IMAP
        connections,
        ["gmail", "plain"],
        "INBOX",
        SearchQuery(subject="invoice", has_attachment=True),
        limit=10,
        cursor=None,
    )
    assert sorted(connections.asked) == ["gmail", "plain"]
    # Gmail ignores a search of the Content-Type header: X-GM-RAW there, as core's own search.
    assert read["criteria"] == {
        "gmail": ["SUBJECT", "invoice", "X-GM-RAW", "has:attachment"],
        "plain": ["SUBJECT", "invoice", "HEADER", "Content-Type", "multipart/mixed"],
    }
    assert [(source.account, source.folder) for source in read["sources"]] == [
        ("gmail", "INBOX"),
        ("plain", "INBOX"),
    ]

    # A query that needs nothing of Gmail's does not ask.
    connections.asked.clear()
    service_read._search_accounts(  # noqa: SLF001
        connections, ["gmail", "plain"], "INBOX", SearchQuery(unread=True), limit=10, cursor=None
    )
    assert connections.asked == []
    assert read["criteria"] == {"gmail": ["UNSEEN"], "plain": ["UNSEEN"]}


class _Sessions:
    """A connection's sessions without a mailbox, and its folder lists, counted; ``readied``
    runs while a session readies, before its ``fn``."""

    def __init__(self) -> None:
        self.sessions = 0
        self.lists = 0
        self.readied: Callable[[], None] = lambda: None

    def with_connection(self, _account_id: str, fn: Callable[[object], Any]) -> Any:
        self.sessions += 1
        self.readied()
        return fn(object())

    def folders(self, _account_id: str, *, with_status: bool = False) -> list[Folder]:
        self.lists += 1
        return []


def test_a_folder_call_past_its_deadline_opens_no_session_but_may_confirm_a_write() -> None:
    from mailroom.mail.service import _shared as service_shared

    sessions = _Sessions()
    stop = threading.Event()
    stoppable = service_shared._Stoppable(  # noqa: SLF001 - the deadline's stop, without IMAP
        cast("MailConnections", sessions), stop, confirming=True
    )
    ran: list[str] = []
    assert stoppable.with_connection("work", lambda _client: "done") == "done"
    sessions.readied = stop.set  # the deadline passes while the session readies
    with pytest.raises(MailError) as cut:
        stoppable.with_connection("work", lambda _client: ran.append("fn"))
    assert (cut.value.code, ran, sessions.sessions) == ("timeout", [], 2)
    with pytest.raises(MailError):
        stoppable.with_connection("work", lambda _client: ran.append("fn"))
    assert (ran, sessions.sessions) == ([], 2)
    assert stoppable.folders("work") == []
    plain = service_shared._Stoppable(cast("MailConnections", sessions), stop)  # noqa: SLF001
    with pytest.raises(MailError):
        plain.folders("work")
    assert sessions.lists == 1


def test_an_unexpected_failure_is_a_failed_refusal(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from mailroom.mail.service import read as service_read

    def broken(*_args: Any, **_kwargs: Any) -> Any:
        msg = "a bug"
        raise RuntimeError(msg)

    monkeypatch.setattr(service_read.core, "list_messages", broken)
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
    from mailroom.mail import connections

    real = connections.account_password

    def removed_meanwhile(store: Any, subkey: Any, account_id: str) -> str | None:
        current = load_accounts(store)
        store.save_settings(current.doc, current.revision, SecretChanges(remove=[account_id]))
        return real(store, subkey, account_id)

    monkeypatch.setattr(connections, "account_password", removed_meanwhile)
    response = mailbox.client.get(f"{MAIL}/accounts/work/messages")
    assert detail(response) == (409, "password_missing")
    assert user_store(migrated_engine, user.id).secrets() == {}


def test_an_all_accounts_search_reports_a_password_removed_meanwhile(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom.mail import connections

    real = connections.account_password

    def removed_meanwhile(store: Any, subkey: Any, account_id: str) -> str | None:
        current = load_accounts(store)
        store.save_settings(current.doc, current.revision, SecretChanges(remove=[account_id]))
        return real(store, subkey, account_id)

    monkeypatch.setattr(connections, "account_password", removed_meanwhile)
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
    invalid = client.get(f"{MAIL}/accounts/work/messages", params={"limit": 0})
    assert invalid.status_code == 422
    assert invalid.headers["cache-control"] == "private, no-store"


def subjects(mailbox: Mailbox, folder: str) -> list[str]:
    return sorted(m.subject for m in folder_contents(mailbox.server, mailbox.login, folder))


def create_archive(mailbox: Mailbox) -> None:
    """Creates the mailbox's Archive folder unless the server did: the dev Dovecot does, the
    throwaway servers of ``scripts/test-mail.sh`` and CI do not (the role is then found by name)."""
    with raw_client(mailbox.server, mailbox.login) as client:
        if not client.folder_exists("Archive"):
            client.create_folder("Archive")


def test_a_move_answers_the_new_refs_and_the_old_ones_are_gone(
    mailbox: Mailbox, caplog: pytest.LogCaptureFixture
) -> None:
    create_archive(mailbox)
    mailbox.seed(mime("file me", sender="Ada <ada@example.org>"))
    mailbox.seed(mime("and me"))
    refs = mailbox.refs()
    with caplog.at_level(logging.INFO, logger="mailroom.mail.service"):
        [group] = mailbox.organize("move", refs=refs, folder="Archive")
    assert {k: v for k, v in group.items() if k != "new_refs"} == {
        "account": "work",
        "folder": "INBOX",
        "ok": True,
        "count": 2,
        "junk": None,
        "flags": [],
        "error": None,
    }
    assert sorted(mailbox.get(f"/messages/{ref}")["subject"] for ref in group["new_refs"]) == [
        "and me",
        "file me",
    ]
    assert subjects(mailbox, "INBOX") == []
    assert subjects(mailbox, "Archive") == ["and me", "file me"]

    gone = mailbox.client.get(f"{MAIL}/messages/{refs[0]}")
    assert detail(gone) == (404, "not_found")
    # Nothing moved: the refusal of the only group, its text naming the count.
    again = mailbox.client.post(f"{MAIL}/messages/move", json={"refs": refs, "folder": "Junk"})
    assert detail(again) == (404, "not_found")
    assert again.json()["detail"]["message"].endswith(
        "2 of these messages are no longer in INBOX; search again."
    )
    assert again.headers["cache-control"] == "private, no-store"

    record = next(r for r in caplog.records if r.msg == "mail_moved")
    logged = {
        key: record.__dict__[key]
        for key in (
            "via",
            "account_ids",
            "ref_count",
            "changed",
            "groups",
            "failed_groups",
            "codes",
            "outcome",
        )
    }
    assert logged == {
        "via": "web",
        "account_ids": ["work"],
        "ref_count": 2,
        "changed": 2,
        "groups": 1,
        "failed_groups": 0,
        "codes": [],
        "outcome": "ok",
    }
    assert isinstance(record.__dict__["duration_ms"], int)
    for text in ("file me", "ada@example.org", "Archive", "INBOX", refs[0]):
        assert text not in str(record.__dict__)


def test_moves_into_drafts_sent_or_where_they_are_are_refused(mailbox: Mailbox) -> None:
    mailbox.seed(mime("stay"))
    refs = mailbox.refs()
    for folder, code, status in (
        ("Drafts", "invalid_argument", 400),
        ("Sent", "invalid_argument", 400),
        ("inbox", "invalid_argument", 400),  # INBOX in any case
        ("Nowhere", "mailbox_missing", 404),
    ):
        response = mailbox.client.post(
            f"{MAIL}/messages/move", json={"refs": refs, "folder": folder}
        )
        assert detail(response) == (status, code), folder
    assert subjects(mailbox, "INBOX") == ["stay"]


def test_a_batch_over_two_folders_answers_each_group(mailbox: Mailbox) -> None:
    mailbox.seed(mime("offer"))
    mailbox.seed(mime("already junk"), folder="Junk")
    refs = mailbox.refs() + mailbox.refs("Junk")
    inbox, junk = mailbox.organize("move", refs=refs, folder="Junk")
    assert (inbox["folder"], inbox["ok"], inbox["count"], inbox["junk"]) == (
        "INBOX",
        True,
        1,
        "spam",
    )
    assert junk["folder"] == "Junk"
    assert (junk["ok"], junk["count"], junk["new_refs"]) == (False, 0, None)
    assert junk["error"] == {
        "code": "invalid_argument",
        "message": "Invalid argument. They are already in that folder.",
    }
    assert subjects(mailbox, "Junk") == ["already junk", "offer"]

    [back] = mailbox.organize("move", refs=mailbox.refs("Junk")[:1], folder="INBOX")
    assert back["junk"] == "ham"

    # Every group failed: the refusal is the first group's (a stale INBOX ref), not the second's
    # (already in Junk), and nothing is written.
    before = (subjects(mailbox, "INBOX"), subjects(mailbox, "Junk"))
    [kept] = mailbox.refs()
    message = parse_ref(kept)
    stale = format_ref(MessageRef("work", "INBOX", message.uid_validity + 1, message.uid))
    refused = mailbox.client.post(
        f"{MAIL}/messages/move", json={"refs": [stale, *mailbox.refs("Junk")], "folder": "Junk"}
    )
    assert detail(refused) == (409, "stale_reference")
    assert (subjects(mailbox, "INBOX"), subjects(mailbox, "Junk")) == before


def test_a_call_cut_at_its_deadline_writes_no_group_after_the_cut(
    mailbox: Mailbox, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_archive(mailbox)
    from mailroom.mail.service import organize as service_organize

    mailbox.seed(mime("slow inbox"))
    mailbox.seed(mime("quick junk"), folder="Junk")
    refs = mailbox.refs() + mailbox.refs("Junk")
    pool: MailPool = application.state.mail_pool
    monkeypatch.setattr(pool, "deadline_seconds", 1.5)
    answered = threading.Event()
    ended = threading.Event()
    real_with_mailbox = MailConnections.with_mailbox
    real_move = service_organize.core.move_messages

    def slow_inbox(
        self: MailConnections, account_id: str, path: str, read_only: bool, fn: Any
    ) -> Any:
        # A server that answers the INBOX group only once the caller has had its 504: nothing in
        # use for the pool to cut, so only the call's own stop keeps the groups from writing.
        if path == "INBOX" and not read_only:
            assert answered.wait(10)
        return real_with_mailbox(self, account_id, path, read_only, fn)

    def move_then_end(*args: Any, **kwargs: Any) -> Any:
        try:
            return real_move(*args, **kwargs)
        finally:
            ended.set()

    monkeypatch.setattr(MailConnections, "with_mailbox", slow_inbox)
    monkeypatch.setattr(service_organize.core, "move_messages", move_then_end)
    response = mailbox.client.post(
        f"{MAIL}/messages/move", json={"refs": refs, "folder": "Archive"}
    )
    answered.set()
    assert detail(response) == (504, "timeout")
    assert ended.wait(10)
    assert subjects(mailbox, "INBOX") == ["slow inbox"]
    assert subjects(mailbox, "Junk") == ["quick junk"]
    assert subjects(mailbox, "Archive") == []


def test_archive_moves_to_the_archive_folder_or_is_unsupported(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_archive(mailbox)
    mailbox.seed(mime("keep"))
    [ref] = mailbox.refs()
    [group] = mailbox.organize("archive", refs=[ref])
    assert (group["ok"], group["count"], len(group["new_refs"])) == (True, 1, 1)
    assert subjects(mailbox, "Archive") == ["keep"]

    # A server with no folder in the archive role.
    real = MailConnections.folders

    def without_archive(self: MailConnections, account_id: str, **kwargs: Any) -> list[Folder]:
        return [
            replace(f, role=None) if f.role == "archive" else f
            for f in real(self, account_id, **kwargs)
        ]

    monkeypatch.setattr(MailConnections, "folders", without_archive)
    [archived] = group["new_refs"]
    refused = mailbox.client.post(f"{MAIL}/messages/archive", json={"refs": [archived]})
    assert detail(refused) == (502, "unsupported")
    assert refused.json()["detail"]["message"].endswith("work has no Archive folder.")
    assert subjects(mailbox, "Archive") == ["keep"]


def test_trash_moves_to_trash_and_never_deletes(
    mailbox: Mailbox, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    mailbox.seed(mime("unwanted"))
    [group] = mailbox.organize("trash", refs=mailbox.refs())
    assert (group["ok"], group["count"], group["junk"]) == (True, 1, None)
    assert subjects(mailbox, "Trash") == ["unwanted"]
    [record] = audited(user.id)
    assert (record.actor, record.action, record.account_id, record.result) == (
        "user",
        "trash",
        "work",
        "ok",
    )
    assert (record.detail["folder"], record.detail["destination"]) == ("INBOX", "Trash")
    assert "subject" not in record.detail
    assert "subject_sha256" in record.detail

    refused = mailbox.client.post(f"{MAIL}/messages/trash", json={"refs": group["new_refs"]})
    assert detail(refused) == (400, "invalid_argument")
    assert refused.json()["detail"]["message"].endswith(
        "They are already in Trash; mailroom never deletes permanently."
    )
    assert subjects(mailbox, "Trash") == ["unwanted"]


def test_a_trash_answers_the_same_when_its_record_cannot_be_written(
    mailbox: Mailbox,
    application: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> None:
        raise RuntimeError

    monkeypatch.setattr(application.state, "session_maker", broken)
    caplog.set_level(logging.WARNING)
    mailbox.seed(mime("unwanted"))
    [group] = mailbox.organize("trash", refs=mailbox.refs())
    assert (group["ok"], group["count"]) == (True, 1)
    assert subjects(mailbox, "Trash") == ["unwanted"]
    assert [r.getMessage() for r in caplog.records].count("audit_write_failed") == 1


def test_removing_an_account_keeps_its_records(
    mailbox: Mailbox, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    mailbox.seed(mime("unwanted"))
    mailbox.organize("trash", refs=mailbox.refs())
    assert mailbox.client.delete("/api/v1/accounts/work").status_code == 204
    assert [(r.action, r.account_id) for r in audited(user.id)] == [("trash", "work")]


def test_flags_change_read_back_and_repeat(mailbox: Mailbox) -> None:
    mailbox.seed(mime("news"))
    [ref] = mailbox.refs()

    def flags() -> set[str]:
        [message] = folder_contents(mailbox.server, mailbox.login, "INBOX")
        return set(message.flags)

    for _ in range(2):  # the same change twice answers the same
        [group] = mailbox.organize("flag", refs=[ref], seen=True)
        assert group == {
            "account": "work",
            "folder": "INBOX",
            "ok": True,
            "count": 1,
            "new_refs": [],
            "junk": None,
            "flags": [{"ref": ref, "seen": True, "flagged": False}],
            "error": None,
        }
        assert flags() == {"\\Seen"}
    [starred] = mailbox.organize("flag", refs=[ref], seen=False, flagged=True)
    assert starred["flags"] == [{"ref": ref, "seen": False, "flagged": True}]
    assert flags() == {"\\Flagged"}
    [unstarred] = mailbox.organize("flag", refs=[ref], flagged=False, seen=None)
    assert unstarred["flags"] == [{"ref": ref, "seen": False, "flagged": False}]
    assert mailbox.get(f"/messages/{ref}")["flags"]["flagged"] is False

    neither = mailbox.client.post(f"{MAIL}/messages/flag", json={"refs": [ref]})
    assert detail(neither) == (400, "invalid_argument")
    assert neither.json()["detail"]["message"].endswith("Give `seen`, `flagged` or both.")


def test_a_batch_is_refused_whole_before_any_write(mailbox: Mailbox) -> None:
    create_archive(mailbox)
    mailbox.seed(mime("mine"))
    [ref] = mailbox.refs()
    message = parse_ref(ref)
    elsewhere = format_ref(MessageRef("home", "INBOX", message.uid_validity, message.uid))
    stale = format_ref(MessageRef("work", "INBOX", message.uid_validity + 1, message.uid))
    client = mailbox.client

    def post(action: str, **body: Any) -> Any:
        return client.post(f"{MAIL}/messages/{action}", json=body)

    for action, extra in (
        ("move", {"folder": "Archive"}),
        ("archive", {}),
        ("trash", {}),
        ("flag", {"seen": True}),
    ):
        foreign = post(action, refs=[ref, elsewhere], **extra)
        assert foreign.json() == {
            "detail": {"code": "not_found", "message": "No such message or part."}
        }
        assert foreign.headers["cache-control"] == "private, no-store"
        assert detail(post(action, refs=[ref, "garbage"], **extra)) == (400, "invalid_argument")
        assert detail(post(action, refs=[stale], **extra)) == (409, "stale_reference")
        for refs in ([], [ref] * 51, ["x" * 4097], [""]):
            refused = post(action, refs=refs, **extra)
            assert refused.status_code == 422
            assert refused.headers["cache-control"] == "private, no-store"
        assert post(action, refs=[ref], unknown=1, **extra).status_code == 422
    assert post("move", refs=[ref]).status_code == 422  # no folder
    # Neither flag given is refused before the refs are looked at: not the foreign ref's 404.
    assert detail(post("flag", refs=[elsewhere])) == (400, "invalid_argument")
    assert [
        (m.subject, m.flags) for m in folder_contents(mailbox.server, mailbox.login, "INBOX")
    ] == [("mine", ())]


def test_a_batch_naming_an_account_that_cannot_log_in_is_refused_before_any_write(
    mailbox: Mailbox,
    application: FastAPI,
    migrated_engine: Any,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every account a batch names is checked before anything connects; the usable account's
    message stays where it was."""
    create_archive(mailbox)
    home = new_account("home", imap=endpoint(mailbox.server))
    added = mailbox.client.post("/api/v1/accounts", json=home)
    assert added.status_code == 201, added.text
    store = user_store(migrated_engine, user.id)
    current = load_accounts(store)
    store.save_settings(current.doc, current.revision, SecretChanges(remove=["home"]))
    mailbox.seed(mime("mine"))
    [ref] = mailbox.refs()
    message = parse_ref(ref)
    locked = format_ref(MessageRef("home", "INBOX", message.uid_validity, message.uid))
    actions: tuple[tuple[str, dict[str, Any]], ...] = (
        ("move", {"folder": "Archive"}),
        ("archive", {}),
        ("trash", {}),
        ("flag", {"seen": True}),
    )

    def post(action: str, **body: Any) -> Any:
        return mailbox.client.post(f"{MAIL}/messages/{action}", json=body)

    for action, extra in actions:
        assert detail(post(action, refs=[ref, locked], **extra)) == (409, "password_missing")
    monkeypatch.setattr(application.state, "secrets_subkey", None)
    for action, extra in actions:
        assert detail(post(action, refs=[ref], **extra)) == (503, "secrets_key_missing")
    [kept] = folder_contents(mailbox.server, mailbox.login, "INBOX")
    assert (kept.subject, set(kept.flags) - {"\\Recent"}) == ("mine", set())
    assert subjects(mailbox, "Archive") == subjects(mailbox, "Trash") == []


def test_a_cross_site_organize_is_refused(mailbox: Mailbox) -> None:
    mailbox.seed(mime("mine"))
    refs = mailbox.refs()
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}):
        response = mailbox.client.post(
            f"{MAIL}/messages/trash", json={"refs": refs}, headers=headers
        )
        assert response.status_code == 403
        assert response.headers["cache-control"] == "private, no-store"
    assert subjects(mailbox, "INBOX") == ["mine"]


def folder_paths(mailbox: Mailbox, **params: Any) -> dict[str, dict[str, Any]]:
    listed = mailbox.get("/accounts/work/folders", **params)["folders"]
    return {folder["path"]: folder for folder in listed}


def server_folders(mailbox: Mailbox) -> set[str]:
    with raw_client(mailbox.server, mailbox.login) as client:
        return {name for _flags, _delimiter, name in client.list_folders()}


def folder_call(mailbox: Mailbox, action: str, **body: Any) -> Any:
    """``POST /accounts/work/folders[/<action>]``: the response."""
    path = f"{MAIL}/accounts/work/folders" + (f"/{action}" if action != "create" else "")
    return mailbox.client.post(path, json=body)


def add_rule_set(
    db: Session,
    user_id: int,
    folder: list[str],
    *specs: dict[str, Any],
    account_id: str = "work",
    enabled: bool = False,
    name: str = "rules",
) -> RuleSet:
    rule_set = RuleSet(
        user_id=user_id,
        account_id=account_id,
        folder=folder,
        name=name,
        trigger="new_mail",
        enabled=enabled,
    )
    db.add(rule_set)
    db.flush()
    for position, spec in enumerate(specs):
        db.add(Filter(rule_set_id=rule_set.id, position=position, name="f", spec=spec))
    db.commit()
    return rule_set


def moving_to(*folder: str) -> dict[str, Any]:
    return {
        "criterion": {"type": "from_contains", "args": {"addr": "shop"}},
        "action": {"seq": [{"type": "star"}, {"type": "move_to", "args": {"folder": [*folder]}}]},
    }


def test_folders_are_created_renamed_and_deleted(
    mailbox: Mailbox, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    created = folder_call(mailbox, "create", parent=None, name="Projects")
    assert (created.status_code, created.json()) == (
        201,
        {"account_id": "work", "path": "Projects"},
    )
    assert created.headers["cache-control"] == "private, no-store"
    inner = folder_call(mailbox, "create", parent="Projects", name="Città 2026")
    assert inner.json() == {"account_id": "work", "path": "Projects/Città 2026"}
    assert {"Projects", "Projects/Città 2026"} <= set(folder_paths(mailbox))

    renamed = folder_call(mailbox, "rename", folder="Projects", parent=None, name="Work")
    assert (renamed.status_code, renamed.json()) == (
        200,
        {
            "account_id": "work",
            "folder": "Projects",
            "path": "Work",
            "rule_sets": [],
            "filters": [],
            "rule_sets_updated": True,
        },
    )
    assert {"Work", "Work/Città 2026"} <= server_folders(mailbox)
    assert "Projects" not in server_folders(mailbox)

    mailbox.seed(mime("one"), folder="Work/Città 2026")
    mailbox.seed(mime("two"), folder="Work/Città 2026")
    deleted = folder_call(mailbox, "delete", folder="Work/Città 2026", messages=2)
    assert (deleted.status_code, deleted.json()) == (
        200,
        {"account_id": "work", "folder": "Work/Città 2026", "messages": 2},
    )
    assert "Work/Città 2026" not in server_folders(mailbox)

    records = audited(user.id)
    assert [(r.actor, r.action, r.account_id, r.result, r.detail) for r in records] == [
        ("user", "folder_create", "work", "ok", {"folder": "Projects"}),
        ("user", "folder_create", "work", "ok", {"folder": "Projects/Città 2026"}),
        (
            "user",
            "folder_rename",
            "work",
            "ok",
            {"folder": "Projects", "destination": "Work", "rule_sets": [], "filters": []},
        ),
        ("user", "folder_delete", "work", "ok", {"folder": "Work/Città 2026", "messages": 2}),
    ]


def test_all_folders_include_the_unselectable_ones(mailbox: Mailbox) -> None:
    create_folders(mailbox.server, mailbox.login, "Box/Leaf")
    assert "Box" not in folder_paths(mailbox)
    every = folder_paths(mailbox, all="true")
    assert every["Box"] == {
        "path": "Box",
        "role": None,
        "unread": 0,
        "total": 0,
        "delimiter": "/",
        "selectable": False,
    }
    assert every["Box/Leaf"]["selectable"] is True
    assert every["INBOX"]["selectable"] is True


def test_folder_writes_are_refused_and_write_nothing(
    mailbox: Mailbox, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    create_folders(mailbox.server, mailbox.login, "Lists", "Kept")
    mailbox.seed(mime("news"), folder="Lists")
    before = server_folders(mailbox)
    for action, body, status, code in (
        ("create", {"parent": None, "name": "Lists"}, 409, "folder_exists"),
        ("create", {"parent": "Nowhere", "name": "Inner"}, 404, "mailbox_missing"),
        ("create", {"parent": None, "name": "a/b"}, 400, "invalid_argument"),
        ("create", {"parent": None, "name": "inbox"}, 400, "invalid_argument"),
        ("rename", {"folder": "INBOX", "parent": None, "name": "Box"}, 400, "invalid_argument"),
        ("rename", {"folder": "Trash", "parent": None, "name": "Bin"}, 400, "invalid_argument"),
        ("rename", {"folder": "Lists", "parent": None, "name": "Kept"}, 409, "folder_exists"),
        ("rename", {"folder": "Lists", "parent": "Lists", "name": "In"}, 400, "invalid_argument"),
        ("rename", {"folder": "Gone", "parent": None, "name": "New"}, 404, "mailbox_missing"),
        ("delete", {"folder": "Trash", "messages": 0}, 400, "invalid_argument"),
        ("delete", {"folder": "Gone", "messages": 0}, 404, "mailbox_missing"),
    ):
        response = folder_call(mailbox, action, **body)
        assert detail(response) == (status, code), (action, body)
    assert server_folders(mailbox) == before

    changed = folder_call(mailbox, "delete", folder="Lists", messages=0)
    assert detail(changed) == (409, "folder_changed")
    assert changed.json()["detail"]["messages"] == 1
    assert changed.json()["detail"]["message"].endswith(
        "It holds 1 message now; nothing was deleted."
    )
    assert subjects(mailbox, "Lists") == ["news"]

    records = audited(user.id)
    assert len(records) == 12
    assert (records[-1].action, records[-1].result, records[-1].detail) == (
        "folder_delete",
        "folder_changed",
        {"folder": "Lists", "messages": 0},
    )


def test_folder_requests_are_checked_before_anything_connects(mailbox: Mailbox) -> None:
    for action, body in (
        ("create", {"name": "No parent given"}),
        ("create", {"parent": None, "name": ""}),
        ("create", {"parent": None, "name": "x" * 256}),
        ("create", {"parent": None, "name": "x", "role": "trash"}),
        ("rename", {"folder": "Lists", "name": "x"}),
        ("rename", {"folder": "", "parent": None, "name": "x"}),
        ("delete", {"folder": "Lists"}),
        ("delete", {"folder": "Lists", "messages": -1}),
        ("delete", {"folder": "Lists", "messages": "1"}),
        ("delete", {"folder": "Lists", "messages": True}),
        ("delete", {"folder": "x" * 1001, "messages": 0}),
    ):
        response = folder_call(mailbox, action, **body)
        assert response.status_code == 422, (action, body)
    unknown = mailbox.client.post(
        f"{MAIL}/accounts/home/folders/delete", json={"folder": "Lists", "messages": 0}
    )
    assert detail(unknown) == (404, "not_found")
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}):
        response = mailbox.client.post(
            f"{MAIL}/accounts/work/folders",
            json={"parent": None, "name": "Evil"},
            headers=headers,
        )
        assert response.status_code == 403
    assert "Evil" not in server_folders(mailbox)


def test_a_rename_carries_the_rule_sets_with_it(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
    db_session: Session,
) -> None:
    create_folders(mailbox.server, mailbox.login, "Old", "Old/Sub", "Older")
    enabled = add_rule_set(db_session, user.id, ["Old"], enabled=True)
    below = add_rule_set(db_session, user.id, ["Old", "Sub"])
    mover = add_rule_set(
        db_session, user.id, ["INBOX"], moving_to("Older"), moving_to("Old", "Sub")
    )
    other_account = add_rule_set(db_session, user.id, ["Old"], account_id="home")
    bob = create_user(db_session, "bob", PASSWORD)
    bobs = add_rule_set(db_session, bob.id, ["Old"], moving_to("Old"))
    [stays, moves] = db_session.query(Filter).filter_by(rule_set_id=mover.id).order_by(Filter.id)

    answer = folder_call(mailbox, "rename", folder="Old", parent="Older", name="New")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "account_id": "work",
        "folder": "Old",
        "path": "Older/New",
        "rule_sets": [enabled.id, below.id, mover.id],
        "filters": [moves.id],
        "rule_sets_updated": True,
    }
    assert {"Older/New", "Older/New/Sub"} <= server_folders(mailbox)
    db_session.expire_all()
    assert (enabled.folder, enabled.enabled) == (["Older", "New"], True)
    assert below.folder == ["Older", "New", "Sub"]
    assert moves.spec == moving_to("Older", "New", "Sub")
    assert stays.spec == moving_to("Older")
    assert other_account.folder == ["Old"]
    assert bobs.folder == ["Old"]
    assert db_session.query(Filter).filter_by(rule_set_id=bobs.id).one().spec == moving_to("Old")

    [record] = audited(user.id)
    assert (record.action, record.result, record.detail) == (
        "folder_rename",
        "ok",
        {
            "folder": "Old",
            "destination": "Older/New",
            "rule_sets": [enabled.id, below.id, mover.id],
            "filters": [moves.id],
        },
    )


def test_a_rename_stands_when_its_rule_sets_cannot_follow(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mailroom.mail.service import folder_writes as service_folder_writes

    def broken(*_args: object) -> None:
        raise RuntimeError("SELECT … 'Old' …")

    create_folders(mailbox.server, mailbox.login, "Old")
    rule_set = add_rule_set(db_session, user.id, ["Old"])
    monkeypatch.setattr(service_folder_writes, "rewrite_folder", broken)
    with caplog.at_level(logging.INFO, logger="mailroom.mail.service"):
        answer = folder_call(mailbox, "rename", folder="Old", parent=None, name="New")
    assert answer.status_code == 200, answer.text
    assert answer.json()["rule_sets_updated"] is False
    assert (answer.json()["rule_sets"], answer.json()["filters"]) == ([], [])
    assert "New" in server_folders(mailbox)
    assert "Old" not in server_folders(mailbox)
    db_session.expire_all()
    assert rule_set.folder == ["Old"]
    failed = next(r for r in caplog.records if r.msg == "folder_rule_sets_failed")
    assert failed.__dict__["error"] == "RuntimeError"
    assert "Old" not in str(failed.__dict__)
    logged = next(r for r in caplog.records if r.msg == "mail_folder_renamed")
    assert (logged.__dict__["outcome"], logged.__dict__["rule_sets_updated"]) == ("ok", False)
    [record] = audited(user.id)
    assert record.detail == {"folder": "Old", "destination": "New", "rule_sets_error": True}


def test_a_folder_a_rule_set_names_is_not_deleted(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
    db_session: Session,
) -> None:
    create_folders(mailbox.server, mailbox.login, "Lists", "Other")
    mailbox.seed(mime("news"), folder="Lists")
    mover = add_rule_set(db_session, user.id, ["INBOX"], moving_to("Lists"), name="Newsletters")
    add_rule_set(db_session, user.id, ["Lists"], account_id="home", name="Elsewhere")

    refused = folder_call(mailbox, "delete", folder="Lists", messages=1)
    assert detail(refused) == (409, "folder_in_use")
    assert refused.json()["detail"]["rule_sets"] == [{"id": mover.id, "name": "Newsletters"}]
    assert '"Newsletters"' in refused.json()["detail"]["message"]
    assert subjects(mailbox, "Lists") == ["news"]
    [record] = audited(user.id)
    assert (record.action, record.result, record.detail) == (
        "folder_delete",
        "folder_in_use",
        {"folder": "Lists", "messages": 1},
    )

    named = [add_rule_set(db_session, user.id, ["Other"], name=f"r{n}") for n in range(11)]
    many = folder_call(mailbox, "delete", folder="Other", messages=0)
    assert detail(many) == (409, "folder_in_use")
    assert [r["id"] for r in many.json()["detail"]["rule_sets"]] == [r.id for r in named[:10]]
    assert '"r9" and 1 more.' in many.json()["detail"]["message"]
    assert "Other" in server_folders(mailbox)


def test_folder_logs_carry_counts_never_names(
    mailbox: Mailbox, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="mailroom.mail.service"):
        folder_call(mailbox, "create", parent=None, name="Secret Plans")
        folder_call(mailbox, "rename", folder="Secret Plans", parent=None, name="Hidden Things")
        mailbox.seed(mime("plan"), folder="Hidden Things")
        folder_call(mailbox, "delete", folder="Hidden Things", messages=0)
        folder_call(mailbox, "delete", folder="Hidden Things", messages=1)
    records = {
        (r.msg, r.__dict__["outcome"]): r
        for r in caplog.records
        if str(r.msg).startswith("mail_folder_")
    }
    assert set(records) == {
        ("mail_folder_created", "ok"),
        ("mail_folder_renamed", "ok"),
        ("mail_folder_deleted", "folder_changed"),
        ("mail_folder_deleted", "ok"),
    }
    renamed = records["mail_folder_renamed", "ok"].__dict__
    assert (renamed["via"], renamed["account_id"]) == ("web", "work")
    assert (renamed["rule_sets"], renamed["filters"], renamed["rule_sets_updated"]) == (0, 0, True)
    assert records["mail_folder_deleted", "ok"].__dict__["messages"] == 1
    for record in records.values():
        assert isinstance(record.__dict__["duration_ms"], int)
        for name in ("Secret", "Hidden", "Plans", "Things"):
            assert name not in str(record.__dict__)
