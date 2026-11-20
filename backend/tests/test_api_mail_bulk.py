"""Acting on every message a search matches through ``POST /mail/messages/count`` and
``POST /mail/messages/bulk``, against the test Dovecot: the count confirmed on each call, a
batch at a time, and a record per message."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from mailroom_core.testing import create_folders, folder_contents, mime
from tests.test_api_accounts import new_account
from tests.test_api_mail import MAIL, Mailbox, detail
from tests.test_api_mail import mailbox as mailbox  # the fixture

if TYPE_CHECKING:
    from collections.abc import Callable

    from mailroom.models import AuditRecord, User

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("numbered_accounts")]


def post(mailbox: Mailbox, route: str, **body: Any) -> Any:
    response = mailbox.client.post(f"{MAIL}/messages/{route}", json=body)
    assert response.headers["cache-control"] == "private, no-store"
    return response


def answer(mailbox: Mailbox, route: str, **body: Any) -> Any:
    response = post(mailbox, route, **body)
    assert response.status_code == 200, response.text
    return response.json()


def unread(mailbox: Mailbox, folder: str = "INBOX") -> list[str]:
    return [
        m.subject
        for m in folder_contents(mailbox.server, mailbox.login, folder)
        if "\\Seen" not in m.flags
    ]


def test_flags_what_matches_a_batch_a_call_with_the_count_confirmed(
    mailbox: Mailbox,
    audited: Callable[[int], list[AuditRecord]],
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("mailroom_core.mail.bulk.BULK_BATCH", 2)
    for subject in ("news 1", "news 2", "news 3"):
        mailbox.seed(mime(subject))
    mailbox.seed(mime("news read"), flags=("\\Seen",))
    mailbox.seed(mime("other"))
    read = {"kind": "flag", "seen": True}

    assert answer(mailbox, "count", account=1, subject="news") == {"total": 4, "failures": []}
    assert answer(mailbox, "count", account=1, subject="news", action=read)["total"] == 3

    changed = post(mailbox, "bulk", account=1, subject="news", action=read, expected=4)
    assert detail(changed) == (409, "count_changed")
    assert changed.json()["detail"]["matching"] == 3
    assert unread(mailbox) == ["news 1", "news 2", "news 3", "other"]

    first = answer(mailbox, "bulk", account=1, subject="news", action=read, expected=3)
    assert (first["done"], first["remaining"], first["failures"]) == (2, 1, [])
    [group] = first["results"]
    assert (group["folder"], group["ok"], group["count"], group["flags"]) == (
        "INBOX",
        True,
        2,
        [],
    )
    assert unread(mailbox) == ["news 1", "other"]
    last = answer(mailbox, "bulk", account=1, subject="news", action=read, expected=1)
    assert (last["done"], last["remaining"]) == (1, 0)
    assert unread(mailbox) == ["other"]

    records = audited(user.id)
    assert [(r.action, r.result, r.detail.get("seen")) for r in records] == [
        ("bulk_flag", "count_changed", None),
        ("bulk_flag", "ok", True),
        ("bulk_flag", "ok", True),
        ("bulk_flag", "ok", True),
    ]
    assert records[0].detail == {"folder": "INBOX", "messages": 3}
    assert "subject_sha256" in records[1].detail


def test_more_than_the_cap_is_refused_and_nothing_changes(
    mailbox: Mailbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mailroom_core.mail.bulk.BULK_MAX", 2)
    for subject in ("one", "two", "three"):
        mailbox.seed(mime(subject))
    refused = post(mailbox, "bulk", account=1, action={"kind": "trash"}, expected=3)
    assert detail(refused) == (409, "too_many")
    assert refused.json()["detail"]["matching"] == 3
    assert len(folder_contents(mailbox.server, mailbox.login, "INBOX")) == 3


def test_over_every_account_a_failing_one_is_reported_and_the_others_done(
    mailbox: Mailbox, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    create_folders(mailbox.server, mailbox.login, "Done")
    mailbox.seed(mime("report"))
    mailbox.seed(mime("keep"))
    closed = new_account("closed", login="closed@example.org")
    assert mailbox.client.post("/api/v1/accounts", json=closed).status_code == 201
    failure = {"account_id": 2, "folder": "INBOX", "code": "unreachable"}

    counted = answer(mailbox, "count", subject="report")
    assert counted == {"total": 1, "failures": [failure]}
    moved = answer(
        mailbox, "bulk", subject="report", action={"kind": "move", "folder": "Done"}, expected=1
    )
    # A move lists the account's folders first: it failed before one was chosen.
    assert (moved["done"], moved["remaining"], moved["failures"]) == (
        1,
        0,
        [{**failure, "folder": None}],
    )
    [group] = moved["results"]
    assert (group["account"], group["ok"], len(group["new_refs"])) == (1, True, 1)
    assert [m.subject for m in folder_contents(mailbox.server, mailbox.login, "Done")] == ["report"]
    assert sorted((r.action, r.account_id, r.result) for r in audited(user.id)) == [
        ("bulk_move", 1, "ok"),
        ("bulk_move", 2, "unreachable"),
    ]

    # Alone, the account's failure is the answer.
    alone = post(mailbox, "count", account=2)
    assert detail(alone) == (502, "unreachable")


def test_a_move_from_every_folder_counts_and_acts_without_its_destination(
    mailbox: Mailbox, audited: Callable[[int], list[AuditRecord]], user: User
) -> None:
    create_folders(mailbox.server, mailbox.login, "Lists", "Done")
    mailbox.seed(mime("report a"))
    mailbox.seed(mime("report b"), folder="Lists")
    mailbox.seed(mime("report filed"), folder="Done")
    move = {"kind": "move", "folder": "Done"}
    scope = {"account": 1, "folder": "*", "subject": "report"}

    # The count the user confirms is the move's, over the folders it moves from.
    assert answer(mailbox, "count", **scope)["total"] == 3
    assert answer(mailbox, "count", **scope, action=move)["total"] == 2
    stale = post(mailbox, "bulk", **scope, action=move, expected=3)
    assert detail(stale) == (409, "count_changed")
    assert stale.json()["detail"]["matching"] == 2
    moved = answer(mailbox, "bulk", **scope, action=move, expected=2)
    assert (moved["done"], moved["remaining"]) == (2, 0)
    assert sorted(m.subject for m in folder_contents(mailbox.server, mailbox.login, "Done")) == [
        "report a",
        "report b",
        "report filed",
    ]
    assert [(r.action, r.result) for r in audited(user.id)] == [
        ("bulk_move", "count_changed"),
        ("bulk_move", "ok"),
        ("bulk_move", "ok"),
    ]


def test_trash_never_deletes_and_what_would_still_match_is_refused(mailbox: Mailbox) -> None:
    mailbox.seed(mime("unwanted"))
    trashed = answer(mailbox, "bulk", account=1, action={"kind": "trash"}, expected=1)
    assert (trashed["done"], trashed["remaining"]) == (1, 0)
    assert [m.subject for m in folder_contents(mailbox.server, mailbox.login, "Trash")] == [
        "unwanted"
    ]
    again = post(mailbox, "bulk", account=1, folder="Trash", action={"kind": "trash"}, expected=1)
    assert detail(again) == (400, "invalid_argument")
    into_drafts = post(
        mailbox,
        "bulk",
        account=1,
        folder="Trash",
        action={"kind": "move", "folder": "Drafts"},
        expected=1,
    )
    assert detail(into_drafts) == (400, "invalid_argument")
    assert detail(post(mailbox, "bulk", account=3, action={"kind": "trash"}, expected=0)) == (
        404,
        "not_found",
    )


def test_bulk_requests_are_checked(mailbox: Mailbox) -> None:
    trash = {"kind": "trash"}
    for route, body in (
        ("bulk", {"action": trash}),
        ("bulk", {"action": trash, "expected": -1}),
        ("bulk", {"action": trash, "expected": 5001}),
        ("bulk", {"action": trash, "expected": "1"}),
        ("bulk", {"action": trash, "expected": True}),
        ("bulk", {"expected": 1}),
        ("bulk", {"action": {"kind": "delete"}, "expected": 1}),
        ("bulk", {"action": {"kind": "move"}, "expected": 1}),
        ("bulk", {"action": {"kind": "trash", "folder": "x"}, "expected": 1}),
        ("bulk", {"action": trash, "expected": 1, "limit": 5}),
        ("count", {"cursor": None}),
    ):
        assert post(mailbox, route, **body).status_code == 422, (route, body)
    for route, body in (
        ("count", {"account": 1, "action": {"kind": "flag"}}),
        ("count", {"folder": "*", "subfolders": True}),
        ("bulk", {"text": "a\r\nb", "action": trash, "expected": 0}),
    ):
        assert detail(post(mailbox, route, **body)) == (400, "invalid_argument"), (route, body)
