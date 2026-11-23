"""The mail client's REST routes (``docs/requirements/mail-client.md``) against the Dovecot
``IMAP_TEST_HOST`` names (``mise run test-backend-mail`` starts one; ``tests/support/mail.py``
says which TLS it trusts); skipped without it. Each test logs in as a fresh mailbox user, seeded
with mailroom-core's testing helpers."""

from __future__ import annotations

import base64
import ipaddress
import logging
import os
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy import select

from mailroom.accounts.store import load_accounts, user_store
from mailroom.api.routes.mail.read import (
    ORIGINAL_TARGET,
    PRINT_CSP,
    download_name,
    html_document,
    served_type,
)
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session
from mailroom.auth.users import create_user
from mailroom.mail.pool import DeadlineExceeded, MailPool
from mailroom.mail.service import read as read_service
from mailroom.mail.service.read import HTML_VIEW_SECONDS, HtmlKey, HtmlViews
from mailroom.models import Filter, MutedThread, RuleSet
from mailroom.net.safe_fetch import safe_fetch
from mailroom_core import mail as mail_core
from mailroom_core.crypto import secrets_subkey
from mailroom_core.mail import (
    MESSAGES,
    TO_SORT,
    Capabilities,
    MailboxContext,
    MailConnections,
    MailError,
    MessageRef,
    Page,
    SearchQuery,
    ThreadKey,
    format_ref,
    parse_ref,
)
from mailroom_core.render import SafeHtml
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
from tests.support.http import streamed
from tests.support.mail import mail_servers_ssl_context
from tests.test_api_accounts import new_account

if TYPE_CHECKING:
    from collections.abc import Callable

    from fastapi import FastAPI
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import AuditRecord, User
    from mailroom.net.safe_fetch import Fetched, IpAddress
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
DARK = ":root:not(:has(#mailroom-original:target))"
"""What every dark rule waits for: the dark document's URL fragment is not ``#mailroom-original``."""
TARGET = '<span id="mailroom-original" hidden></span>'

