"""Filters that move to another account, in the app (``mailroom.filters.transfers``): the target
checked on save, on enable and at run, the ledger of copies, what a run leaves in both accounts,
and the removal of the target account. Moves on real mail: ``test_filter_runner_mail``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from mailroom.accounts.store import user_store
from mailroom.filters.transfers import (
    IN_BOTH,
    IN_BOTH_TEXT,
    LEDGER_DAYS,
    TARGET_REMOVED,
    RunTargets,
    ledger_copies,
    ledger_skip,
    rename_in_ledger,
)
from mailroom.models import TransferLedger
from mailroom.worker.runs import COPIED_NOT_REMOVED, COPIED_NOT_REMOVED_TEXT, execute_run
from mailroom_core.filters import Folder
from mailroom_core.mail import CopiedNotRemovedError, MailError, MessageRef, Moved, format_ref
from tests.test_api_filters import SHOP_SPEC, add_filter
from tests.test_filter_runner import add_account, add_rule_set, queued_run, run_out

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.worker.context import Mailboxes


def to_account(account: int | str, *folder: str) -> dict[str, Any]:
    return {
        "criterion": {"type": "from_contains", "args": {"addr": "shop"}},
        "action": {"type": "move_to_account", "args": {"account": account, "folder": [*folder]}},
    }


def refused(client: TestClient, rule_set_id: int, spec: dict[str, Any]) -> str:
    response = client.post(
        f"/api/v1/rule-sets/{rule_set_id}/filters", json={"name": "Move", "spec": spec}
    )
    assert response.status_code == 400, response.json()
    code: str = response.json()["detail"]["code"]
    return code


def test_a_filter_moves_only_to_another_of_the_users_accounts(user_client: TestClient) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home")
    rule_set_id = add_rule_set(user_client, work, enabled=False)
    assert refused(user_client, rule_set_id, to_account(work, "X")) == "move_target_same_account"
    assert refused(user_client, rule_set_id, to_account(home + 100, "X")) == (
        "move_target_not_found"
    )
    every = add_rule_set(user_client, "all", enabled=False)
    assert refused(user_client, every, to_account(home, "X")) == "move_target_same_account"
    filter_id = add_filter(user_client, rule_set_id, "Move", to_account(home, "Shop"))
    patched = user_client.patch(
        f"/api/v1/filters/{filter_id}", json={"spec": to_account(work, "Shop")}
    )
    assert patched.status_code == 400
    assert patched.json()["detail"]["code"] == "move_target_same_account"


def test_removing_the_target_turns_the_rule_sets_moving_there_off(
    user_client: TestClient,
) -> None:
    """With a reason; enabling it again is refused until the filter is changed, which clears
    the reason."""
    work = add_account(user_client)
    home = add_account(user_client, "home")
    moving = add_rule_set(user_client, work, enabled=False)
    filter_id = add_filter(user_client, moving, "Move", to_account(home, "Shop"))
    assert user_client.patch(f"/api/v1/rule-sets/{moving}", json={"enabled": True}).is_success
    other = add_rule_set(user_client, work)
    add_filter(user_client, other, "Shop", SHOP_SPEC)

    assert user_client.delete(f"/api/v1/accounts/{home}").status_code == 204
    turned_off = user_client.get(f"/api/v1/rule-sets/{moving}").json()
    assert turned_off["enabled"] is False
    assert turned_off["disabled_reason"].startswith(f"{TARGET_REMOVED}: ")
    kept = user_client.get(f"/api/v1/rule-sets/{other}").json()
    assert (kept["enabled"], kept["disabled_reason"]) == (True, None)

    again = user_client.patch(f"/api/v1/rule-sets/{moving}", json={"enabled": True})
    assert again.status_code == 400
    assert again.json()["detail"]["code"] == "move_target_not_found"
    user_client.patch(f"/api/v1/filters/{filter_id}", json={"enabled": False})
    enabled = user_client.patch(f"/api/v1/rule-sets/{moving}", json={"enabled": True}).json()
    assert (enabled["enabled"], enabled["disabled_reason"]) == (True, None)
    # Turning the filter itself on again inside the enabled rule set is refused too.
    filter_on = user_client.patch(f"/api/v1/filters/{filter_id}", json={"enabled": True})
    assert filter_on.status_code == 400
    assert filter_on.json()["detail"]["code"] == "move_target_not_found"


def test_a_run_whose_moves_left_messages_in_both_turns_its_rule_set_off(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home")
    rule_set_id = add_rule_set(user_client, work, enabled=False)
    add_filter(user_client, rule_set_id, "Move", to_account(home, "Shop"))
    user_client.patch(f"/api/v1/rule-sets/{rule_set_id}", json={"enabled": True})
    run_id = queued_run(user_client, rule_set_id)
    transfer = {
        "account": home,
        "folder": ["Shop"],
        "messages": [{"uid": 4, "result": "server_error", "removed": "failed", "copied": True}],
        "new_refs": [],
        "error": "server_error: x",
    }

    def moved(_self: object, report: Any, *_args: object, **_kwargs: object) -> None:
        report["filters"].append(
            {
                "spec": None,
                "action": "Move",
                "matched": [],
                "executed": False,
                "transfers": [transfer],
            }
        )

    monkeypatch.setattr(Folder, "run_into", moved)
    execute_run(db_session, run_id, mailboxes)
    assert run_out(user_client, rule_set_id, run_id)["error"] is None
    turned_off = user_client.get(f"/api/v1/rule-sets/{rule_set_id}").json()
    assert turned_off["enabled"] is False
    assert turned_off["disabled_reason"] == f"{IN_BOTH}: {IN_BOTH_TEXT}"


def test_a_copy_without_removal_says_why_its_rule_set_was_turned_off(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    run_id = queued_run(user_client, rule_set_id)

    def copied(_self: object, _report: Any, *_args: object, **_kwargs: object) -> None:
        raise CopiedNotRemovedError("server_error", Moved("Archive", None, {}))

    monkeypatch.setattr(Folder, "run_into", copied)
    execute_run(db_session, run_id, mailboxes)
    path = f"/api/v1/rule-sets/{rule_set_id}"
    reason = f"{COPIED_NOT_REMOVED}: {COPIED_NOT_REMOVED_TEXT}"
    assert user_client.get(path).json()["disabled_reason"] == reason
    assert user_client.patch(path, json={"enabled": True}).json()["disabled_reason"] is None


def test_a_run_leaves_the_ledgers_copies_alone(
    user_client: TestClient,
    user: User,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker gives the run its skip: the ledger's UIDs of the folder as selected."""
    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    ledger_copies(db_session, user.id, [format_ref(MessageRef(work, "INBOX", 7, 3))])
    skipped: list[set[int]] = []

    def run_into(_self: object, _report: Any, *_args: object, **kwargs: Any) -> None:
        skipped.append(kwargs["skip"]("INBOX", 7))

    monkeypatch.setattr(Folder, "run_into", run_into)
    execute_run(db_session, queued_run(user_client, rule_set_id), mailboxes)
    assert skipped == [{3}]


