"""Muted threads without a mailbox: the ids a thread keeps, what a mute rule set's run resolves
and adds, and the rule set kept out of every listing and edit. Muting on real mail:
``test_api_mail`` and ``test_filter_runner_mail``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from mailroom.filters.mute import MAX_IDS, MUTE_SPEC, NEWEST, THREAD, ThreadJoins, capped
from mailroom.filters.spec import loaded_filter, normalized_spec
from mailroom.models import Filter, MutedThread, RuleSet
from mailroom.worker.runs import execute_run
from mailroom.worker.tasks import queue_new_mail_runs
from mailroom_core.filters import Folder, Message
from mailroom_core.mail import parsed_message
from tests.test_filter_runner import add_account

if TYPE_CHECKING:
    import pytest
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.worker.context import Mailboxes


def message(own: str | None, *, cites: str = "", unread: bool = True, gmail: int | None = None):
    headers = "From: a@example.org\r\nSubject: hi\r\n"
    if own is not None:
        headers += f"Message-ID: {own}\r\n"
    if cites:
        headers += f"References: {cites}\r\n"
    flags = [] if unread else [b"\\Seen"]
    return Message(1, parsed_message(f"{headers}\r\nbody\r\n".encode()), flags, gmail)


def muted(db: Session, user: User, account_id: int, *ids: str, gm: int | None = None):
    row = MutedThread(user_id=user.id, account_id=account_id, message_ids=list(ids), gm_thrid=gm)
    db.add(row)
    db.flush()
    return row


def mute_rule_set(db: Session, user: User, account_id: int) -> RuleSet:
    rule_set = RuleSet(
        user_id=user.id,
        account_id=account_id,
        folder=["INBOX"],
        name="Muted threads",
        trigger="new_mail",
        enabled=True,
        kind="mute",
    )
    db.add(rule_set)
    db.flush()
    db.add(Filter(rule_set_id=rule_set.id, position=0, name="Muted threads", spec=MUTE_SPEC))
    db.commit()
    return rule_set


def test_a_thread_keeps_its_first_id_and_its_newest() -> None:
    ids = [f"<{i}@x>" for i in range(MAX_IDS + 50)]
    kept = capped([*ids, "<0@x>"])
    assert len(kept) == MAX_IDS
    assert kept[0] == "<0@x>"
    assert kept[1:] == ids[-(MAX_IDS - 1) :]
    assert capped(["<a@x>", "<a@x>", "<b@x>"]) == ["<a@x>", "<b@x>"]


def test_the_mute_spec_is_one_the_engine_keeps_as_it_is() -> None:
    assert normalized_spec(MUTE_SPEC) == MUTE_SPEC


def test_a_run_matches_unread_mail_of_the_muted_threads_and_adds_it(
    db_session: Session, user: User
) -> None:
    root = muted(db_session, user, 1, "<root@x>")
    gmail = muted(db_session, user, 1, "<g@x>", gm=77)
    muted(db_session, user, 2, "<other@x>")
    joins = ThreadJoins.of(db_session, user.id, 1)
    members = joins.members(THREAD)
    assert members is not None
    assert members.message_ids == {"<root@x>", "<g@x>"}
    assert members.gmail_threads == {77}
    assert joins.members("another") is None

    [ran] = joins.watched([loaded_filter(MUTE_SPEC, threads=joins.members)])
    matches = ran.criterion.func
    assert not matches(message("<r2@x>", cites="<r1@x>"))  # before r1
    assert matches(message("<r1@x>", cites="<root@x>"))
    assert matches(message("<g2@x>", gmail=77))
    assert matches(message("<r2@x>", cites="<r1@x>"))  # after r1, in the same run
    assert not matches(message("<r3@x>", cites="<root@x>", unread=False))
    assert not matches(message("<r4@x>", cites="<r1@x>", unread=False))
    assert not matches(message("<n@x>", cites="<other@x>"))  # another account's thread
    assert matches(message(None, cites="<root@x>"))  # marked read, but nothing to add

    joins.save(db_session)
    db_session.commit()
    assert root.message_ids == ["<root@x>", "<r1@x>", "<r2@x>"]
    assert gmail.message_ids == ["<g@x>", "<g2@x>"]
    assert ThreadJoins.of(db_session, user.id, 1).members(THREAD).message_ids >= {"<r1@x>"}


def test_a_mute_run_reads_the_newest_messages_only(
    user_client: TestClient,
    db_session: Session,
    user: User,
    mailboxes: Mailboxes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work = add_account(user_client)
    muted(db_session, user, work, "<root@x>")
    mute_rule_set(db_session, user, work)
    newest: list[int | None] = []

    def run_into(_self: object, _report: object, *_args: object, **kwargs: Any) -> None:
        newest.append(kwargs.get("newest"))

    monkeypatch.setattr(Folder, "run_into", run_into)
    [run_id] = queue_new_mail_runs(db_session, user.id, work, ["INBOX"])
    db_session.commit()
    execute_run(db_session, run_id, mailboxes)
    assert newest == [NEWEST]


def test_a_thread_unmuted_during_the_run_is_left_alone(db_session: Session, user: User) -> None:
    row = muted(db_session, user, 1, "<root@x>")
    joins = ThreadJoins.of(db_session, user.id, 1)
    [ran] = joins.watched([loaded_filter(MUTE_SPEC, threads=joins.members)])
    assert ran.criterion.func(message("<r1@x>", cites="<root@x>"))
    db_session.delete(row)
    db_session.flush()
    joins.save(db_session)
    assert db_session.scalars(select(MutedThread)).all() == []


def test_without_muted_threads_the_filter_matches_nothing(db_session: Session, user: User) -> None:
    joins = ThreadJoins.of(db_session, user.id, 1)
    assert joins.members(THREAD) is None
    [ran] = joins.watched([loaded_filter(MUTE_SPEC, threads=joins.members)])
    assert not ran.criterion.func(message("<r1@x>", cites="<root@x>"))


def test_the_mute_rule_set_is_hidden_but_runs_on_new_mail(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    hidden = mute_rule_set(db_session, user, work)
    [filter_id] = db_session.scalars(select(Filter.id).where(Filter.rule_set_id == hidden.id))

    assert user_client.get(f"/api/v1/accounts/{work}/rule-sets").json() == []
    for method, path in (
        ("GET", f"/api/v1/rule-sets/{hidden.id}"),
        ("PATCH", f"/api/v1/rule-sets/{hidden.id}"),
        ("DELETE", f"/api/v1/rule-sets/{hidden.id}"),
        ("POST", f"/api/v1/rule-sets/{hidden.id}/run"),
        ("POST", f"/api/v1/rule-sets/{hidden.id}/dry-run"),
        ("GET", f"/api/v1/rule-sets/{hidden.id}/runs"),
        ("PATCH", f"/api/v1/filters/{filter_id}"),
        ("DELETE", f"/api/v1/filters/{filter_id}"),
    ):
        body = {"name": "Mine"} if method == "PATCH" else None
        response = user_client.request(method, path, json=body)
        assert response.status_code == 404, (method, path)

    assert len(queue_new_mail_runs(db_session, user.id, work, ["INBOX"])) == 1


def test_muted_threads_go_with_their_account(
    user_client: TestClient, db_session: Session, user: User
) -> None:
    work = add_account(user_client)
    home = add_account(user_client, "home")
    muted(db_session, user, work, "<root@x>")
    muted(db_session, user, home, "<home@x>")
    mute_rule_set(db_session, user, work)

    assert user_client.delete(f"/api/v1/accounts/{work}").status_code == 204
    left = db_session.scalars(select(MutedThread.account_id)).all()
    assert left == [home]
    assert db_session.scalars(select(RuleSet).where(RuleSet.account_id == work)).all() == []
    assert user_client.get("/api/v1/mail/muted").json()["threads"][0]["account_id"] == home
