"""The worker's runs of rule sets: queueing (scheduled, new mail), the checks a run makes when it
starts, and how it records a failure. Runs that reach real mail: ``test_filter_runner_mail``."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select, text

from mailroom.auth.users import set_time_zone
from mailroom.mail.connections import mail_ssl_context
from mailroom.models import AuditRecord, FilterRun, MailIndexFolder, RuleSet
from mailroom.worker import tasks
from mailroom.worker.app import PROCRASTINATE_SCHEMA
from mailroom.worker.context import Mailboxes, WorkerContext
from mailroom.worker.idle import rule_set_folders
from mailroom.worker.runs import (
    COPIED_NOT_REMOVED,
    COPIED_NOT_REMOVED_TEXT,
    INTERRUPTED,
    MATCHED_KEPT,
    close_interrupted_runs,
    execute_run,
)
from mailroom.worker.tasks import (
    queue_new_mail_runs,
    queue_scheduled_runs,
    run_rule_set,
)
from mailroom_core.config import TimeZoneName
from mailroom_core.filters import Folder
from mailroom_core.mail import MESSAGES, CopiedNotRemovedError, MailError, Moved
from tests.test_api_accounts import added, new_account
from tests.test_api_filters import SHOP_SPEC, add_filter, jobs, rule_set

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import User


def add_account(client: TestClient, label: str = "work", *, run_filters: bool = True) -> int:
    account_id = added(client, new_account(label))
    set_run_filters(client, account_id, on=run_filters)
    return account_id


def set_run_filters(client: TestClient, account_id: int, *, on: bool) -> None:
    settings = f"/api/v1/accounts/{account_id}/filter-settings"
    assert client.put(settings, json={"run_filters": on}).status_code == 200


def add_rule_set(client: TestClient, account_id: int | str, **changes: Any) -> int:
    body = rule_set(**({"enabled": True} | changes))
    response = client.post(f"/api/v1/accounts/{account_id}/rule-sets", json=body)
    assert response.status_code == 201, response.json()
    identifier: int = response.json()["id"]
    return identifier


def runs(db: Session, rule_set_id: int) -> list[FilterRun]:
    return list(
        db.scalars(
            select(FilterRun).where(FilterRun.rule_set_id == rule_set_id).order_by(FilterRun.id)
        )
    )


def index_folder(
    db: Session, user: User, account_id: int, path: str, *, enabled: bool = True
) -> int:
    row = MailIndexFolder(user_id=user.id, account_id=account_id, path=path, enabled=enabled)
    db.add(row)
    db.commit()
    return row.id


def finish_jobs(db: Session) -> None:
    """As a worker would: procrastinate's triggers name its types unqualified."""
    path = db.execute(text("SELECT current_setting('search_path')")).scalar_one()
    db.execute(text(f"SET LOCAL search_path = {PROCRASTINATE_SCHEMA}, {path}"))
    db.execute(text("UPDATE procrastinate_jobs SET status = 'doing' WHERE status = 'todo'"))
    db.execute(text("UPDATE procrastinate_jobs SET status = 'succeeded' WHERE status = 'doing'"))
    db.execute(text(f"SET LOCAL search_path = {path}"))


EVERY_15 = {"trigger": "scheduled", "interval_minutes": 15}