def test_run_targets_check_the_target_when_the_move_is_made(
    user_client: TestClient, user: User, mailboxes: Mailboxes
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home")
    connections = MagicMock()
    copied = MagicMock()
    store = user_store(mailboxes.engine, user.id)
    targets = RunTargets(store, mailboxes.subkey, connections, work, 1234, copied)
    target = targets(home)
    assert (target.connections, target.max_bytes, target.copied) == (connections, 1234, copied)
    for account in (work, home + 100):
        with pytest.raises(MailError) as caught:
            targets(account)
        assert caught.value.code == "invalid_argument"
    without_key = RunTargets(store, None, connections, work, 1234, copied)
    with pytest.raises(MailError) as caught:
        without_key(home)
    assert caught.value.code == "auth_failed"


def ledger(db: Session, user: User) -> list[tuple[int, str, int, int]]:
    rows = db.execute(
        select(
            TransferLedger.account_id,
            TransferLedger.folder,
            TransferLedger.uid_validity,
            TransferLedger.uid,
        )
        .where(TransferLedger.user_id == user.id)
        .order_by(TransferLedger.folder, TransferLedger.uid)
    )
    return [tuple(row) for row in rows]


def test_the_ledger_keeps_copies_per_folder_and_uidvalidity(
    db_session: Session, user: User
) -> None:
    refs = [
        format_ref(MessageRef(2, "inbox", 7, 3)),
        format_ref(MessageRef(2, "inbox", 7, 3)),
        format_ref(MessageRef(2, "Lists/Shop", 9, 5)),
        format_ref(MessageRef(2, "INBOX/Sub", 7, 6)),
        format_ref(MessageRef(2, "Lists/inbox", 9, 8)),
    ]
    ledger_copies(db_session, user.id, refs)
    assert ledger(db_session, user) == [
        (2, "INBOX", 7, 3),
        (2, "INBOX/Sub", 7, 6),
        (2, "Lists/Shop", 9, 5),
        (2, "Lists/inbox", 9, 8),
    ]
    assert ledger_skip(db_session, user.id, 2, "INBOX", 7) == {3}
    # A rule set may name the subfolder as stored, a leading INBOX in any case.
    assert ledger_skip(db_session, user.id, 2, "Inbox/Sub", 7) == {6}
    assert ledger_skip(db_session, user.id, 2, "Lists/INBOX", 9) == set()  # only a leading one
    assert ledger_skip(db_session, user.id, 3, "INBOX", 7) == set()
    # A new UIDVALIDITY: its UIDs name other messages, and the old entries go.
    assert ledger_skip(db_session, user.id, 2, "Lists/Shop", 10) == set()
    assert ledger(db_session, user) == [
        (2, "INBOX", 7, 3),
        (2, "INBOX/Sub", 7, 6),
        (2, "Lists/inbox", 9, 8),
    ]


def test_the_ledger_forgets_old_copies(db_session: Session, user: User) -> None:
    ledger_copies(db_session, user.id, [format_ref(MessageRef(2, "INBOX", 7, 3))])
    old = datetime.now(UTC) - timedelta(days=LEDGER_DAYS + 1)
    db_session.execute(update(TransferLedger).values(created_at=old))
    db_session.commit()
    ledger_copies(db_session, user.id, [format_ref(MessageRef(2, "INBOX", 7, 4))])
    assert ledger(db_session, user) == [(2, "INBOX", 7, 4)]


def test_a_ledger_that_cannot_be_written_stops_the_moves_and_keeps_the_session(
    db_session: Session,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    real = db_session.execute

    def refuse_insert(statement: Any, *args: Any, **kwargs: Any) -> Any:
        if getattr(statement, "is_insert", False):
            msg = "INSERT INTO app.transfer_ledger ... <secret@example.org>"
            raise SQLAlchemyError(msg)
        return real(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", refuse_insert)
    with pytest.raises(MailError) as stopped:
        ledger_copies(db_session, user.id, [format_ref(MessageRef(2, "INBOX", 7, 3))])
    assert stopped.value.code == "unsupported"
    monkeypatch.undo()
    assert ledger(db_session, user) == []  # the session is usable again
    [logged] = [r for r in caplog.records if r.getMessage() == "ledger_write_failed"]
    assert logged.__dict__["error"] == "SQLAlchemyError"
    assert "secret" not in caplog.text


def test_a_folder_rename_moves_its_ledger_entries(db_session: Session, user: User) -> None:
    refs = [
        format_ref(MessageRef(2, folder, 7, 1)) for folder in ("Old", "Old/Sub", "Older", "INBOX")
    ]
    ledger_copies(db_session, user.id, [*refs, format_ref(MessageRef(3, "Old", 7, 1))])
    rename_in_ledger(db_session, user.id, 2, "Old", "New/Place", "/")
    db_session.commit()
    assert ledger(db_session, user) == [
        (2, "INBOX", 7, 1),
        (2, "New/Place", 7, 1),
        (2, "New/Place/Sub", 7, 1),
        (3, "Old", 7, 1),
        (2, "Older", 7, 1),
    ]


def test_removing_an_account_forgets_its_ledger(
    user_client: TestClient, user: User, db_session: Session
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home")
    refs = [format_ref(MessageRef(account, "INBOX", 7, 1)) for account in (work, home)]
    ledger_copies(db_session, user.id, refs)
    assert user_client.delete(f"/api/v1/accounts/{home}").status_code == 204
    db_session.expire_all()
    assert ledger(db_session, user) == [(work, "INBOX", 7, 1)]