pytestmark = pytest.mark.usefixtures("numbered_accounts")


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
        page = self.get("/accounts/1/messages", folder=folder)
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
    numbered_accounts: None,
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
            "/accounts/1/folders",
            "/accounts/1/messages",
            "/messages/x",
            "/messages/x/html",
            "/messages/x/parts/2",
        )
    ]
    refused += [
        api_client.post(f"{MAIL}{path}", json={"refs": ["x"], "folder": "Archive"})
        for path in (
            "/search",
            "/messages/move",
            "/messages/archive",
            "/messages/junk",
            "/messages/trash",
        )
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
    listed = mailbox.get("/accounts/1/folders")
    assert listed["account_id"] == 1
    folders = {folder["path"]: folder for folder in listed["folders"]}
    inbox = folders["INBOX"]
    assert isinstance(inbox["uid_validity"], int)
    assert inbox == {
        "path": "INBOX",
        "role": "inbox",
        "unread": 1,
        "total": 2,
        "delimiter": "/",
        "selectable": True,
        "uid_validity": inbox["uid_validity"],
        "uid_next": 3,
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
    first = mailbox.get("/accounts/1/messages", limit=2)
    assert [m["subject"] for m in first["messages"]] == ["Message 2", "Message 1"]
    assert first["failures"] == []
    item = first["messages"][0]
    assert item["from"] == {"name": "Ada Lovelace", "address": "ada@example.org"}
    assert item["date"] == "2026-10-03T09:00:00+02:00"
    assert item["flags"] == {
        "seen": False,
        "flagged": False,
        "answered": False,
        "forwarded": False,
        "draft": False,
        "to_sort": False,
    }
    assert item["preview"] == "Body number 2."
    assert (item["account_id"], item["folder"], item["has_attachments"]) == (
        1,
        "INBOX",
        False,
    )
    assert item["size"] > 0

    rest = mailbox.get("/accounts/1/messages", limit=2, cursor=first["next_cursor"])
    assert [m["subject"] for m in rest["messages"]] == ["Message 0"]
    assert rest["messages"][0]["flags"]["flagged"] is True
    assert rest["next_cursor"] is None


def test_list_refusals(mailbox: Mailbox) -> None:
    client = mailbox.client
    assert detail(client.get(f"{MAIL}/accounts/1/messages", params={"folder": "Nowhere"})) == (
        404,
        "mailbox_missing",
    )
    bad_cursor = client.get(f"{MAIL}/accounts/1/messages", params={"cursor": "nope"})
    assert detail(bad_cursor) == (400, "invalid_argument")
    too_many = client.get(f"{MAIL}/accounts/1/messages", params={"limit": 51})
    assert too_many.status_code == 422
    unknown = client.get(f"{MAIL}/accounts/2/messages")
    assert unknown.json() == {"detail": {"code": "not_found", "message": "No such account."}}
    assert detail(client.get(f"{MAIL}/accounts/2/folders")) == (404, "not_found")


def test_a_folder_lists_and_searches_with_its_subfolders(mailbox: Mailbox) -> None:
    create_folders(mailbox.server, mailbox.login, "Projects", "Projects/Old")
    for subject, folder, day in (
        ("Kickoff", "Projects", 1),
        ("Retro", "Projects/Old", 2),
        ("Elsewhere", "INBOX", 3),
    ):
        arrived = datetime(2026, 10, day, 9, tzinfo=UTC)
        seed(mailbox.server, mailbox.login, folder, mime(subject, text="plan"), (), arrived)

    alone = mailbox.get("/accounts/1/messages", folder="Projects")
    assert [m["subject"] for m in alone["messages"]] == ["Kickoff"]
    merged = mailbox.get("/accounts/1/messages", folder="Projects", subfolders="true")
    assert [(m["subject"], m["folder"]) for m in merged["messages"]] == [
        ("Retro", "Projects/Old"),
        ("Kickoff", "Projects"),
    ]
    assert (merged["next_cursor"], merged["failures"]) == (None, [])

    found = mailbox.search(account=1, folder="Projects", text="plan", subfolders=True)
    assert [m["subject"] for m in found["messages"]] == ["Retro", "Kickoff"]
    refused = mailbox.client.post(
        f"{MAIL}/search", json={"account": 1, "folder": "*", "subfolders": True}
    )
    assert detail(refused) == (400, "invalid_argument")


def test_search_reads_the_fields_and_leaves_junk_out(mailbox: Mailbox) -> None:
    mailbox.seed(mime("Invoice March", sender="Shop <shop@example.org>"))
    mailbox.seed(mime("Invoice April", sender="Shop <shop@example.org>"), folder="Junk")
    mailbox.seed(mime("Holiday plans", sender="Ada <ada@example.org>"), flags=("\\Seen", TO_SORT))

    inbox = mailbox.search(account=1, subject="invoice")
    assert [m["subject"] for m in inbox["messages"]] == ["Invoice March"]
    everywhere = mailbox.search(folder="*", **{"from": "shop"})
    assert [m["subject"] for m in everywhere["messages"]] == ["Invoice March"]
    junk = mailbox.search(folder="Junk", subject="invoice")
    assert [m["subject"] for m in junk["messages"]] == ["Invoice April"]
    unread = mailbox.search(unread=True)
    assert [m["subject"] for m in unread["messages"]] == ["Invoice March"]
    to_sort = mailbox.search(to_sort=True)
    assert [m["subject"] for m in to_sort["messages"]] == ["Holiday plans"]
    # An empty field is no criterion, nor is null.
    assert len(mailbox.search(text="", subject=None)["messages"]) == 2

    def post(**body: Any) -> Any:
        return mailbox.client.post(f"{MAIL}/search", json=body)

    assert detail(post(account=2)) == (404, "not_found")
    assert post(since="2026-02-30").status_code == 422
    assert post(query="shop").status_code == 422  # an unknown field is refused
    assert post(sender="shop").status_code == 422  # the field is "from"
    for field in ("account", "folder", "limit"):
        assert post(**{field: None}).status_code == 422
    assert detail(post(account=1, gmail_query="x")) == (502, "unsupported")
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
    assert page["failures"] == [{"account_id": 2, "folder": "INBOX", "code": "unreachable"}]
    # Over every folder it failed before one was chosen.
    page = mailbox.search(folder="*")
    assert [m["subject"] for m in page["messages"]] == ["Kept"]
    assert page["failures"] == [{"account_id": 2, "folder": None, "code": "unreachable"}]
    # Searched alone, a single folder's failure is the answer, as for a list.
    closed_only = mailbox.client.post(f"{MAIL}/search", json={"account": 2})
    assert detail(closed_only) == (502, "unreachable")


def test_an_all_accounts_search_with_one_usable_account_still_answers_a_page(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom.mail.service import read as service_read

    closed = new_account("closed", login="closed@example.org")
    assert mailbox.client.post("/api/v1/accounts", json=closed).status_code == 201
    monkeypatch.setattr(
        service_read, "_missing_password", lambda _ctx, _user, account_id: account_id == 1
    )
    for folder in ("INBOX", "*"):
        page = mailbox.search(folder=folder)
        assert page["messages"] == []
        assert page["failures"] == [
            {"account_id": 1, "folder": None, "code": "password_missing"},
            {
                "account_id": 2,
                "folder": None if folder == "*" else "INBOX",
                "code": "unreachable",
            },
        ]


def test_an_account_without_a_password_stays_a_failure_of_every_later_page(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An account skipped as ``password_missing`` is carried by the cursor: later pages report
    it without checking its password again, even once it is back (its mail would have no place
    in the cursor and be skipped in silence), until paging ends."""
    from mailroom.mail.service import read as service_read

    for subject in ("One", "Two", "Three"):
        mailbox.seed(mime(subject))
    other = new_account("other", login="other@example.org")
    assert mailbox.client.post("/api/v1/accounts", json=other).status_code == 201
    checked: list[int] = []

    def missing(_ctx: object, _user: int, account_id: int) -> bool:
        checked.append(account_id)
        return account_id == 2 and checked.count(2) == 1

    monkeypatch.setattr(service_read, "_missing_password", missing)
    subjects: list[str] = []
    failures: list[Any] = []
    cursor = None
    for _ in range(5):
        page = mailbox.search(limit=2, **({} if cursor is None else {"cursor": cursor}))
        subjects += [m["subject"] for m in page["messages"]]
        failures.append(page["failures"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert (subjects, cursor) == (["Three", "Two", "One"], None)
    assert failures == [[{"account_id": 2, "folder": None, "code": "password_missing"}]] * 2
    assert checked.count(2) == 1


def test_a_slow_account_is_a_timeout_failure_of_an_all_accounts_search(
    mailbox: Mailbox, silent_server: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom.mail.service import read as service_read

    # Long enough for the healthy account under a loaded test run, below the silent one's 10 s
    # connect timeout: the search waits the full 8 s for it, and answering before 9.5 s proves
    # it was cut rather than waited out (its `timeout` code says so too).
    monkeypatch.setattr(service_read, "SOURCES_SECONDS", 8.0)
    mailbox.seed(mime("Kept"))
    imap = {"host": "127.0.0.1", "port": silent_server, "tls": "implicit"}
    silent = new_account("silent", login="silent@example.org", imap=imap)
    assert mailbox.client.post("/api/v1/accounts", json=silent).status_code == 201
    started = time.monotonic()
    page = mailbox.search(folder="*")
    assert time.monotonic() - started < 9.5
    assert [m["subject"] for m in page["messages"]] == ["Kept"]
    assert page["failures"] == [{"account_id": 2, "folder": None, "code": "timeout"}]


def test_a_message_says_who_it_was_sent_to_and_what_the_accounts_server_found(
    mailbox: Mailbox,
) -> None:
    mailbox.seed(
        "Authentication-Results: mx.evil.example; dmarc=pass header.from=bank.example\r\n"
        "Authentication-Results: mx.google.com; spf=pass smtp.mailfrom=bank.example;\r\n"
        " dkim=pass header.d=bank.example header.b=AAAA; dmarc=pass header.from=bank.example\r\n"
        "From: Bank Support <alerts@bank.example>\r\n"
        "To: list@lists.example\r\nCc: Work@Example.org\r\nSubject: Statement\r\n"
        "\r\nhello\r\n"
    )
    mailbox.seed(
        "Authentication-Results: mx.evil.example; dmarc=pass header.from=bank.example\r\n"
        'From: "support@bank.example" <x@evil.example>\r\nTo: work@example.org\r\n'
        "Subject: Forged\r\n\r\nhello\r\n"
    )
    account = new_account(login=mailbox.login, imap=endpoint(mailbox.server))
    account = {key: value for key, value in account.items() if key != "password"}
    changed = mailbox.client.put(
        "/api/v1/accounts/1", json=account | {"authserv_id": "mx.google.com"}
    )
    assert changed.status_code == 200, changed.text
    real, forged = (mailbox.get(f"/messages/{ref}") for ref in mailbox.refs()[::-1])
    assert (real["sent_to"], real["sender_mismatch"]) == ("work@example.org", False)
    assert real["authentication"] == {
        "verified": True,
        "spf": "pass",
        "spf_domain": "bank.example",
        "dkim": "pass",
        "dkim_domain": "bank.example",
        "dmarc": "pass",
        "dmarc_pass": True,
        "from_domain": "bank.example",
    }
    # Only the forged field, written above where the account's server would: not believed.
    assert (forged["sent_to"], forged["sender_mismatch"]) == ("work@example.org", True)
    assert forged["authentication"]["verified"] is False


def test_remote_content_is_remembered_only_for_a_verified_sender(
    mailbox: Mailbox, caplog: pytest.LogCaptureFixture
) -> None:
    trusted = (
        "Authentication-Results: mx.google.com; dkim=pass header.d=shop.example;\r\n"
        " dmarc=pass header.from=shop.example\r\n"
    )
    mailbox.seed(f"{trusted}From: Shop <News@Shop.example>\r\nSubject: Sale\r\n\r\nhi\r\n")
    mailbox.seed(
        "Authentication-Results: mx.evil.example; dmarc=pass header.from=shop.example\r\n"
        "From: news@shop.example\r\nSubject: Forged\r\n\r\nhi\r\n"
    )
    mailbox.seed(
        f"{trusted}From: news@shop.example, other@shop.example\r\nSubject: Two\r\n\r\nhi\r\n"
    )
    account = new_account(login=mailbox.login, imap=endpoint(mailbox.server))
    account = {key: value for key, value in account.items() if key != "password"}
    changed = mailbox.client.put(
        "/api/v1/accounts/1", json=account | {"authserv_id": "mx.google.com"}
    )
    assert changed.status_code == 200, changed.text
    two, forged, real = mailbox.refs()
    senders = f"{MAIL}/remote-senders"

    def trusted_view(ref: str) -> bool:
        return cast("bool", mailbox.get(f"/messages/{ref}")["remote_trusted"])

    assert not trusted_view(real)
    # The server reads the message itself: a From nobody verified is not remembered.
    for ref in (forged, two):
        refused = mailbox.client.post(senders, json={"ref": ref})
        assert detail(refused) == (400, "sender_not_verified")
    assert mailbox.client.get(senders).json() == {"senders": []}

    with caplog.at_level(logging.INFO, logger="mailroom"):
        added = mailbox.client.post(senders, json={"ref": real})
        again = mailbox.client.post(senders, json={"ref": real})
    assert (added.status_code, added.json()["address"]) == (201, "news@shop.example")
    assert again.json() == added.json()
    assert "shop.example" not in caplog.text
    assert [s["address"] for s in mailbox.client.get(senders).json()["senders"]] == [
        "news@shop.example"
    ]
    # Remembered, it counts only where DMARC passed for the message's one From.
    assert (trusted_view(real), trusted_view(forged), trusted_view(two)) == (True, False, False)

    removed = mailbox.client.post(f"{senders}/remove", json={"address": "NEWS@shop.example"})
    assert removed.status_code == 204
    assert (
        mailbox.client.post(f"{senders}/remove", json={"address": "news@shop.example"}).status_code
        == 404
    )
    assert not trusted_view(real)
    cross_site = mailbox.client.post(
        senders, json={"ref": real}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert cross_site.status_code == 403


def test_html_mail_opens_as_html_unless_its_sender_is_read_as_text(
    mailbox: Mailbox, caplog: pytest.LogCaptureFixture
) -> None:
    mailbox.seed(mime("Sale", sender="Shop <News@Shop.example>", html="<p>Sale</p>"))
    mailbox.seed(mime("Later", sender="news@shop.example", html="<p>Later</p>"))
    mailbox.seed(mime("Plain", sender="news@shop.example"))
    plain, later, sale = mailbox.refs()
    views = f"{MAIL}/sender-views"

    def preferred(ref: str) -> str:
        return cast("str", mailbox.get(f"/messages/{ref}")["preferred_view"])

    assert (preferred(sale), preferred(later), preferred(plain)) == ("html", "html", "text")
    # The server reads the From itself: any From, no verification asked.
    with caplog.at_level(logging.INFO, logger="mailroom"):
        chosen = mailbox.client.post(views, json={"ref": sale, "view": "text"})
        again = mailbox.client.post(views, json={"ref": later, "view": "text"})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json() == {"address": "news@shop.example", "view": "text"}
    assert again.json() == chosen.json()
    assert "shop.example" not in caplog.text
    listed = mailbox.client.get(views).json()["senders"]
    assert [each["address"] for each in listed] == ["news@shop.example"]
    assert (preferred(sale), preferred(later)) == ("text", "text")

    back = mailbox.client.post(views, json={"ref": later, "view": "html"})
    assert back.json() == {"address": "news@shop.example", "view": "html"}
    assert mailbox.client.get(views).json() == {"senders": []}
    assert preferred(sale) == "html"

    assert mailbox.client.post(views, json={"ref": sale, "view": "text"}).status_code == 200
    removed = mailbox.client.post(f"{views}/remove", json={"address": "NEWS@shop.example"})
    assert removed.status_code == 204
    gone = mailbox.client.post(f"{views}/remove", json={"address": "news@shop.example"})
    assert gone.status_code == 404
    assert preferred(sale) == "html"
    bad = mailbox.client.post(views, json={"ref": sale, "view": "pdf"})
    assert bad.status_code == 422
    cross_site = mailbox.client.post(
        views, json={"ref": sale, "view": "text"}, headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert cross_site.status_code == 403


def test_a_sender_view_is_refused_for_a_message_without_a_from(mailbox: Mailbox) -> None:
    mailbox.seed(mime("Nobody", sender="undisclosed-recipients:;", html="<p>Hi</p>"))
    (ref,) = mailbox.refs()
    refused = mailbox.client.post(f"{MAIL}/sender-views", json={"ref": ref, "view": "text"})
    assert detail(refused) == (400, "no_sender"), refused.text
    assert mailbox.client.get(f"{MAIL}/sender-views").json() == {"senders": []}


def test_one_click_unsubscribe_posts_only_where_the_accounts_server_vouches(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mailroom.mail import unsubscribe

    passed = (
        "Authentication-Results: mx.google.com; dkim=pass header.d=news.example"
        " header.b=AbCd1234\r\n"
    )
    signed = (
        "DKIM-Signature: v=1; d=news.example; s=s1;\r\n"
        " h=from:list-unsubscribe:list-unsubscribe-post; bh=x=; b=AbCd1234efgh\r\n"
    )
    listed = (
        "List-Id: <weekly.news.example>\r\n"
        "List-Unsubscribe: <https://news.example/u?t=secret42>\r\n"
        "List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
    )
    tail = "From: news@news.example\r\nSubject: Weekly\r\n\r\nhi\r\n"
    mailbox.seed(f"{passed}{signed}{listed}{tail}")
    mailbox.seed(f"{passed}{signed.replace(':list-unsubscribe-post', '')}{listed}{tail}")
    mailbox.seed(f"{passed}{signed}{listed}List-Unsubscribe: <https://evil.example/u>\r\n{tail}")
    mailbox.seed(
        f"{passed}{signed}{listed.replace('https://news.example/u?t=secret42', 'mailto:leave@news.example')}{tail}"
    )
    mailbox.seed(mime("No list"))
    good, unsigned, repeated, mailto, plain = mailbox.refs()[::-1]
    asked: list[httpx.Request] = []
    answer: list[Callable[[httpx.Request], httpx.Response]] = [lambda _: httpx.Response(200)]
    resolved = ["93.184.215.14"]

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return answer[0](request)

    async def public(_host: str) -> list[IpAddress]:
        return [ipaddress.ip_address(resolved[0])]

    async def fetch(url: str, **kwargs: Any) -> Fetched:
        return await safe_fetch(url, resolver=public, transport=streamed(handle), **kwargs)

    monkeypatch.setattr(unsubscribe, "safe_fetch", fetch)

    def post(ref: str, **kwargs: Any) -> Any:
        return mailbox.client.post(f"{MAIL}/messages/{ref}/unsubscribe", **kwargs)

    def offered(ref: str) -> bool:
        return cast("bool", mailbox.get(f"/messages/{ref}")["one_click_unsubscribe"])

    # Without the account's authentication server nothing is believed.
    assert not offered(good)
    assert detail(post(good)) == (400, "unsubscribe_unverified")
    account = new_account(login=mailbox.login, imap=endpoint(mailbox.server))
    account = {key: value for key, value in account.items() if key != "password"}
    changed = mailbox.client.put(
        "/api/v1/accounts/1", json=account | {"authserv_id": "mx.google.com"}
    )
    assert changed.status_code == 200, changed.text
    assert detail(post(unsigned)) == (400, "unsubscribe_not_signed")
    assert detail(post(repeated)) == (400, "unsubscribe_repeated")
    assert detail(post(mailto)) == (400, "unsubscribe_not_https")
    assert detail(post(plain)) == (400, "unsubscribe_unavailable")
    assert asked == []
    assert offered(good)
    assert not any(map(offered, (unsigned, repeated, mailto, plain)))

    with caplog.at_level(logging.INFO, logger="mailroom"):
        done = post(good)
    assert done.status_code == 200, done.text
    assert done.json() == {"outcome": "ok", "status": 200, "list_id": "weekly.news.example"}
    [request] = asked
    assert (request.method, str(request.url.path), request.url.query) == (
        "POST",
        "/u",
        b"t=secret42",
    )
    assert request.headers["Host"] == "news.example"
    assert request.content == b"List-Unsubscribe=One-Click"
    assert {"cookie", "referer", "authorization"}.isdisjoint(request.headers.keys())
    assert "secret42" not in caplog.text
    assert "news.example" not in caplog.text

    answer[0] = lambda _: httpx.Response(500)
    assert post(good).json() == {
        "outcome": "rejected",
        "status": 500,
        "list_id": "weekly.news.example",
    }
    cross_site = post(good, headers={"Sec-Fetch-Site": "cross-site"})
    assert cross_site.status_code == 403
    assert len(asked) == 2

    records = [r for r in audited(user.id) if r.action == "unsubscribe"]
    assert [r.result for r in records] == [
        "unsubscribe_unverified",
        "unsubscribe_not_signed",
        "unsubscribe_repeated",
        "unsubscribe_not_https",
        "unsubscribe_unavailable",
        "ok",
        "rejected",
    ]
    assert {r.actor for r in records} == {"user"}
    last = records[-1].detail
    assert (last["list_id"], last["host"], last["status"]) == (
        "weekly.news.example",
        "news.example",
        500,
    )
    assert "secret42" not in str([r.detail for r in records])


def test_one_click_unsubscribe_says_how_the_list_answered_or_why_not(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mailroom.mail import unsubscribe

    mailbox.seed(
        "Authentication-Results: mx.google.com; dkim=pass header.d=news.example"
        " header.b=AbCd1234\r\n"
        "DKIM-Signature: v=1; d=news.example; s=s1;\r\n"
        " h=from:list-unsubscribe:list-unsubscribe-post; bh=x=; b=AbCd1234efgh\r\n"
        "List-Unsubscribe: <https://news.example/u>\r\n"
        "List-Unsubscribe-Post: List-Unsubscribe=One-Click\r\n"
        "From: news@news.example\r\nSubject: Weekly\r\n\r\nhi\r\n"
    )
    [ref] = mailbox.refs()
    account = new_account(login=mailbox.login, imap=endpoint(mailbox.server))
    account = {key: value for key, value in account.items() if key != "password"}
    changed = mailbox.client.put(
        "/api/v1/accounts/1", json=account | {"authserv_id": "mx.google.com"}
    )
    assert changed.status_code == 200, changed.text
    asked: list[httpx.Request] = []
    answer: list[Callable[[httpx.Request], httpx.Response]] = []
    resolved = ["93.184.215.14"]

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return answer[0](request)

    async def public(_host: str) -> list[IpAddress]:
        return [ipaddress.ip_address(resolved[0])]

    async def fetch(url: str, **kwargs: Any) -> Fetched:
        return await safe_fetch(url, resolver=public, transport=streamed(handle), **kwargs)

    monkeypatch.setattr(unsubscribe, "safe_fetch", fetch)

    def outcome() -> tuple[object, object]:
        body = mailbox.client.post(f"{MAIL}/messages/{ref}/unsubscribe").json()
        return body["outcome"], body["status"]

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    # The list answered: a redirect is not followed, a long page not read, but both are answers.
    answer[:] = [lambda _: httpx.Response(303, headers={"Location": "https://news.example/ok"})]
    assert outcome() == ("rejected", 303)
    big = b"x" * (unsubscribe.MAX_ANSWER_BYTES + 1)
    answer[:] = [lambda _: httpx.Response(200, content=big)]
    assert outcome() == ("ok", 200)
    assert len(asked) == 2
    # A host that resolves to a private address is never asked.
    resolved[0] = "10.0.0.1"
    assert outcome() == ("refused", None)
    assert len(asked) == 2
    resolved[0] = "93.184.215.14"
    answer[:] = [unreachable]
    assert outcome() == ("failed", None)

    records = [r for r in audited(user.id) if r.action == "unsubscribe"]
    assert [(r.result, r.detail["status"]) for r in records] == [
        ("rejected", 303),
        ("ok", 200),
        ("refused", None),
        ("failed", None),
    ]


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
    assert (view["has_html"], view["remote_images"], view["calendar"]) == (False, 0, None)
    assert view["loadable_remote_images"] == 0
    assert view["headers"] == {
        "mailing_list": None,
        "authentication": [],
        "dkim_signatures": [],
        "from_fields": 1,
        "delivered_to": [],
        "truncated": False,
    }
    assert (view["sender_mismatch"], view["sent_to"]) == (False, None)
    # The account names no authentication server: nothing is believed.
    assert view["authentication"] == {
        "verified": False,
        "spf": None,
        "spf_domain": None,
        "dkim": None,
        "dkim_domain": None,
        "dmarc": None,
        "dmarc_pass": False,
        "from_domain": None,
    }
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
    assert (record.__dict__["via"], record.__dict__["account_id"]) == ("web", 1)
    assert "Notes" not in str(record.__dict__)


def test_refs_of_other_accounts_and_stale_refs_are_refused(
    mailbox: Mailbox, db_session: Session, api_client: TestClient
) -> None:
    mailbox.seed(mime("Mine", text="secret"))
    [ref] = mailbox.refs()
    message = parse_ref(ref)
    client = mailbox.client

    stale = format_ref(MessageRef(1, "INBOX", message.uid_validity + 1, message.uid))
    for path in ("", "/html", "/parts/2", "/filter-draft", "/thread"):
        assert detail(client.get(f"{MAIL}/messages/{stale}{path}")) == (409, "stale_reference")
    assert detail(client.post(f"{MAIL}/messages/{stale}/mute")) == (409, "stale_reference")

    elsewhere = format_ref(MessageRef(2, "INBOX", message.uid_validity, message.uid))
    for path in ("", "/html", "/parts/2", "/filter-draft", "/thread"):
        response = client.get(f"{MAIL}/messages/{elsewhere}{path}")
        assert response.json() == {
            "detail": {"code": "not_found", "message": "No such message or part."}
        }
    assert detail(client.get(f"{MAIL}/messages/garbage")) == (400, "invalid_argument")

    # Another user has no account 1: the ref is not theirs to read.
    bob = create_user(db_session, "bob", PASSWORD)
    api_client.cookies.set(SESSION_COOKIE_NAME, create_session(db_session, bob))
    assert detail(api_client.get(f"{MAIL}/messages/{ref}")) == (404, "not_found")
    assert detail(api_client.post(f"{MAIL}/messages/{ref}/mute")) == (404, "not_found")
    assert detail(api_client.get(f"{MAIL}/accounts/1/folders")) == (404, "not_found")
    assert detail(api_client.get(f"{MAIL}/messages/{ref}/thread")) == (404, "not_found")
    assert api_client.post(f"{MAIL}/search", json={}).json() == {
        "messages": [],
        "next_cursor": None,
        "failures": [],
        "conversations": None,
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
    assert (view["has_html"], view["remote_images"], view["loadable_remote_images"]) == (True, 2, 1)
    # The logo the HTML shows in place is no attachment.
    assert view["attachments"] == []
    assert mailbox.get("/accounts/1/messages")["messages"][0]["has_attachments"] is False

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

    assert ":root{color-scheme:light}" in page
    dark = mailbox.client.get(f"{MAIL}/messages/{ref}/html", params={"dark": "1"})
    assert dark.headers["content-security-policy"] == NO_REMOTE_CSP
    assert "{color-scheme:dark}" in dark.text
    assert dark.text.split("<body>", 1)[1] == TARGET + body


def test_dark_html_inverts_a_message_that_sets_its_own_colours(mailbox: Mailbox) -> None:
    mailbox.seed(
        related_html('<table bgcolor="#fff"><tr><td><img src="cid:logo@x">Hi</td></tr></table>')
    )
    [ref] = mailbox.refs()
    light = mailbox.client.get(f"{MAIL}/messages/{ref}/html").text
    dark = mailbox.client.get(f"{MAIL}/messages/{ref}/html", params={"dark": "1"})
    assert dark.headers["content-security-policy"] == NO_REMOTE_CSP
    assert "filter:invert(1) hue-rotate(180deg)" in dark.text
    assert "filter" not in light
    assert dark.text.split("<body>", 1)[1] == TARGET + light.split("<body>", 1)[1]


def test_the_dark_document_is_css_alone() -> None:
    plain = html_document("<p>hi</p>", dark=True)
    assert f"{DARK}{{color-scheme:dark}}" in plain
    assert f"{DARK} a{{color:#8ab4f8}}" in plain
    assert "invert" not in plain
    inverted = html_document("<p>hi</p>", dark=True, sets_colours=True)
    assert f"{DARK} img{{filter:invert(1) hue-rotate(180deg)}}" in inverted
    assert html_document("<p>hi</p>", sets_colours=True) == html_document("<p>hi</p>")
    for page in (plain, inverted):
        assert "<script" not in page
        assert page.count("<style>") == 1


def test_the_dark_document_holds_the_original_colours_behind_a_fragment() -> None:
    """``#mailroom-original`` shows the light document's own styles: every dark rule waits for
    the target not to be targeted, and the target shows nothing. The light document has neither."""
    light = html_document("<p>hi</p>")
    light_style = light.split("<style>", 1)[1].split("img{", 1)[0]
    for sets_colours in (False, True):
        page = html_document("<p>hi</p>", dark=True, sets_colours=sets_colours)
        style = page.split("<style>", 1)[1].split("</style>", 1)[0]
        assert style.startswith(light_style)
        dark_rules = style.removeprefix(light_style).removesuffix("img{max-width:100%;height:auto}")
        assert dark_rules.startswith(DARK)
        assert all(rule.startswith(DARK) for rule in dark_rules.split("}")[:-1])
        assert page.split("<body>", 1)[1] == f"{TARGET}<p>hi</p></body></html>\n"
    assert ORIGINAL_TARGET not in light


def test_a_message_without_html_has_no_html_view(mailbox: Mailbox) -> None:
    mailbox.seed(mime("plain", text="hello"))
    [ref] = mailbox.refs()
    assert detail(mailbox.client.get(f"{MAIL}/messages/{ref}/html")) == (404, "not_found")
    check = mailbox.client.get(f"{MAIL}/messages/{ref}/html/check")
    assert detail(check) == (404, "not_found")
    assert check.headers["cache-control"] == "private, no-store"


def test_the_html_view_of_a_message_just_read_is_not_read_again(
    mailbox: Mailbox, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox.seed(related_html('<p>Hi <img src="cid:logo@x"> <img src="https://t.example/p.gif">'))
    [ref] = mailbox.refs()
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(application.state, "html_views", HtmlViews(lambda: clock.now))
    made: list[bool] = []
    html_view = mail_core.html_view

    def counted(*args: Any, remote: bool, **kwargs: Any) -> Any:
        made.append(remote)
        return html_view(*args, remote=remote, **kwargs)

    monkeypatch.setattr(mail_core, "html_view", counted)
    client = mailbox.client

    assert mailbox.get(f"/messages/{ref}")["remote_images"] == 1
    check = client.get(f"{MAIL}/messages/{ref}/html/check")
    assert (check.status_code, check.content) == (204, b"")
    assert check.headers["cache-control"] == "private, no-store"
    light = client.get(f"{MAIL}/messages/{ref}/html").text
    dark = client.get(f"{MAIL}/messages/{ref}/html", params={"dark": "1"}).text
    # The read's own sanitising pass, cid: image inlined, serves the check and both documents.
    assert made == []
    assert "data:image/png;base64," in light
    assert dark.split("<body>", 1)[1] == TARGET + light.split("<body>", 1)[1]

    # Remote images shown are another view, read once.
    for _ in range(2):
        shown = client.get(f"{MAIL}/messages/{ref}/html", params={"remote": "1"})
        assert "https://t.example/p.gif" in shown.text
    assert made == [True]

    clock.now = HTML_VIEW_SECONDS
    assert client.get(f"{MAIL}/messages/{ref}/html").text == light
    assert made == [True, False]


def test_kept_html_views_are_each_users_own_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = SimpleNamespace(now=0.0)
    views = HtmlViews(lambda: clock.now)
    message = MessageRef(account=1, folder="INBOX", uid_validity=7, uid=3)
    mine = HtmlKey.of(1, 0, message, remote=False)
    view = SafeHtml("<p>mine</p>", 0, 0, sets_colours=False)
    views.put(mine, view)
    assert views.get(mine) is view
    # Another user with the same account id and ref, a changed account, remote content shown,
    # another UIDVALIDITY: none of them is the view kept.
    for other in (
        HtmlKey.of(2, 0, message, remote=False),
        HtmlKey.of(1, 1, message, remote=False),
        HtmlKey.of(1, 0, message, remote=True),
        HtmlKey.of(1, 0, replace(message, uid_validity=8), remote=False),
    ):
        assert views.get(other) is None
    clock.now = HTML_VIEW_SECONDS
    assert views.get(mine) is None

    monkeypatch.setattr(read_service, "HTML_VIEW_CHARS", 25)
    first, second = (HtmlKey.of(1, 0, replace(message, uid=uid), remote=False) for uid in (1, 2))
    views.put(first, SafeHtml("x" * 15, 0, 0, sets_colours=False))
    views.put(second, SafeHtml("y" * 15, 0, 0, sets_colours=False))
    # The oldest made room; a view past the whole budget is not kept at all.
    assert (views.get(first), views.get(second) is not None) == (None, True)
    views.put(mine, SafeHtml("z" * 26, 0, 0, sets_colours=False))
    assert (views.get(mine), views.get(second) is not None) == (None, True)


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


def test_the_source_downloads_as_an_eml_file(
    mailbox: Mailbox, migrated_engine: Any, user: User
) -> None:
    message = mime("Q3 report: draft/final", text="the numbers")
    mailbox.seed(message)
    [ref] = mailbox.refs()
    client = mailbox.client

    response = client.get(f"{MAIL}/messages/{ref}/source")
    assert response.status_code == 200
    assert response.content == message.encode()
    headers = response.headers
    assert headers["content-disposition"] == (
        "attachment; filename*=UTF-8''Q3%20report%3A%20draft_final.eml"
    )
    assert headers["content-type"] == "message/rfc822"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert headers["cache-control"] == "private, no-store"
    # Reading the source never marks the message read.
    assert mailbox.get(f"/messages/{ref}")["flags"]["seen"] is False

    store = user_store(migrated_engine, user.id)
    current = load_accounts(store)
    store.save_settings({**current.doc, "limits": {"attachment_max_mb": 0.00001}}, current.revision)
    assert detail(client.get(f"{MAIL}/messages/{ref}/source")) == (413, "too_large")


def test_a_message_without_a_subject_downloads_as_message_eml(mailbox: Mailbox) -> None:
    mailbox.seed(mime("", text="hello"))
    [ref] = mailbox.refs()
    response = mailbox.client.get(f"{MAIL}/messages/{ref}/source")
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''message.eml"


def test_the_printable_page_escapes_headers_and_loads_nothing(mailbox: Mailbox) -> None:
    mailbox.seed(
        related_html(
            '<p onclick="steal()">Hi <img src="cid:logo@x"> '
            '<img src="https://t.example/pixel.gif"></p><script>alert(1)</script>'
            '<div style="display:inline-block;margin-top:-400px;background-color:#fff">'
            "<table><tr><th>From</th><td>Bank</td></tr></table></div>"
        ).replace("Subject: News", "Subject: <b>News</b> & more")
    )
    [ref] = mailbox.refs()
    response = mailbox.client.get(f"{MAIL}/messages/{ref}/print")
    assert response.status_code == 200
    headers = response.headers
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert headers["content-security-policy"] == PRINT_CSP
    assert "img-src data:;" in PRINT_CSP
    assert "script-src" not in PRINT_CSP
    assert "frame-ancestors 'none'" in PRINT_CSP
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "private, no-store"
    assert "content-disposition" not in headers
    page = response.text
    assert page.startswith("<!doctype html>")
    assert "<title>&lt;b&gt;News&lt;/b&gt; &amp; more</title>" in page
    assert "<b>News</b>" not in page
    assert "Shop &lt;shop@example.org&gt;" in page
    assert "data:image/png;base64," in page
    for dropped in ("t.example", "<script", "alert(1)", "onclick"):
        assert dropped not in page
    # The sender's markup is boxed in below the headers, and the headers' look is not its.
    head, body = page.split('<div class="body">', 1)
    assert "Shop &lt;shop@example.org&gt;" in head
    assert "margin-top:-400px" in body
    assert "<th>From</th>" in body
    assert ".body{position:relative;overflow:hidden;contain:paint}" in head
    assert "th{" not in head.replace(".headers>tbody>tr>th{", "")


def test_the_printable_page_of_a_text_message_has_its_whole_text(mailbox: Mailbox) -> None:
    text = "\n".join([f"line {n} <tag>" for n in range(2000)] + ["> quoted & kept"])
    mailbox.seed(mime("plain", text=text))
    [ref] = mailbox.refs()
    page = mailbox.client.get(f"{MAIL}/messages/{ref}/print").text
    body = page.split('<pre class="text">', 1)[1]
    assert "line 0 &lt;tag&gt;" in body
    assert "line 1999 &lt;tag&gt;" in body
    assert "&gt; quoted &amp; kept" in body
    assert "<tag>" not in page


def test_download_names_and_types() -> None:
    assert download_name("../../etc/passwd", "attachment-2") == "_.._etc_passwd"
    # Nothing a header could break on: control characters go, the rest is percent-encoded.
    tricky = download_name('a\r\nSet-Cookie: x"; b.pdf', "attachment-2")
    assert tricky == 'aSet-Cookie: x"; b.pdf'
    assert quote(tricky, safe="") == "aSet-Cookie%3A%20x%22%3B%20b.pdf"
    assert download_name("a" + chr(0x202E) + "exe.pdf\x00", "attachment-2") == "aexe.pdf"
    assert download_name(None, "attachment-1.2") == "attachment-1.2"
    assert download_name("  ..  ", "attachment-3") == "attachment-3"
    assert len(download_name("x" * 400, "attachment-2")) == 150
    assert served_type("image/png") == "image/png"
    for kind in ("text/html", "image/svg+xml", "application/pdf", "application/xml"):
        assert served_type(kind) == "application/octet-stream"


def test_an_unreachable_server_is_refused(user_client: TestClient) -> None:
    """Port 9 on loopback is closed."""
    assert user_client.post("/api/v1/accounts", json=new_account()).status_code == 201
    response = user_client.get(f"{MAIL}/accounts/1/folders")
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
        answers["folders"] = user_client.get(f"{MAIL}/accounts/1/folders")

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
    assert client.get(f"{MAIL}/accounts/1/folders").status_code == 200
    account = new_account(login=mailbox.login, imap=endpoint(mailbox.server))
    change = {k: v for k, v in account.items() if k != "password"}
    change["password"] = "wrong password"
    assert client.put("/api/v1/accounts/1", json=change).status_code == 200
    # The open connection logged in with the old password; it went with the change.
    assert detail(client.get(f"{MAIL}/accounts/1/folders")) == (409, "auth_failed")

    change["password"] = mailbox.server.password
    assert client.put("/api/v1/accounts/1", json=change).status_code == 200
    assert client.get(f"{MAIL}/accounts/1/folders").status_code == 200
    assert client.delete("/api/v1/accounts/1").status_code == 204
    assert detail(client.get(f"{MAIL}/accounts/1/folders")) == (404, "not_found")


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
        pool.invalidate(1, 1)
        assert made[1].closed == [1]
        assert pool.close_idle() == 0
        assert pool.close_idle(now=time.monotonic() + 61) == 1
        assert made[1].closed == [1, None]
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


class _PagedConnections:
    """Account 1 has three messages in INBOX; account 2's folders fail once, then answer; account
    3 reads until it is interrupted."""

    def __init__(self) -> None:
        self.cut = threading.Event()
        self.asked: list[int] = []

    def folders(self, account_id: int) -> list[Folder]:
        self.asked.append(account_id)
        if account_id == 2 and self.asked.count(2) == 1:
            raise MailError("unreachable")
        return [cast("Folder", SimpleNamespace(path="INBOX", role=None, selectable=True))]

    def with_mailbox(self, account_id: int, path: str, read_only: bool, fn: Any) -> Any:
        if account_id == 3:
            self.cut.wait(5)
            raise MailError("unreachable")
        arrived = datetime(2026, 9, 1, tzinfo=UTC)

        class Client:
            def _raw_command_untagged(self, _command: bytes, arguments: list[Any]) -> list[bytes]:
                top = 3
                if b"UID" in arguments:
                    top = int(arguments[arguments.index(b"UID") + 1].split(b":")[1])
                return [" ".join(str(uid) for uid in range(1, min(top, 3) + 1)).encode()]

            def fetch(self, uids: list[int], _items: list[str]) -> dict[int, dict[bytes, Any]]:
                return {uid: {b"INTERNALDATE": arrived.replace(hour=uid)} for uid in uids}

        return fn(MailboxContext(Client(), path, read_only, 7, 3, None, account_id))  # type: ignore[arg-type]

    def interrupt(self, account_id: int) -> int:
        if account_id == 3:
            self.cut.set()
        return 1


def test_an_all_accounts_search_reports_a_failed_account_on_every_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An account that failed, before its folders were found or when it was cut, is reported
    with its code on every later page without being asked again, even when it would answer
    now (it would have no place in the cursor, so its mail would be skipped in silence), and
    paging ends."""
    from mailroom.mail.service import read as service_read

    monkeypatch.setattr(service_read, "ACCOUNT_SECONDS", 0.3)
    connections: Any = _PagedConnections()
    seen: list[tuple[int, int]] = []
    failed: list[list[tuple[int, str | None, str]]] = []
    cursor = None
    for _ in range(5):
        page = service_read._search_accounts(  # noqa: SLF001 - the cursor, without IMAP
            connections, [1, 2, 3], "*", SearchQuery(), limit=2, cursor=cursor
        )
        failed.append([(f.account, f.folder, f.code) for f in page.failures])
        seen += [(m.account, m.uid) for m in page.messages]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert (seen, cursor) == ([(1, 3), (1, 2), (1, 1)], None)
    assert failed == [[(2, None, "unreachable"), (3, "INBOX", "timeout")]] * 2
    assert connections.asked.count(2) == 1


class _GmailConnections:
    """``gmail`` has Gmail's extensions and ``plain`` has not; any other call fails the test."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def capabilities(self, account_id: str) -> Capabilities:
        self.asked.append(account_id)
        gmail = account_id == "gmail"
        return Capabilities(
            gmail=gmail,
            thread=gmail,
            move=True,
            uidplus=True,
            condstore=True,
            special_use=True,
            quota=False,
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
    assert stoppable.with_connection(1, lambda _client: "done") == "done"
    sessions.readied = stop.set  # the deadline passes while the session readies
    with pytest.raises(MailError) as cut:
        stoppable.with_connection(1, lambda _client: ran.append("fn"))
    assert (cut.value.code, ran, sessions.sessions) == ("timeout", [], 2)
    with pytest.raises(MailError):
        stoppable.with_connection(1, lambda _client: ran.append("fn"))
    assert (ran, sessions.sessions) == ([], 2)
    assert stoppable.folders(1) == []
    plain = service_shared._Stoppable(cast("MailConnections", sessions), stop)  # noqa: SLF001
    with pytest.raises(MailError):
        plain.folders(1)
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
        response = mailbox.client.get(f"{MAIL}/accounts/1/messages")
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
    response = mailbox.client.get(f"{MAIL}/accounts/1/messages")
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
    assert page["failures"] == [{"account_id": 1, "folder": "INBOX", "code": "password_missing"}]


def test_a_password_that_does_not_open_is_refused_before_connecting(
    mailbox: Mailbox, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(application.state, "secrets_subkey", secrets_subkey(os.urandom(32)))
    for path in ("/accounts/1/folders", "/accounts/1/messages"):
        response = mailbox.client.get(f"{MAIL}{path}")
        assert detail(response) == (409, "password_missing")
    one = mailbox.client.post(f"{MAIL}/search", json={"account": 1})
    assert detail(one) == (409, "password_missing")


def test_without_the_secrets_key_mail_is_unavailable(
    mailbox: Mailbox, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox.seed(mime("one"))
    [ref] = mailbox.refs()
    monkeypatch.setattr(application.state, "secrets_subkey", None)
    for path in ("/accounts/1/folders", "/accounts/1/messages", f"/messages/{ref}"):
        assert detail(mailbox.client.get(f"{MAIL}{path}")) == (503, "secrets_key_missing")
    assert detail(mailbox.client.post(f"{MAIL}/search", json={})) == (503, "secrets_key_missing")


def test_a_full_process_answers_busy(
    mailbox: Mailbox, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = MailPool(process_calls=0)
    monkeypatch.setattr(application.state, "mail_pool", full)
    try:
        response = mailbox.client.get(f"{MAIL}/accounts/1/folders")
    finally:
        full.close()
    assert detail(response) == (429, "busy")
    assert response.json()["detail"]["message"] == MESSAGES["busy"]


def test_mail_answers_are_never_cached(mailbox: Mailbox) -> None:
    mailbox.seed(mime("one", text="hello"))
    [ref] = mailbox.refs()
    client = mailbox.client
    for response in (
        client.get(f"{MAIL}/accounts/1/folders"),
        client.get(f"{MAIL}/accounts/1/messages"),
        client.post(f"{MAIL}/search", json={}),
        client.get(f"{MAIL}/messages/{ref}"),
    ):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
    refused = client.get(f"{MAIL}/messages/{ref}/html")
    assert detail(refused) == (404, "not_found")
    assert refused.headers["cache-control"] == "private, no-store"
    missing = client.get(f"{MAIL}/accounts/2/folders")
    assert missing.json() == {"detail": {"code": "not_found", "message": "No such account."}}
    assert missing.headers["cache-control"] == "private, no-store"
    invalid = client.get(f"{MAIL}/accounts/1/messages", params={"limit": 0})
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
        "account": 1,
        "folder": "INBOX",
        "ok": True,
        "count": 2,
        "destination_account": None,
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
        "account_ids": [1],
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
    stale = format_ref(MessageRef(1, "INBOX", message.uid_validity + 1, message.uid))
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
    # The folder acted from matters on Gmail only.
    [group] = mailbox.organize("archive", refs=[ref], source_folder="Archive")
    assert (group["ok"], group["count"], len(group["new_refs"])) == (True, 1, 1)
    assert subjects(mailbox, "Archive") == ["keep"]
    for action, body in (("archive", {"source_folder": ""}), ("flag", {"source_folder": "INBOX"})):
        refused = mailbox.client.post(f"{MAIL}/messages/{action}", json={"refs": [ref], **body})
        assert refused.status_code == 422, action

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
    assert refused.json()["detail"]["message"].endswith("Account 1 has no Archive folder.")
    assert subjects(mailbox, "Archive") == ["keep"]


def test_junk_reports_spam_to_the_junk_folder_or_is_unsupported(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mailbox.seed(mime("buy now", sender="Spam <spam@example.org>"))
    [ref] = mailbox.refs()
    [group] = mailbox.organize("junk", refs=[ref])
    assert (group["ok"], group["count"], group["junk"], len(group["new_refs"])) == (
        True,
        1,
        "spam",
        1,
    )
    assert subjects(mailbox, "Junk") == ["buy now"]
    assert subjects(mailbox, "INBOX") == []
    [record] = audited(user.id)
    assert (record.actor, record.action, record.result) == ("user", "junk", "ok")
    assert (record.detail["folder"], record.detail["destination"]) == ("INBOX", "Junk")
    assert record.detail["junk"] == "spam"

    # The stale ref is refused, never another message.
    stale = mailbox.client.post(f"{MAIL}/messages/junk", json={"refs": [ref]})
    assert detail(stale)[0] == 404

    [junked] = group["new_refs"]
    again = mailbox.client.post(f"{MAIL}/messages/junk", json={"refs": [junked]})
    assert detail(again) == (400, "invalid_argument")

    # A server with no folder in the junk role.
    real = MailConnections.folders

    def without_junk(self: MailConnections, account_id: str, **kwargs: Any) -> list[Folder]:
        return [
            replace(f, role=None) if f.role == "junk" else f
            for f in real(self, account_id, **kwargs)
        ]

    monkeypatch.setattr(MailConnections, "folders", without_junk)
    mailbox.seed(mime("more"))
    [inbox] = mailbox.refs()
    refused = mailbox.client.post(f"{MAIL}/messages/junk", json={"refs": [inbox]})
    assert detail(refused) == (502, "unsupported")
    assert refused.json()["detail"]["message"].endswith("Account 1 has no Junk folder.")
    assert subjects(mailbox, "INBOX") == ["more"]
    assert [r.result for r in audited(user.id) if r.action == "junk"].count("unsupported") == 1


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
        1,
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
    assert mailbox.client.delete("/api/v1/accounts/1").status_code == 204
    assert [(r.action, r.account_id) for r in audited(user.id)] == [("trash", 1)]


def test_flags_change_read_back_and_repeat(mailbox: Mailbox) -> None:
    mailbox.seed(mime("news"))
    [ref] = mailbox.refs()

    def flags() -> set[str]:
        [message] = folder_contents(mailbox.server, mailbox.login, "INBOX")
        return set(message.flags)

    for _ in range(2):  # the same change twice answers the same
        [group] = mailbox.organize("flag", refs=[ref], seen=True)
        assert group == {
            "account": 1,
            "folder": "INBOX",
            "ok": True,
            "count": 1,
            "new_refs": [],
            "destination_account": None,
            "junk": None,
            "flags": [{"ref": ref, "seen": True, "flagged": False, "to_sort": False}],
            "error": None,
        }
        assert flags() == {"\\Seen"}
    [starred] = mailbox.organize("flag", refs=[ref], seen=False, flagged=True)
    assert starred["flags"] == [{"ref": ref, "seen": False, "flagged": True, "to_sort": False}]
    assert flags() == {"\\Flagged"}
    [unstarred] = mailbox.organize("flag", refs=[ref], flagged=False, seen=None)
    assert unstarred["flags"] == [{"ref": ref, "seen": False, "flagged": False, "to_sort": False}]
    assert mailbox.get(f"/messages/{ref}")["flags"]["flagged"] is False
    [marked] = mailbox.organize("flag", refs=[ref], to_sort=True)
    assert marked["flags"] == [{"ref": ref, "seen": False, "flagged": False, "to_sort": True}]
    assert flags() == {TO_SORT}
    assert mailbox.get(f"/messages/{ref}")["flags"]["to_sort"] is True
    [cleared] = mailbox.organize("flag", refs=[ref], to_sort=False)
    assert cleared["flags"][0]["to_sort"] is False
    assert flags() == set()

    neither = mailbox.client.post(f"{MAIL}/messages/flag", json={"refs": [ref]})
    assert detail(neither) == (400, "invalid_argument")
    assert neither.json()["detail"]["message"].endswith(
        "Give `seen`, `flagged`, `to_sort` or several."
    )


def test_a_batch_is_refused_whole_before_any_write(mailbox: Mailbox) -> None:
    create_archive(mailbox)
    mailbox.seed(mime("mine"))
    [ref] = mailbox.refs()
    message = parse_ref(ref)
    elsewhere = format_ref(MessageRef(2, "INBOX", message.uid_validity, message.uid))
    stale = format_ref(MessageRef(1, "INBOX", message.uid_validity + 1, message.uid))
    client = mailbox.client

    def post(action: str, **body: Any) -> Any:
        return client.post(f"{MAIL}/messages/{action}", json=body)

    for action, extra in (
        ("move", {"folder": "Archive"}),
        ("archive", {}),
        ("trash", {}),
        ("junk", {}),
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
    store.save_settings(current.doc, current.revision, SecretChanges(remove=[2]))
    mailbox.seed(mime("mine"))
    [ref] = mailbox.refs()
    message = parse_ref(ref)
    locked = format_ref(MessageRef(2, "INBOX", message.uid_validity, message.uid))
    actions: tuple[tuple[str, dict[str, Any]], ...] = (
        ("move", {"folder": "Archive"}),
        ("archive", {}),
        ("trash", {}),
        ("junk", {}),
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
    assert subjects(mailbox, "Junk") == []


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
    listed = mailbox.get("/accounts/1/folders", **params)["folders"]
    return {folder["path"]: folder for folder in listed}


def server_folders(mailbox: Mailbox) -> set[str]:
    with raw_client(mailbox.server, mailbox.login) as client:
        return {name for _flags, _delimiter, name in client.list_folders()}


def folder_call(mailbox: Mailbox, action: str, **body: Any) -> Any:
    """``POST /accounts/1/folders[/<action>]``: the response."""
    path = f"{MAIL}/accounts/1/folders" + (f"/{action}" if action != "create" else "")
    return mailbox.client.post(path, json=body)


def add_rule_set(
    db: Session,
    user_id: int,
    folder: list[str],
    *specs: dict[str, Any],
    account_id: int | None = 1,
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
        {"account_id": 1, "path": "Projects"},
    )
    assert created.headers["cache-control"] == "private, no-store"
    inner = folder_call(mailbox, "create", parent="Projects", name="Città 2026")
    assert inner.json() == {"account_id": 1, "path": "Projects/Città 2026"}
    assert {"Projects", "Projects/Città 2026"} <= set(folder_paths(mailbox))

    renamed = folder_call(mailbox, "rename", folder="Projects", parent=None, name="Work")
    assert (renamed.status_code, renamed.json()) == (
        200,
        {
            "account_id": 1,
            "folder": "Projects",
            "path": "Work",
            "rule_sets": [],
            "filters": [],
            "rule_sets_updated": True,
            "all_accounts_rule_sets": [],
        },
    )
    assert {"Work", "Work/Città 2026"} <= server_folders(mailbox)
    assert "Projects" not in server_folders(mailbox)

    mailbox.seed(mime("one"), folder="Work/Città 2026")
    mailbox.seed(mime("two"), folder="Work/Città 2026")
    deleted = folder_call(mailbox, "delete", folder="Work/Città 2026", messages=2)
    assert (deleted.status_code, deleted.json()) == (
        200,
        {"account_id": 1, "folder": "Work/Città 2026", "messages": 2},
    )
    assert "Work/Città 2026" not in server_folders(mailbox)

    records = audited(user.id)
    assert [(r.actor, r.action, r.account_id, r.result, r.detail) for r in records] == [
        ("user", "folder_create", 1, "ok", {"folder": "Projects"}),
        ("user", "folder_create", 1, "ok", {"folder": "Projects/Città 2026"}),
        (
            "user",
            "folder_rename",
            1,
            "ok",
            {"folder": "Projects", "destination": "Work", "rule_sets": [], "filters": []},
        ),
        ("user", "folder_delete", 1, "ok", {"folder": "Work/Città 2026", "messages": 2}),
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
        "uid_validity": None,
        "uid_next": None,
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


def test_a_folder_is_marked_read_up_to_the_listing_the_user_saw(
    mailbox: Mailbox, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    create_folders(mailbox.server, mailbox.login, "Lists")
    mailbox.seed(mime("one"), folder="Lists")
    mailbox.seed(mime("two"), folder="Lists")
    mailbox.seed(mime("seen"), folder="Lists", flags=("\\Seen",))
    listed = folder_paths(mailbox)["Lists"]
    assert listed["unread"] == 2
    mailbox.seed(mime("later"), folder="Lists")

    stale = folder_call(
        mailbox,
        "mark-read",
        folder="Lists",
        uid_validity=listed["uid_validity"] + 1,
        uid_next=listed["uid_next"],
    )
    assert detail(stale) == (409, "stale_reference")
    assert folder_paths(mailbox)["Lists"]["unread"] == 3

    marked = folder_call(
        mailbox,
        "mark-read",
        folder="Lists",
        uid_validity=listed["uid_validity"],
        uid_next=listed["uid_next"],
    )
    assert (marked.status_code, marked.json()) == (
        200,
        {"account_id": 1, "folder": "Lists", "marked": 2},
    )
    assert marked.headers["cache-control"] == "private, no-store"
    unread = [
        message.subject
        for message in folder_contents(mailbox.server, mailbox.login, "Lists")
        if "\\Seen" not in message.flags
    ]
    assert unread == ["later"]

    missing = folder_call(mailbox, "mark-read", folder="Gone", uid_validity=1, uid_next=2)
    assert detail(missing) == (404, "mailbox_missing")

    records = audited(user.id)
    assert [(r.actor, r.action, r.account_id, r.result, r.detail) for r in records] == [
        ("user", "mark_folder_read", 1, "stale_reference", {"folder": "Lists", "messages": 0}),
        ("user", "mark_folder_read", 1, "ok", {"folder": "Lists", "messages": 2}),
        ("user", "mark_folder_read", 1, "mailbox_missing", {"folder": "Gone", "messages": 0}),
    ]


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
        ("mark-read", {"folder": "Lists", "uid_validity": 1}),
        ("mark-read", {"folder": "Lists", "uid_validity": 1, "uid_next": 0}),
        ("mark-read", {"folder": "Lists", "uid_validity": 1, "uid_next": 2**32 + 1}),
        ("mark-read", {"folder": "Lists", "uid_validity": "1", "uid_next": 2}),
    ):
        response = folder_call(mailbox, action, **body)
        assert response.status_code == 422, (action, body)
    unknown = mailbox.client.post(
        f"{MAIL}/accounts/2/folders/delete", json={"folder": "Lists", "messages": 0}
    )
    assert detail(unknown) == (404, "not_found")
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}):
        response = mailbox.client.post(
            f"{MAIL}/accounts/1/folders",
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
    other_account = add_rule_set(db_session, user.id, ["Old"], account_id=2)
    every = add_rule_set(db_session, user.id, ["INBOX"], moving_to("Old"), account_id=None)
    bob = create_user(db_session, "bob", PASSWORD)
    bobs = add_rule_set(db_session, bob.id, ["Old"], moving_to("Old"))
    [stays, moves] = db_session.query(Filter).filter_by(rule_set_id=mover.id).order_by(Filter.id)

    answer = folder_call(mailbox, "rename", folder="Old", parent="Older", name="New")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {
        "account_id": 1,
        "folder": "Old",
        "path": "Older/New",
        "rule_sets": [enabled.id, below.id, mover.id],
        "filters": [moves.id],
        "rule_sets_updated": True,
        # Reported, not rewritten: the user's other accounts may keep their Old.
        "all_accounts_rule_sets": [every.id],
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
    assert db_session.query(Filter).filter_by(rule_set_id=every.id).one().spec == moving_to("Old")

    [record] = audited(user.id)
    assert (record.action, record.result, record.detail) == (
        "folder_rename",
        "ok",
        {
            "folder": "Old",
            "destination": "Older/New",
            "rule_sets": [enabled.id, below.id, mover.id],
            "filters": [moves.id],
            "all_accounts_rule_sets": [every.id],
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


def test_the_notification_folders_follow_a_rename_and_a_delete(
    mailbox: Mailbox, audited: Callable[[int], list[AuditRecord]]
) -> None:
    create_folders(mailbox.server, mailbox.login, "Old", "Old/Sub", "Older", "Gone")
    chosen = {"folders": ["INBOX", "Old", "Old/Sub", "Older", "Gone"]}
    assert mailbox.client.put(f"{MAIL}/notifications/accounts/1", json=chosen).status_code == 200

    renamed = folder_call(mailbox, "rename", folder="Old", parent="Older", name="New")
    assert renamed.status_code == 200, renamed.text
    deleted = folder_call(mailbox, "delete", folder="Gone", messages=0)
    assert deleted.status_code == 200, deleted.text
    assert mailbox.client.get(f"{MAIL}/notifications").json() == {
        "accounts": [{"account_id": 1, "folders": ["INBOX", "Older/New", "Older/New/Sub", "Older"]}]
    }


def test_a_rename_stands_when_the_notification_folders_cannot_follow(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mailroom.mail import notifications

    def broken(*_args: object) -> None:
        raise RuntimeError("UPDATE … 'Old' …")

    create_folders(mailbox.server, mailbox.login, "Old")
    monkeypatch.setattr(notifications, "rename_folder", broken)
    with caplog.at_level(logging.INFO, logger="mailroom.mail.service"):
        answer = folder_call(mailbox, "rename", folder="Old", parent=None, name="New")
    assert answer.status_code == 200, answer.text
    assert answer.json()["rule_sets_updated"] is True
    assert "New" in server_folders(mailbox)
    failed = next(r for r in caplog.records if r.msg == "folder_notifications_failed")
    assert failed.__dict__["error"] == "RuntimeError"
    assert "Old" not in str(failed.__dict__)


def test_a_delete_stands_when_the_notification_folders_cannot_follow(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mailroom.mail import notifications

    def broken(*_args: object) -> None:
        raise RuntimeError("DELETE … 'Gone' …")

    create_folders(mailbox.server, mailbox.login, "Gone")
    monkeypatch.setattr(notifications, "forget_folder", broken)
    with caplog.at_level(logging.INFO, logger="mailroom.mail.service"):
        answer = folder_call(mailbox, "delete", folder="Gone", messages=0)
    assert answer.status_code == 200, answer.text
    assert "Gone" not in server_folders(mailbox)
    failed = next(r for r in caplog.records if r.msg == "folder_notifications_failed")
    assert failed.__dict__["error"] == "RuntimeError"
    assert all("Gone" not in str(r.__dict__) for r in caplog.records)
    [record] = audited(user.id)
    assert (record.action, record.result, record.detail) == (
        "folder_delete",
        "ok",
        {"folder": "Gone", "messages": 0},
    )


def test_a_folder_a_rule_set_names_is_not_deleted(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
    db_session: Session,
) -> None:
    create_folders(mailbox.server, mailbox.login, "Lists", "Other")
    mailbox.seed(mime("news"), folder="Lists")
    mover = add_rule_set(db_session, user.id, ["INBOX"], moving_to("Lists"), name="Newsletters")
    add_rule_set(db_session, user.id, ["Lists"], account_id=2, name="Elsewhere")

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
    assert (renamed["via"], renamed["account_id"]) == ("web", 1)
    assert (renamed["rule_sets"], renamed["filters"], renamed["rule_sets_updated"]) == (0, 0, True)
    assert records["mail_folder_deleted", "ok"].__dict__["messages"] == 1
    for record in records.values():
        assert isinstance(record.__dict__["duration_ms"], int)
        for name in ("Secret", "Hidden", "Plans", "Things"):
            assert name not in str(record.__dict__)


def test_a_message_drafts_a_filter_from_its_list_and_sender(
    mailbox: Mailbox, caplog: pytest.LogCaptureFixture
) -> None:
    listed = mime("Weekly", sender="Shop <news@shop.example>", text="hi").replace(
        "MIME-Version", "List-Id: Shop news <news.shop.example>\r\nMIME-Version", 1
    )
    mailbox.seed(listed)
    mailbox.seed(mime("Hello", sender="Maria <maria@gmail.com>", text="hi"))
    maria, shop = mailbox.refs()

    caplog.set_level(logging.INFO, logger="mailroom")
    response = mailbox.client.get(f"{MAIL}/messages/{shop}/filter-draft")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    draft = response.json()
    assert draft["name"] == "Shop news"
    assert [c["type"] for c in draft["criteria"]] == ["list_is", "from_is", "from_domain"]
    assert draft["spec"] == {"criterion": draft["criteria"][0], "action": {"type": "no_op"}}
    assert "news@shop.example" not in caplog.text
    assert "shop.example" not in caplog.text

    draft = mailbox.get(f"/messages/{maria}/filter-draft")
    assert draft["criteria"] == [{"type": "from_is", "args": {"addr": "maria@gmail.com"}}]
    assert draft["name"] == "Maria"
    # Drafting never marks the message read.
    assert mailbox.get(f"/messages/{maria}")["flags"]["seen"] is False


def test_a_message_mutes_its_thread_until_it_is_unmuted(
    mailbox: Mailbox,
    db_session: Session,
    user: User,
    audited: Callable[[int], list[AuditRecord]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    mailbox.seed(mime("Plans", message_id="<root@example.org>", text="hi"))
    mailbox.seed(
        mime(
            "Re: Plans",
            message_id="<r1@example.org>",
            in_reply_to="<root@example.org>",
            references="<root@example.org>",
        )
    )
    reply, root = mailbox.refs()

    caplog.set_level(logging.INFO, logger="mailroom")
    response = mailbox.client.post(f"{MAIL}/messages/{root}/mute")
    assert response.status_code == 201, response.text
    first = response.json()
    assert (first["account_id"], first["ids"], first["gmail"]) == (1, 1, False)
    # Run filters is off: the answer says so, as muting does nothing until it is on.
    assert first["run_filters"] is False
    assert "root@example.org" not in caplog.text
    # The reply belongs to the muted thread: muting it adds its ids to that thread.
    again = mailbox.client.post(f"{MAIL}/messages/{reply}/mute").json()
    assert (again["id"], again["ids"]) == (first["id"], 2)
    assert mailbox.get("/muted") == {"threads": [again]}
    [thread] = db_session.scalars(select(MutedThread)).all()
    assert thread.message_ids == ["<root@example.org>", "<r1@example.org>"]
    # The rule set doing it is hidden from the user's.
    assert mailbox.client.get("/api/v1/accounts/1/rule-sets").json() == []
    [hidden] = db_session.scalars(select(RuleSet).where(RuleSet.user_id == user.id)).all()
    assert (hidden.kind, hidden.folder, hidden.trigger, hidden.enabled) == (
        "mute",
        ["INBOX"],
        "new_mail",
        True,
    )
    # Muting reads the headers only, never marking the message read.
    assert mailbox.get(f"/messages/{root}")["flags"]["seen"] is False

    assert mailbox.client.delete(f"{MAIL}/muted/{first['id']}").status_code == 204
    assert mailbox.get("/muted") == {"threads": []}
    assert db_session.scalars(select(RuleSet).where(RuleSet.user_id == user.id)).all() == []
    gone = mailbox.client.delete(f"{MAIL}/muted/{first['id']}")
    assert (gone.status_code, gone.json()) == (404, {"detail": "No such muted thread"})
    records = [(r.action, r.actor, r.result, r.account_id) for r in audited(user.id)]
    assert records == [
        ("mute", "user", "ok", 1),
        ("mute", "user", "ok", 1),
        ("unmute", "user", "ok", 1),
    ]
    assert audited(user.id)[0].detail["muted_thread_id"] == first["id"]


def test_a_message_bridging_two_muted_threads_makes_them_one(
    mailbox: Mailbox, db_session: Session, user: User, audited: Callable[[int], list[AuditRecord]]
) -> None:
    mailbox.seed(mime("First", message_id="<a@example.org>"))
    mailbox.seed(mime("Second", message_id="<b@example.org>"))
    both = "<a@example.org> <b@example.org>"
    mailbox.seed(mime("Re: both", message_id="<c@example.org>", references=both))
    bridge, second, first = mailbox.refs()
    a = mailbox.client.post(f"{MAIL}/messages/{first}/mute").json()
    b = mailbox.client.post(f"{MAIL}/messages/{second}/mute").json()
    assert a["id"] != b["id"]

    merged = mailbox.client.post(f"{MAIL}/messages/{bridge}/mute").json()
    assert (merged["id"], merged["ids"]) == (a["id"], 3)
    assert mailbox.get("/muted") == {"threads": [merged]}
    [thread] = db_session.scalars(select(MutedThread)).all()
    assert thread.message_ids == ["<a@example.org>", "<b@example.org>", "<c@example.org>"]
    assert audited(user.id)[-1].detail["merged"] == [b["id"]]


def test_a_gmail_thread_alone_is_muted_and_one_past_bigint_is_not(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailroom.mail.service import read

    mailbox.seed(mime("Plans"))
    [ref] = mailbox.refs()
    thread = 2**63 - 1

    def gmail_only(_connections: object, _message: object) -> ThreadKey:
        return ThreadKey((), thread)

    monkeypatch.setattr(read.core, "thread_key", gmail_only)
    muted = mailbox.client.post(f"{MAIL}/messages/{ref}/mute")
    assert muted.status_code == 201, muted.text
    assert (muted.json()["ids"], muted.json()["gmail"]) == (0, True)

    thread = 2**63
    assert detail(mailbox.client.post(f"{MAIL}/messages/{ref}/mute")) == (400, "nothing_to_mute")


def test_a_message_without_a_message_id_cannot_be_muted(mailbox: Mailbox) -> None:
    raw = mime("Anonymous", text="hi")
    mailbox.seed(
        "".join(
            line for line in raw.splitlines(keepends=True) if not line.startswith("Message-ID:")
        )
    )
    [ref] = mailbox.refs()
    assert detail(mailbox.client.post(f"{MAIL}/messages/{ref}/mute")) == (400, "nothing_to_mute")
    assert mailbox.get("/muted") == {"threads": []}