def test_scheduled_rule_sets_are_queued_when_due(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    due = add_rule_set(user_client, work, **EVERY_15)
    now = datetime.now(UTC)

    [run_id] = queue_scheduled_runs(db_session, now)
    [run] = runs(db_session, due)
    assert (run.id, run.trigger, run.dry_run, run.started_at) == (run_id, "scheduled", False, None)
    assert jobs(db_session) == [
        {
            "task_name": "run_rule_set",
            "args": {"run_id": run_id},
            "lock": f"mailbox:{user.id}:{work}",
            "queueing_lock": f"rule_set:{due}:{work}",
            "status": "todo",
        }
    ]

    # Not again within the interval.
    assert queue_scheduled_runs(db_session, now + timedelta(minutes=10)) == []
    # Due again, but the last run still waits: one waiting run per rule set.
    assert queue_scheduled_runs(db_session, now + timedelta(minutes=15)) == []
    finish_jobs(db_session)
    # The dispatcher's minute began a little before the last run was queued.
    [again] = queue_scheduled_runs(db_session, now + timedelta(minutes=15) - timedelta(seconds=5))
    assert [run.id for run in runs(db_session, due)] == [run_id, again]


def test_only_runnable_scheduled_rule_sets_are_queued(
    user_client: TestClient, db_session: Session
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home", run_filters=False)
    add_rule_set(user_client, work, **EVERY_15, enabled=False)
    add_rule_set(user_client, home, **EVERY_15)
    add_rule_set(user_client, work, trigger="manual")
    add_rule_set(user_client, work, trigger="new_mail")

    assert queue_scheduled_runs(db_session, datetime.now(UTC)) == []
    assert jobs(db_session) == []


def test_new_mail_queues_the_folders_new_mail_rule_sets(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home", run_filters=False)
    inbox = add_rule_set(user_client, work, trigger="new_mail")
    add_rule_set(user_client, work, trigger="new_mail", folder=["Archive"])
    add_rule_set(user_client, work, trigger="new_mail", enabled=False)
    add_rule_set(user_client, work, trigger="scheduled", interval_minutes=5)
    add_rule_set(user_client, home, trigger="new_mail")

    [run_id] = queue_new_mail_runs(db_session, user.id, work, ["INBOX"])
    assert [(run.id, run.trigger) for run in runs(db_session, inbox)] == [(run_id, "new_mail")]
    # More new mail while that run waits: it will see that mail too.
    assert queue_new_mail_runs(db_session, user.id, work, ["INBOX"]) == []
    assert len(jobs(db_session)) == 1


def queued_runs(client: TestClient, rule_set_id: int) -> list[int]:
    response = client.post(f"/api/v1/rule-sets/{rule_set_id}/run")
    assert response.status_code == 202, response.json()
    return [run["id"] for run in response.json()]


def queued_run(client: TestClient, rule_set_id: int) -> int:
    response = client.post(f"/api/v1/rule-sets/{rule_set_id}/run")
    assert response.status_code == 202, response.json()
    identifier: int = response.json()["id"]
    return identifier


def run_out(client: TestClient, rule_set_id: int, run_id: int) -> dict[str, Any]:
    listed = client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    [run] = [run for run in listed if run["id"] == run_id]
    return run  # type: ignore[no-any-return]


def test_a_run_checks_its_rule_set_again_when_it_starts(
    user_client: TestClient, db_session: Session, mailboxes: Mailboxes
) -> None:
    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    run_id = queued_run(user_client, rule_set_id)
    set_run_filters(user_client, work, on=False)
    execute_run(db_session, run_id, mailboxes)
    run = run_out(user_client, rule_set_id, run_id)
    assert run["started_at"] is not None
    assert run["finished_at"] is not None
    assert run["result"] is None
    assert run["error"] == "run_filters_off: Run filters is off for this account."

    set_run_filters(user_client, work, on=True)
    finish_jobs(db_session)
    run_id = queued_run(user_client, rule_set_id)
    user_client.patch(f"/api/v1/rule-sets/{rule_set_id}", json={"enabled": False})
    execute_run(db_session, run_id, mailboxes)
    assert run_out(user_client, rule_set_id, run_id)["error"].startswith("rule_set_disabled: ")


def test_a_run_records_why_the_mailbox_failed(
    user_client: TestClient,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The account's IMAP port 9 on loopback is closed. The task runs through the worker's
    context."""
    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    run_id = queued_run(user_client, rule_set_id)

    monkeypatch.setattr(tasks, "worker_context", lambda: WorkerContext(sessions, mailboxes))
    run_rule_set(run_id)
    run = run_out(user_client, rule_set_id, run_id)
    assert run["error"] == f"unreachable: {MESSAGES['unreachable']}"
    assert run["finished_at"] is not None
    # The report so far: no filter had finished.
    assert (run["result"]["filters"], run["result"]["error"]) == ([], run["error"])


def test_a_run_without_the_secrets_key_records_it(
    user_client: TestClient, db_session: Session, migrated_engine: Engine
) -> None:
    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    run_id = queued_run(user_client, rule_set_id)
    execute_run(db_session, run_id, Mailboxes(migrated_engine, None, mail_ssl_context()))
    assert run_out(user_client, rule_set_id, run_id)["error"].startswith("secrets_key_missing: ")


def test_a_removed_run_is_left_alone(
    user_client: TestClient, db_session: Session, mailboxes: Mailboxes
) -> None:
    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    run_id = queued_run(user_client, rule_set_id)
    assert user_client.delete(f"/api/v1/rule-sets/{rule_set_id}").status_code == 204
    execute_run(db_session, run_id, mailboxes)
    assert db_session.get(FilterRun, run_id) is None


def test_a_reports_matches_are_bounded() -> None:
    from mailroom.worker.runs import _bounded

    matched = [{"uid": uid, "from": None, "subject": None, "date": None} for uid in range(250)]
    result = {"filters": [{"matched": matched}, {"matched": matched[:3]}]}
    bounded = _bounded(result)
    assert bounded["filters"][0]["matched"] == matched[:MATCHED_KEPT]
    assert bounded["filters"][0]["matched_total"] == 250
    assert bounded["filters"][1] == {"matched": matched[:3]}


def test_runs_queue_while_the_worker_has_procrastinate_open(
    user_client: TestClient, db_session: Session
) -> None:
    """The worker opens procrastinate asynchronously; queueing goes through the caller's
    (synchronous) connection all the same."""
    import asyncio
    import os

    from mailroom.worker.app import app, database_connector

    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work, trigger="new_mail")
    user = db_session.get_one(RuleSet, rule_set_id).user_id

    async def queue_with_the_app_open() -> list[int]:
        async with app.open_async():
            return await asyncio.to_thread(queue_new_mail_runs, db_session, user, work, ["INBOX"])

    with app.replace_connector(database_connector(os.environ["DATABASE_URL"])):
        [run_id] = asyncio.run(queue_with_the_app_open())
    assert [job["args"] for job in jobs(db_session)] == [{"run_id": run_id}]


def test_an_unexpected_failure_records_its_type_only(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mailroom.worker import runs as worker_runs

    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    run_id = queued_run(user_client, rule_set_id)

    def broken(*_args: object) -> None:
        msg = "Subject: a secret"
        raise RuntimeError(msg)

    monkeypatch.setattr(worker_runs, "_run", broken)
    execute_run(db_session, run_id, mailboxes)
    run = run_out(user_client, rule_set_id, run_id)
    assert (run["error"], run["result"]) == ("run_failed: RuntimeError", None)
    assert run["started_at"] is not None
    assert run["finished_at"] is not None
    assert "a secret" not in caplog.text


def test_a_run_failing_part_way_keeps_the_finished_filters_report(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    first = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    add_filter(user_client, rule_set_id, "Shop again", SHOP_SPEC)
    run_id = queued_run(user_client, rule_set_id)

    def first_then_fail(_self: object, report: Any, *_args: object, **_kwargs: object) -> None:
        report["filters"].append({"spec": None, "action": "Move", "matched": [], "executed": True})
        msg = "Subject: a secret"
        raise RuntimeError(msg)

    monkeypatch.setattr(Folder, "run_into", first_then_fail)
    execute_run(db_session, run_id, mailboxes)
    run = run_out(user_client, rule_set_id, run_id)
    assert run["error"] == "run_failed: RuntimeError"
    assert [entry["filter_id"] for entry in run["result"]["filters"]] == [first]
    assert run["result"]["error"] == run["error"]


def test_a_real_run_counts_each_filters_matches(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each run adds to the filter's count, which an edit keeps; the edit's date is shown beside
    it, which a reorder leaves."""
    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    shop = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    quiet = add_filter(user_client, rule_set_id, "Quiet", SHOP_SPEC)
    # Each filter's matches, in the rule set's order.
    matches = [[{"uid": uid} for uid in range(3)], []]

    def match(_self: object, report: Any, *_args: object, **_kwargs: object) -> None:
        for matched in matches:
            report["filters"].append({"spec": None, "action": "Move", "matched": matched})

    monkeypatch.setattr(Folder, "run_into", match)
    path = f"/api/v1/rule-sets/{rule_set_id}"

    def counts() -> dict[int, int]:
        filters = user_client.get(path).json()["filters"]
        return {entry["id"]: entry["matches_30d"] for entry in filters}

    assert counts() == {shop: 0, quiet: 0}
    execute_run(db_session, queued_run(user_client, rule_set_id), mailboxes)
    assert counts() == {shop: 3, quiet: 0}

    before = user_client.get(path).json()["filters"][0]["updated_at"]
    order = user_client.put(f"{path}/order", json={"filter_ids": [quiet, shop]})
    assert [entry["updated_at"] for entry in order.json()][1] == before
    edited = user_client.patch(f"/api/v1/filters/{shop}", json={"name": "Shop news"}).json()
    assert (edited["matches_30d"], edited["updated_at"] > before) == (3, True)
    unchanged = user_client.patch(f"/api/v1/filters/{shop}", json={"name": "Shop news"}).json()
    assert unchanged["updated_at"] == edited["updated_at"]

    # Past the report's bound, the total counts.
    matches = [[], [{"uid": uid} for uid in range(MATCHED_KEPT + 5)]]
    finish_jobs(db_session)
    execute_run(db_session, queued_run(user_client, rule_set_id), mailboxes)
    assert counts() == {shop: 3 + MATCHED_KEPT + 5, quiet: 0}


def test_a_failed_count_keeps_the_run_and_its_audit_records(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mailroom.worker import runs as worker_runs

    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)

    def match(_self: object, report: Any, *_args: object, **_kwargs: object) -> None:
        report["filters"].append({"spec": None, "action": "Move", "matched": [{"uid": 7}]})

    def broken(*_args: object) -> None:
        msg = "Subject: a secret"
        raise RuntimeError(msg)

    monkeypatch.setattr(Folder, "run_into", match)
    monkeypatch.setattr(worker_runs, "count_matches", broken)
    run_id = queued_run(user_client, rule_set_id)
    with caplog.at_level(logging.WARNING):
        execute_run(db_session, run_id, mailboxes)
    run = run_out(user_client, rule_set_id, run_id)
    assert run["error"] is None
    assert [len(entry["matched"]) for entry in run["result"]["filters"]] == [1]
    records = db_session.scalars(select(AuditRecord).where(AuditRecord.user_id == user.id))
    assert [(r.actor, r.run_id, r.detail["uid"]) for r in records] == [("filter", run_id, 7)]
    [logged] = [r for r in caplog.records if r.getMessage() == "match_count_failed"]
    assert logged.__dict__["error"] == "RuntimeError"
    assert "a secret" not in caplog.text


def test_a_run_counts_days_from_the_users_time_zone(
    user_client: TestClient,
    db_session: Session,
    user: User,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker runs the folder in the user's zone, which `older_than_days` counts from (core's
    `test_older_than_days_counts_from_the_runs_time_zone`)."""
    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    set_time_zone(db_session, user, TimeZoneName("Pacific/Auckland"))
    run_id = queued_run(user_client, rule_set_id)
    zones: list[object] = []

    def record(_self: object, _report: object, _filters: object, **kwargs: object) -> None:
        zones.append(kwargs["time_zone"])

    monkeypatch.setattr(Folder, "run_into", record)
    execute_run(db_session, run_id, mailboxes)
    assert zones == ["Pacific/Auckland"]


def test_a_move_that_copied_without_removing_turns_the_rule_set_off(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every new trigger would copy the originals again: the rule set stops until the user
    enables it, and the run's history says why."""
    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    run_id = queued_run(user_client, rule_set_id)

    def copied(_self: object, _report: Any, *_args: object, **_kwargs: object) -> None:
        raise CopiedNotRemovedError("server_error", Moved("Archive", None, {}))

    monkeypatch.setattr(Folder, "run_into", copied)
    caplog.set_level(logging.WARNING, logger="mailroom.worker.runs")
    execute_run(db_session, run_id, mailboxes)
    run = run_out(user_client, rule_set_id, run_id)
    assert run["error"] == f"{COPIED_NOT_REMOVED}: {COPIED_NOT_REMOVED_TEXT}"
    assert user_client.get(f"/api/v1/rule-sets/{rule_set_id}").json()["enabled"] is False
    turned_off = [r for r in caplog.records if r.name == "mailroom.worker.runs"]
    assert [(r.getMessage(), r.levelno) for r in turned_off] == [
        ("rule_set_turned_off", logging.WARNING)
    ]


def changing(*folders: tuple[int, str], error: Exception | None = None) -> Callable[..., None]:
    """A ``Folder.run_into`` that changed ``folders``, then raises ``error``."""

    def run_into(
        _self: object, _report: Any, *_args: object, changed: set[tuple[int, str]], **_kw: object
    ) -> None:
        changed.update(folders)
        if error is not None:
            raise error

    return run_into


def synced_folders(db: Session) -> set[int]:
    """The index folders with a sync waiting."""
    query = text(
        f"SELECT (args->>'folder_id')::bigint FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs"
        " WHERE task_name = :name AND status = 'todo'"
    )
    return set(db.scalars(query, {"name": tasks.SYNC_FOLDER}))


@pytest.mark.parametrize("error", [None, MailError("server_error"), RuntimeError("x")], ids=repr)
def test_a_run_syncs_the_folders_it_changed_even_one_that_failed(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception | None,
) -> None:
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)
    work = add_account(user_client)
    home = add_account(user_client, "home")
    source, archive = (index_folder(db_session, user, work, p) for p in ("INBOX", "Archive"))
    copies = index_folder(db_session, user, home, "Shop")
    index_folder(db_session, user, work, "Other")
    index_folder(db_session, user, home, "Off", enabled=False)
    rule_set_id = add_rule_set(user_client, work)
    run_id = queued_run(user_client, rule_set_id)
    run_into = changing(
        (work, "inbox"), (work, "Archive"), (home, "Shop"), (home, "Off"), error=error
    )
    monkeypatch.setattr(Folder, "run_into", run_into)
    execute_run(db_session, run_id, mailboxes)
    assert synced_folders(db_session) == {source, archive, copies}
    assert (run_out(user_client, rule_set_id, run_id)["error"] is None) == (error is None)


def test_a_run_that_changed_nothing_syncs_nothing(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)
    work = add_account(user_client)
    index_folder(db_session, user, work, "INBOX")
    run_id = queued_run(user_client, add_rule_set(user_client, work))
    monkeypatch.setattr(Folder, "run_into", changing())
    execute_run(db_session, run_id, mailboxes)
    assert synced_folders(db_session) == set()


def test_a_run_without_the_mail_client_syncs_nothing(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAILROOM_FEATURES", "filters")
    work = add_account(user_client)
    index_folder(db_session, user, work, "INBOX")
    run_id = queued_run(user_client, add_rule_set(user_client, work))
    monkeypatch.setattr(Folder, "run_into", changing((work, "INBOX")))
    execute_run(db_session, run_id, mailboxes)
    assert synced_folders(db_session) == set()


def test_a_run_removed_meanwhile_still_syncs_what_it_changed(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)
    work = add_account(user_client)
    inbox = index_folder(db_session, user, work, "INBOX")
    rule_set_id = add_rule_set(user_client, work)
    run_id = queued_run(user_client, rule_set_id)
    note = changing((work, "INBOX"))

    def run_into(*args: Any, **kwargs: Any) -> None:
        note(*args, **kwargs)
        user_client.delete(f"/api/v1/rule-sets/{rule_set_id}")

    monkeypatch.setattr(Folder, "run_into", run_into)
    execute_run(db_session, run_id, mailboxes)
    assert synced_folders(db_session) == {inbox}


def test_an_index_that_cannot_be_written_leaves_the_run_as_it_went(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)
    work = add_account(user_client)
    index_folder(db_session, user, work, "INBOX")
    rule_set_id = add_rule_set(user_client, work)
    run_id = queued_run(user_client, rule_set_id)
    monkeypatch.setattr(Folder, "run_into", changing((work, "INBOX")))

    def broken(*_args: object) -> None:
        msg = "Subject: a secret"
        raise RuntimeError(msg)

    monkeypatch.setattr(tasks, "queue_folder_sync", broken)
    execute_run(db_session, run_id, mailboxes)
    run = run_out(user_client, rule_set_id, run_id)
    assert (run["error"], run["finished_at"] is not None) == (None, True)
    assert "mail_index_write_failed" in caplog.text
    assert "a secret" not in caplog.text


def set_job_status(db: Session, run_id: int, status: str) -> None:
    path = db.execute(text("SELECT current_setting('search_path')")).scalar_one()
    db.execute(text(f"SET LOCAL search_path = {PROCRASTINATE_SCHEMA}, {path}"))
    db.execute(
        text("UPDATE procrastinate_jobs SET status = 'doing' WHERE (args->>'run_id')::int = :id"),
        {"id": run_id},
    )
    if status != "doing":
        db.execute(
            text(
                "UPDATE procrastinate_jobs SET status = :status WHERE (args->>'run_id')::int = :id"
            ),
            {"id": run_id, "status": status},
        )
    db.execute(text(f"SET LOCAL search_path = {path}"))


def test_runs_a_stopped_worker_left_are_closed(
    user_client: TestClient, db_session: Session
) -> None:
    work = add_account(user_client)
    waiting = queued_run(user_client, add_rule_set(user_client, work))
    running = queued_run(user_client, add_rule_set(user_client, work))
    never_started = queued_run(user_client, add_rule_set(user_client, work))
    still_running = queued_run(user_client, add_rule_set(user_client, work))
    dry = FilterRun(
        rule_set_id=add_rule_set(user_client, work), account_id=work, trigger="manual", dry_run=True
    )
    db_session.add(dry)
    started = datetime.now(UTC) - timedelta(minutes=5)
    db_session.get_one(FilterRun, running).started_at = started
    db_session.flush()
    # At the next start, fail_interrupted_jobs has failed the jobs the stopped worker held.
    set_job_status(db_session, running, "failed")
    set_job_status(db_session, never_started, "failed")
    set_job_status(db_session, still_running, "doing")

    assert sorted(close_interrupted_runs(db_session)) == sorted([running, never_started])
    for run_id in (running, never_started):
        run = db_session.get_one(FilterRun, run_id)
        db_session.refresh(run)
        assert run.error == INTERRUPTED
        assert run.finished_at is not None
        assert run.started_at is not None
    assert db_session.get_one(FilterRun, running).started_at == started
    for run_id in (waiting, still_running, dry.id):
        run = db_session.get_one(FilterRun, run_id)
        db_session.refresh(run)
        assert run.finished_at is None


def test_a_pattern_out_of_time_fails_the_run(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Not reported as the mailbox's timeout (``MatchTimeoutError`` is a ``TimeoutError``), and
    the pattern its text quotes stays out of the record and the log."""
    from mailroom.filters.runs import MATCH_TIMEOUT_MESSAGE
    from mailroom_core.filters import MatchTimeoutError

    work = add_account(user_client)
    rule_set_id = add_rule_set(user_client, work)
    first = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    add_filter(user_client, rule_set_id, "Backtracks", SHOP_SPEC)
    run_id = queued_run(user_client, rule_set_id)

    def first_then_slow(_self: object, report: Any, *_args: object, **_kwargs: object) -> None:
        report["filters"].append({"spec": None, "action": "Move", "matched": [], "executed": True})
        msg = "regular expression '(secret|secrets)+$' timed out after 1.0s"
        raise MatchTimeoutError(msg)

    monkeypatch.setattr(Folder, "run_into", first_then_slow)
    execute_run(db_session, run_id, mailboxes)
    run = run_out(user_client, rule_set_id, run_id)
    assert run["error"] == f"match_timeout: {MATCH_TIMEOUT_MESSAGE}"
    assert [entry["filter_id"] for entry in run["result"]["filters"]] == [first]
    assert run["result"]["error"] == run["error"]
    assert "secret" not in caplog.text
    assert "MatchTimeoutError" in caplog.text


def test_a_rule_set_for_all_accounts_is_queued_on_each_running_account(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home")
    add_account(user_client, "off", run_filters=False)
    scheduled = add_rule_set(user_client, "all", **EVERY_15)
    new_mail = add_rule_set(user_client, "all", trigger="new_mail")
    now = datetime.now(UTC)

    assert len(queue_scheduled_runs(db_session, now)) == 2
    assert [run.account_id for run in runs(db_session, scheduled)] == [work, home]
    # Each account keeps its own schedule and its own waiting run.
    assert queue_scheduled_runs(db_session, now + timedelta(minutes=10)) == []
    assert queue_scheduled_runs(db_session, now + timedelta(minutes=15)) == []
    assert sorted(job["queueing_lock"] for job in jobs(db_session)) == sorted(
        [f"rule_set:{scheduled}:{work}", f"rule_set:{scheduled}:{home}"]
    )

    [run_id] = queue_new_mail_runs(db_session, user.id, home, ["INBOX"])
    assert [(run.id, run.account_id) for run in runs(db_session, new_mail)] == [(run_id, home)]
    assert queue_new_mail_runs(db_session, user.id, home, ["Archive"]) == []
    watched = {folder for folder in rule_set_folders(db_session) if folder[0] == user.id}
    assert watched == {(user.id, work, ("INBOX",)), (user.id, home, ("INBOX",))}


def test_a_rule_set_for_all_accounts_failing_on_one_account_runs_on_the_others(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
    user: User,
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home")
    rule_set_id = add_rule_set(user_client, "all")
    shop = add_filter(user_client, rule_set_id, "Shop", SHOP_SPEC)
    queued = queued_runs(user_client, rule_set_id)
    assert len(queued) == 2

    def run_into(self: Folder, report: Any, *_args: object, **_kwargs: object) -> None:
        if self.account.account_id == work:
            raise MailError("mailbox_missing")
        report["filters"].append({"spec": None, "action": "Move", "matched": [], "executed": True})

    monkeypatch.setattr(Folder, "run_into", run_into)
    for run_id in queued:
        execute_run(db_session, run_id, mailboxes)

    listed = user_client.get(f"/api/v1/rule-sets/{rule_set_id}/runs").json()
    by_account = {run["account_id"]: run for run in listed}
    assert by_account[work]["error"] == f"mailbox_missing: {MESSAGES['mailbox_missing']}"
    assert by_account[home]["error"] is None
    assert [entry["filter_id"] for entry in by_account[home]["result"]["filters"]] == [shop]
    # The refusal is in the audit trail, on its account.
    [failed] = db_session.scalars(
        select(AuditRecord).where(
            AuditRecord.user_id == user.id, AuditRecord.action == "run_failed"
        )
    )
    assert (failed.account_id, failed.rule_set_id) == (work, rule_set_id)
