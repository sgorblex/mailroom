"""Moving messages to another of the user's accounts through ``POST /mail/messages/move-to-account``,
against the test Dovecot: two logins stand for the user's accounts ``work`` and ``home``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from mailroom_core.mail import MailError
from mailroom_core.testing import folder_contents, mime
from tests.test_api_accounts import new_account
from tests.test_api_mail import MAIL, Mailbox, detail, endpoint
from tests.test_api_mail import mailbox as mailbox  # the fixture

if TYPE_CHECKING:
    from collections.abc import Callable

    from mailroom.models import AuditRecord, User
    from mailroom_core.testing import ImapTestServer

pytestmark = pytest.mark.integration

MOVE = f"{MAIL}/messages/move-to-account"


@pytest.fixture
def home(mailbox: Mailbox, imap_server: ImapTestServer) -> str:
    """The login of the user's second account, ``home``."""
    login = imap_server.unique_user("mailapi-home")
    account = new_account(
        "home", login=login, password=imap_server.password, imap=endpoint(imap_server)
    )
    assert mailbox.client.post("/api/v1/accounts", json=account).status_code == 201
    return login


def subjects(server: ImapTestServer, login: str, folder: str) -> list[str]:
    return sorted(m.subject for m in folder_contents(server, login, folder))


def test_moves_to_another_account_and_records_it_in_both(
    mailbox: Mailbox, home: str, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    mailbox.seed(mime("for home", sender="Ada <ada@example.org>"))
    [ref] = mailbox.refs()

    [group] = mailbox.organize(
        "move-to-account", refs=[ref], account="home", folder="INBOX", removal="trash"
    )

    assert {k: v for k, v in group.items() if k != "new_refs"} == {
        "account": "work",
        "folder": "INBOX",
        "ok": True,
        "count": 1,
        "destination_account": "home",
        "junk": None,
        "flags": [],
        "error": None,
    }
    [new_ref] = group["new_refs"]
    assert mailbox.get(f"/messages/{new_ref}")["subject"] == "for home"
    assert subjects(mailbox.server, home, "INBOX") == ["for home"]
    assert subjects(mailbox.server, mailbox.login, "INBOX") == []
    assert subjects(mailbox.server, mailbox.login, "Trash") == ["for home"]
    recorded = [(r.action, r.account_id, r.result) for r in audited(user.id)]
    assert sorted(recorded) == [
        ("move_to_account", "home", "ok"),
        ("move_to_account", "work", "ok"),
    ]
    by_account = {r.account_id: r.detail for r in audited(user.id)}
    assert (by_account["work"]["destination_account"], by_account["work"]["removed"]) == (
        "home",
        "trashed",
    )
    assert (by_account["home"]["source_account"], by_account["home"]["source_folder"]) == (
        "work",
        "INBOX",
    )


def test_deletes_the_originals_for_good_when_asked(
    mailbox: Mailbox, home: str, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    mailbox.seed(mime("gone from work"))
    [ref] = mailbox.refs()

    [group] = mailbox.organize(
        "move-to-account", refs=[ref], account="home", folder="INBOX", removal="delete"
    )

    assert group["ok"]
    assert subjects(mailbox.server, home, "INBOX") == ["gone from work"]
    assert subjects(mailbox.server, mailbox.login, "INBOX") == []
    assert subjects(mailbox.server, mailbox.login, "Trash") == []
    [record] = [r for r in audited(user.id) if r.account_id == "work"]
    assert record.detail["removed"] == "expunged"


def test_refuses_what_is_not_the_users_or_cannot_be_done(mailbox: Mailbox, home: str) -> None:
    mailbox.seed(mime("stay"))
    [ref] = mailbox.refs()

    def post(**body: Any) -> Any:
        return mailbox.client.post(
            MOVE, json={"refs": [ref], "folder": "INBOX", "removal": "trash"} | body
        )

    assert detail(post(account="elsewhere")) == (404, "not_found")
    assert detail(post(account="work")) == (400, "invalid_argument")
    assert detail(post(account="home", folder="Drafts")) == (400, "invalid_argument")
    assert detail(post(account="home", folder="Nowhere")) == (404, "mailbox_missing")
    assert post(account="home", removal="burn").status_code == 422
    assert mailbox.client.post(MOVE, json={"refs": [ref], "account": "home"}).status_code == 422
    assert subjects(mailbox.server, mailbox.login, "INBOX") == ["stay"]
    assert subjects(mailbox.server, mailbox.login, "Trash") == []
    assert subjects(mailbox.server, home, "INBOX") == []


@pytest.mark.usefixtures("home")
def test_a_refused_delete_for_good_is_on_record(
    mailbox: Mailbox, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    mailbox.seed(mime("stay"))
    [ref] = mailbox.refs()

    refused = mailbox.client.post(
        MOVE, json={"refs": [ref], "account": "work", "folder": "INBOX", "removal": "delete"}
    )

    assert detail(refused) == (400, "invalid_argument")
    [record] = audited(user.id)
    assert (record.action, record.account_id, record.result) == (
        "move_to_account",
        "work",
        "invalid_argument",
    )
    assert (record.detail["destination_account"], record.detail["folder"]) == ("work", "INBOX")


@pytest.mark.usefixtures("home")
def test_a_message_over_the_users_move_limit_is_refused(mailbox: Mailbox) -> None:
    limits = mailbox.client.get("/api/v1/me/limits").json()
    assert mailbox.client.put("/api/v1/me/limits", json=limits | {"move_max_mb": 0.001}).is_success
    mailbox.seed(mime("too big", text="z" * 4000))
    [ref] = mailbox.refs()

    refused = mailbox.client.post(
        MOVE, json={"refs": [ref], "account": "home", "folder": "INBOX", "removal": "trash"}
    )

    assert detail(refused) == (413, "too_large")
    assert subjects(mailbox.server, mailbox.login, "INBOX") == ["too big"]


@pytest.mark.usefixtures("home")
def test_copies_left_in_both_are_answered_even_when_no_group_succeeded(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    mailbox.seed(mime("stuck"))
    [ref] = mailbox.refs()

    def fails(*_args: object) -> None:
        raise MailError("server_error")

    # Inside mailroom-core's move: its removal fails once the copy is made.
    monkeypatch.setattr("mailroom_core.mail.transfer.move_uids", fails)
    answer = mailbox.client.post(
        MOVE, json={"refs": [ref], "account": "home", "folder": "INBOX", "removal": "trash"}
    )

    assert answer.status_code == 200, answer.text
    [group] = answer.json()["results"]
    assert (group["ok"], group["count"], group["destination_account"]) == (False, 0, "home")
    assert len(group["new_refs"]) == 1
    assert group["error"]["code"] == "server_error"
    assert "in both" in group["error"]["message"]
