"""What a connected app limited to some accounts, or to a send ceiling, can do over ``/mcp``: every
tool is tried on the account it does not have, on its messages, and on what spans it. The app is a
fake token carrying the claims the built-in server's does. The mail cases run against the Dovecot
``IMAP_TEST_HOST`` names (skipped without it)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from mailroom.accounts import app_data
from mailroom.accounts import service as accounts_service
from mailroom.api.main import create_application
from mailroom.config.features import ALL_FEATURES
from mailroom.context import app_context
from mailroom.errors import Refusal
from mailroom.filters import service as filters_service
from mailroom.filters.folders import Rewritten, RuleSetRef, named_to
from mailroom.mail import service as mail_service
from mailroom.mail.service import folder_writes, send
from mailroom.mcp.groups import selected_groups
from mailroom.mcp.settings import McpSettings
from mailroom.mcp.tools import TOOLS
from mailroom.models import AuditRecord, Filter, MutedThread, RuleSet, ScheduledSend, Snooze
from mailroom.reach import WHOLE, Reach
from mailroom.worker.sends import send_scheduled
from mailroom_core.mail import MESSAGES, MessageRef, format_ref
from mailroom_core.testing import create_folders, mime
from tests.support.mail import mail_servers_ssl_context, settle_sends
from tests.test_api_accounts import MAILBOX_PASSWORD, new_account
from tests.test_api_filters import SHOP_SPEC
from tests.test_mcp import (
    PUBLIC_URL,
    FakeVerifier,
    MailAccounts,
    Mcp,
    Writer,
    cursor_in,
    draft_ref,
    inbox_ref,
    refs_in,
    set_mode,
    smtp_of,
    stranger,
)
from tests.test_scheduled_sends import agent_origin

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.context import AppContext
    from mailroom.models import User
    from mailroom.worker.context import Mailboxes
    from mailroom_core.testing import ImapTestServer

pytestmark = pytest.mark.usefixtures("numbered_accounts")

ALL_GROUPS = ",".join(
    sorted({group for tool in TOOLS for group in tool.groups}),
)
HOME = format_ref(MessageRef(2, "INBOX", 1, 1))
"""A ref of a message in the account the narrowed app does not have."""


@pytest.fixture(scope="module")
def _limits_application(
    _worker_engine: Engine,
) -> Iterator[tuple[TestClient, FakeVerifier, FastAPI]]:
    verifier = FakeVerifier()
    settings = McpSettings(
        public_url=PUBLIC_URL, auth="builtin", groups=selected_groups(ALL_GROUPS)
    )
    application = create_application(
        features=ALL_FEATURES, mcp_settings=settings, mcp_verifier=verifier
    )
    with TestClient(application, base_url=PUBLIC_URL) as client:
        yield client, verifier, application


@pytest.fixture
def mcp(
    _limits_application: tuple[TestClient, FakeVerifier, FastAPI],
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Mcp]:
    client, verifier, application = _limits_application
    verifier.tokens.clear()
    monkeypatch.setattr(application.state, "session_maker", sessions)
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    yield Mcp(client, verifier, application)
    try:
        settle_sends(application.state.send_runner)
    finally:
        pool = application.state.mail_pool
        pool.close_idle(time.monotonic() + pool.idle_seconds)


@pytest.fixture
def ctx(mcp: Mcp) -> AppContext:
    context = app_context(mcp.app.state)
    assert context is not None
    return context


def limited(
    mcp: Mcp, user: User, accounts: list[int] | None = None, ceiling: str | None = None
) -> str:
    """The token of an app with these limits, for every tool group."""
    token = mcp.verifier.issue(user.id)
    mcp.verifier.tokens[token].claims = {
        "grant_id": "7",
        "tool_groups": ALL_GROUPS.split(","),
        "account_ids": accounts,
        "send_ceiling": ceiling,
    }
    return token


@pytest.fixture
def whole(mcp: Mcp, user: User) -> str:
    return limited(mcp, user)


@pytest.fixture
def work_only(mcp: Mcp, user: User) -> str:
    return limited(mcp, user, [1])


@pytest.fixture
def two_accounts(numbered_accounts: None, ctx: AppContext, user: User) -> None:
    """Accounts 1 (work) and 2 (home), whose servers nothing reaches."""
    for label in ("work", "home"):
        fields = {k: v for k, v in new_account(label).items() if k != "password"}
        accounts_service.add_account(ctx, user.id, fields, MAILBOX_PASSWORD)


def rule_set(db: Session, user: User, account_id: int | None, *specs: dict[str, Any]) -> int:
    row = RuleSet(
        user_id=user.id, account_id=account_id, folder=["INBOX"], name="rules", trigger="manual"
    )
    db.add(row)
    db.flush()
    for position, spec in enumerate(specs):
        db.add(Filter(rule_set_id=row.id, position=position, name="f", spec=spec))
    db.commit()
    return row.id


def moving_to(account: int) -> dict[str, Any]:
    action = {"type": "move_to_account", "args": {"account": account, "folder": ["INBOX"]}}
    return {"criterion": {"type": "is_unread"}, "action": action}


@pytest.mark.usefixtures("two_accounts")
def test_accounts_list_has_only_its_accounts_and_the_send_mode_it_gets(
    mcp: Mcp, ctx: AppContext, user: User, whole: str, work_only: str
) -> None:
    set_mode(ctx, user, "direct")

    def listed(token: str) -> list[tuple[int, str]]:
        accounts = mcp.call(token, "accounts_list")["accounts"]
        return [(account["id"], account["send_mode"]) for account in accounts]

    assert listed(whole) == [(1, "direct"), (2, "drafts")]
    assert listed(work_only) == [(1, "direct")]
    assert listed(limited(mcp, user, [1], "approval")) == [(1, "approval")]
    assert listed(limited(mcp, user, None, "direct")) == [(1, "direct"), (2, "drafts")]
    assert listed(limited(mcp, user, [])) == []
    # Another user's account, or nobody's, adds nothing.
    assert listed(limited(mcp, user, [2, 99])) == [(2, "drafts")]


BY_ACCOUNT: list[tuple[str, dict[str, Any]]] = [
    ("folders_list", {}),
    ("account_run_filters", {"on": True}),
    ("rule_sets_list", {}),
    ("rule_set_create", {"name": "x", "folder": ["INBOX"], "trigger": "manual"}),
    ("messages_list", {"folder": "INBOX"}),
    ("messages_search", {"subject": "x"}),
    ("folder_create", {"parent": None, "name": "x"}),
    ("folder_rename", {"folder": "a", "parent": None, "name": "x"}),
    ("folder_delete", {"folder": "a", "messages": 0}),
    ("draft_create", {"subject": "s", "text": "t"}),
    ("draft_import", {"html": "<p>x</p>", "subject": "s"}),
    ("message_send", {"to": ["a@example.org"], "subject": "s", "text": "t"}),
]


@pytest.mark.usefixtures("two_accounts")
@pytest.mark.parametrize(("tool", "arguments"), BY_ACCOUNT, ids=[tool for tool, _ in BY_ACCOUNT])
def test_a_tool_on_an_account_beyond_is_refused_as_on_no_account(
    mcp: Mcp, work_only: str, tool: str, arguments: dict[str, Any]
) -> None:
    beyond = mcp.error(work_only, tool, account_id=2, **arguments)
    assert beyond == mcp.error(work_only, tool, account_id=99, **arguments) == "No such account."


SOON = (datetime.now(UTC) + timedelta(days=2)).isoformat()
NO_MESSAGE = MESSAGES["not_found"]
BY_REF: list[tuple[str, dict[str, Any]]] = [
    ("message_read", {"ref": HOME}),
    ("thread_read", {"ref": HOME}),
    ("attachment_read", {"ref": HOME, "part": "2"}),
    ("messages_move", {"refs": [HOME], "folder": "INBOX"}),
    ("messages_trash", {"refs": [HOME]}),
    ("messages_flag", {"refs": [HOME], "seen": True}),
    ("messages_snooze", {"ref": HOME, "until": SOON}),
    ("messages_move_to_account", {"refs": [HOME], "account_id": 1, "folder": "INBOX"}),
    ("messages_move_to_account_for_good", {"refs": [HOME], "account_id": 1, "folder": "INBOX"}),
    ("thread_mute", {"ref": HOME}),
    ("draft_create", {"reply_to": HOME, "text": "t"}),
    (
        "draft_create",
        {"account_id": 1, "subject": "s", "text": "t", "attachments": [{"ref": HOME, "part": "2"}]},
    ),
    ("draft_update", {"ref": HOME, "text": "t"}),
    ("draft_delete", {"ref": HOME}),
    ("draft_send", {"ref": HOME}),
    ("draft_send", {"ref": HOME, "send_at": SOON}),
    (
        "message_send",
        {
            "account_id": 1,
            "to": ["a@example.org"],
            "subject": "s",
            "text": "t",
            "attachments": [{"ref": HOME, "part": "2"}],
        },
    ),
    ("message_reply", {"ref": HOME, "text": "t"}),
    ("message_forward", {"ref": HOME, "to": ["a@example.org"]}),
]


@pytest.mark.usefixtures("two_accounts")
@pytest.mark.parametrize(("tool", "arguments"), BY_REF, ids=[tool for tool, _ in BY_REF])
def test_a_message_of_an_account_beyond_is_not_there(
    mcp: Mcp, db_session: Session, user: User, work_only: str, tool: str, arguments: dict[str, Any]
) -> None:
    assert mcp.error(work_only, tool, **arguments) == NO_MESSAGE
    assert not db_session.scalars(
        select(AuditRecord).where(AuditRecord.user_id == user.id, AuditRecord.account_id == 2)
    ).all()


def test_every_tool_naming_an_account_or_a_message_is_tried_above() -> None:
    """A new tool joins ``BY_ACCOUNT`` or ``BY_REF``, or is one of these, tried on their own."""
    tried = {tool for tool, _ in (*BY_ACCOUNT, *BY_REF)}
    elsewhere = {
        "accounts_list",
        "audit_list",
        "muted_threads_list",
        "thread_unmute",
        "filter_schema",
        *BY_RULE_SET,
        "filters_reorder",
        "filter_add",
        "filter_update",
        "filter_delete",
    }
    assert {tool.name for tool in TOOLS} == tried | elsewhere


@pytest.mark.usefixtures("two_accounts")
def test_mail_is_not_moved_to_an_account_beyond(mcp: Mcp, work_only: str) -> None:
    ref = format_ref(MessageRef(1, "INBOX", 1, 1))
    for tool in ("messages_move_to_account", "messages_move_to_account_for_good"):
        error = mcp.error(work_only, tool, refs=[ref], account_id=2, folder="INBOX")
        assert error == "No such account."


BY_RULE_SET = (
    "rule_set_get",
    "runs_list",
    "rule_set_update",
    "rule_set_disable",
    "rule_set_delete",
    "dry_run",
    "rule_set_enable",
    "run_now",
)


@pytest.mark.usefixtures("two_accounts")
@pytest.mark.parametrize("where", ["account", "all", "moves_there"])
def test_a_rule_set_beyond_is_not_found(
    mcp: Mcp, db_session: Session, user: User, whole: str, work_only: str, where: str
) -> None:
    if where == "account":
        rules = rule_set(db_session, user, 2, SHOP_SPEC)
    elif where == "all":
        rules = rule_set(db_session, user, None, SHOP_SPEC)
    else:
        rules = rule_set(db_session, user, 1, SHOP_SPEC, moving_to(2))
    first = mcp.call(whole, "rule_set_get", rule_set_id=rules)["filters"][0]["id"]

    for tool in BY_RULE_SET:
        assert mcp.error(work_only, tool, rule_set_id=rules) == "No such rule set", tool
    add = mcp.error(work_only, "filter_add", rule_set_id=rules, name="x", spec=SHOP_SPEC)
    assert add == "No such rule set"
    reorder = mcp.error(work_only, "filters_reorder", rule_set_id=rules, filter_ids=[first])
    assert reorder == "No such rule set"
    for tool in ("filter_update", "filter_delete"):
        assert mcp.error(work_only, tool, filter_id=first) in (
            "No such filter",
            "No such rule set",
        ), tool
    if where == "moves_there":
        assert mcp.call(work_only, "rule_sets_list", account_id=1) == {"rule_sets": []}
        assert len(mcp.call(whole, "rule_sets_list", account_id=1)["rule_sets"]) == 1


@pytest.mark.usefixtures("two_accounts")
def test_rule_sets_for_all_accounts_are_refused(mcp: Mcp, user: User, work_only: str) -> None:
    assert "beyond this app" in mcp.error(work_only, "rule_sets_list", account_id="all")
    created = mcp.error(
        work_only,
        "rule_set_create",
        account_id="all",
        name="x",
        folder=["INBOX"],
        trigger="new_mail",
    )
    assert "beyond this app" in created
    # Both accounts chosen is still not all of them: one added later is not its own.
    everything = limited(mcp, user, [1, 2])
    assert "beyond this app" in mcp.error(everything, "rule_sets_list", account_id="all")


@pytest.mark.usefixtures("two_accounts")
def test_a_filter_cannot_be_made_to_move_mail_beyond(
    mcp: Mcp, db_session: Session, user: User, whole: str, work_only: str
) -> None:
    rules = rule_set(db_session, user, 1, SHOP_SPEC)
    first = mcp.call(work_only, "rule_set_get", rule_set_id=rules)["filters"][0]["id"]
    nested = {
        "criterion": {"type": "is_unread"},
        "action": {
            "seq": [
                {"type": "mark_as_read"},
                moving_to(2)["action"],
            ]
        },
    }
    for spec in (moving_to(2), nested):
        add = mcp.error(work_only, "filter_add", rule_set_id=rules, name="x", spec=spec)
        assert "only to another of your accounts" in add
        change = mcp.error(work_only, "filter_update", filter_id=first, spec=spec)
        assert "only to another of your accounts" in change
    mcp.call(whole, "filter_add", rule_set_id=rules, name="x", spec=moving_to(2))


@pytest.mark.usefixtures("two_accounts")
@pytest.mark.parametrize("where", [1, None])
def test_run_filters_is_not_turned_on_while_a_rule_set_would_move_mail_beyond(
    mcp: Mcp, db_session: Session, user: User, whole: str, work_only: str, where: int | None
) -> None:
    rules = rule_set(db_session, user, where, SHOP_SPEC, moving_to(2))
    row = db_session.get_one(RuleSet, rules)
    row.enabled = False
    db_session.commit()
    # Disabled it would not run, and only the user can enable it.
    assert mcp.call(work_only, "account_run_filters", account_id=1, on=True)["run_filters"]
    mcp.call(work_only, "account_run_filters", account_id=1, on=False)
    row.enabled = True
    db_session.commit()
    refused = mcp.error(work_only, "account_run_filters", account_id=1, on=True)
    assert "beyond this app" in refused
    assert not mcp.call(work_only, "accounts_list")["accounts"][0]["run_filters"]
    mcp.call(work_only, "account_run_filters", account_id=1, on=False)
    assert mcp.call(whole, "account_run_filters", account_id=1, on=True)["run_filters"]


@pytest.mark.usefixtures("two_accounts")
def test_muted_threads_beyond_are_neither_listed_nor_unmuted(
    mcp: Mcp, db_session: Session, user: User, whole: str, work_only: str
) -> None:
    rows = [
        MutedThread(user_id=user.id, account_id=account, message_ids=[f"<{account}@x>"])
        for account in (1, 2)
    ]
    db_session.add_all(rows)
    db_session.flush()
    mine, beyond = (row.id for row in rows)

    def ids(token: str, **arguments: Any) -> list[int]:
        threads = mcp.call(token, "muted_threads_list", **arguments)["threads"]
        return [thread["id"] for thread in threads]

    assert ids(whole) == [mine, beyond]
    assert ids(work_only) == [mine]
    assert ids(work_only, account_id=2) == []
    assert mcp.error(work_only, "thread_unmute", id=beyond) == "No such muted thread"
    assert ids(whole) == [mine, beyond]
    mcp.call(work_only, "thread_unmute", id=mine)


@pytest.mark.usefixtures("two_accounts")
def test_the_audit_trail_leaves_out_what_is_beyond(
    mcp: Mcp, db_session: Session, user: User, whole: str, work_only: str
) -> None:
    everywhere = rule_set(db_session, user, None)
    mine = rule_set(db_session, user, 1, SHOP_SPEC)
    leaving = rule_set(db_session, user, 1, SHOP_SPEC, moving_to(2))

    def add(account: int, uid: int, rules: int | None = None, **detail: Any) -> None:
        db_session.add(
            AuditRecord(
                user_id=user.id,
                actor="user" if rules is None else "filter",
                rule_set_id=rules,
                run_id=None if rules is None else 5,
                action="move",
                account_id=account,
                result="ok",
                detail={"uid": uid, **detail},
            )
        )
        db_session.flush()

    add(1, 1, rule_sets=[everywhere], filters=[3], all_accounts_rule_sets=[everywhere])
    add(2, 2)
    add(1, 3, destination_account=2, destination="Home folder")
    add(1, 4, source_account=2, source_folder="Home folder")
    add(1, 5, destination_account=1)
    add(1, 6, rules=everywhere, filter="Secret", filter_id=3)
    add(1, 7, rules=leaving, filter="Secret", filter_id=4)
    add(1, 8, rules=mine, filter="Shop", filter_id=5)

    def listed(token: str, **arguments: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = mcp.call(token, "audit_list", **arguments)["records"]
        return records

    assert [r["detail"]["uid"] for r in listed(whole)] == [8, 7, 6, 5, 4, 3, 2, 1]
    [within, leaves, filtered, moved, renamed] = listed(work_only)
    assert within["detail"] == {"uid": 8, "filter": "Shop", "filter_id": 5}
    assert (within["rule_set"], within["rule_set_id"], within["run_id"]) == ("rules", mine, 5)
    for record, uid in ((leaves, 7), (filtered, 6)):
        assert (record["detail"], record["rule_set"]) == ({"uid": uid}, None)
        assert (record["rule_set_id"], record["run_id"]) == (None, None)
    assert moved["detail"]["uid"] == 5
    assert renamed["detail"] == {"uid": 1}
    assert listed(work_only, account_id=2) == []
    assert listed(limited(mcp, user, [])) == []


@pytest.mark.usefixtures("two_accounts")
def test_held_sends_and_snoozes_beyond_are_not_found(
    ctx: AppContext, db_session: Session, user: User
) -> None:
    later = datetime.now(UTC) + timedelta(hours=1)
    held = ScheduledSend(
        user_id=user.id,
        account_id=2,
        ref=HOME,
        send_key="2/x",
        subject="s",
        recipients=[],
        via="web",
        send_at=later,
        state="held",
    )
    snoozed = Snooze(
        user_id=user.id,
        account_id=2,
        via="web",
        message_id="<x@x>",
        size=1,
        folder="INBOX",
        snoozed="Snoozed",
        subject="s",
        sender="x",
        until=later,
    )
    db_session.add_all([held, snoozed])
    db_session.flush()
    narrow = ctx.within(Reach(frozenset({1})))

    assert len(mail_service.list_scheduled(ctx, user.id)) == 1
    assert mail_service.list_scheduled(narrow, user.id) == []
    assert mail_service.scheduled_for(narrow, user.id, HOME) is None
    assert len(mail_service.list_snoozes(ctx, user.id)) == 1
    assert mail_service.list_snoozes(narrow, user.id) == []
    for call in (
        lambda: mail_service.read_scheduled(narrow, user.id, str(held.id)),
        lambda: mail_service.cancel_scheduled(narrow, user.id, str(held.id)),
        lambda: mail_service.wake_now(narrow, user.id, str(snoozed.id)),
    ):
        with pytest.raises(Refusal) as caught:
            call()
        assert caught.value.code == "not_found"


@pytest.mark.usefixtures("two_accounts")
def test_sends_beyond_are_not_found(
    ctx: AppContext, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    narrow = ctx.within(Reach(frozenset({1})))
    claim = ctx.store(user.id).claim_send("2/key", 2, 10)
    assert mail_service.read_send(ctx, user.id, str(claim.send.id)).send_id == str(claim.send.id)
    with pytest.raises(Refusal):
        mail_service.read_send(narrow, user.id, str(claim.send.id))
    monkeypatch.setattr(send, "_holding_send", lambda *_: claim.send)
    assert mail_service.send_holds(ctx, user.id, HOME)
    assert not mail_service.send_holds(narrow, user.id, HOME)


@pytest.mark.usefixtures("two_accounts")
def test_a_rename_tells_only_of_the_rule_sets_within(
    ctx: AppContext, db_session: Session, user: User
) -> None:
    mine, beyond, leaving = (
        rule_set(db_session, user, 1, SHOP_SPEC),
        rule_set(db_session, user, 2, SHOP_SPEC),
        rule_set(db_session, user, 1, moving_to(2)),
    )
    filters = {
        row.rule_set_id: row.id
        for row in db_session.scalars(
            select(Filter).where(Filter.rule_set_id.in_([mine, beyond, leaving]))
        )
    }
    everywhere = rule_set(db_session, user, None)
    rewritten = Rewritten([mine, beyond, leaving], sorted(filters.values()), [everywhere])
    told = folder_writes._told(ctx.within(Reach(frozenset({1}))), rewritten)  # noqa: SLF001
    assert told == Rewritten([mine], [filters[mine]], [])
    assert folder_writes._told(ctx, rewritten) == rewritten  # noqa: SLF001


@pytest.mark.usefixtures("two_accounts")
def test_a_folder_in_use_names_only_the_rule_sets_within(db_session: Session, user: User) -> None:
    mine, *others = [
        RuleSetRef(rule_set(db_session, user, account, spec), "rules", account)
        for account, spec in (
            (1, SHOP_SPEC),
            (2, SHOP_SPEC),
            (None, SHOP_SPEC),
            (1, moving_to(2)),
        )
    ]
    reach = Reach(frozenset({1}))
    named = [mine, *others]
    assert named_to(db_session, named, WHOLE) == named
    told = named_to(db_session, named, reach)
    assert told == [mine]
    assert named_to(db_session, others, reach) == []
    refusal = folder_writes._in_use(len(named), told)  # noqa: SLF001
    assert '"rules" and 3 more.' in refusal.message
    assert refusal.extra == {"rule_sets": [{"id": mine.id, "name": "rules"}]}
    hidden = folder_writes._in_use(len(others), [])  # noqa: SLF001
    assert "under it: 3 more." in hidden.message


@pytest.mark.usefixtures("two_accounts")
def test_accounts_beyond_cannot_be_changed_through_a_narrowed_context(
    ctx: AppContext, db_session: Session, user: User
) -> None:
    narrow = ctx.within(Reach(frozenset({1})))
    fields = {k: v for k, v in new_account("home").items() if k != "password"}
    for call in (
        lambda: accounts_service.change_account(narrow, db_session, user.id, 2, fields, None),
        lambda: accounts_service.remove_account(narrow, db_session, user.id, 2),
        lambda: accounts_service.try_login(narrow, user.id, 2),
        lambda: filters_service.run_filters(narrow, db_session, user.id, 2),
    ):
        with pytest.raises(Refusal) as caught:
            call()
        assert caught.value.code == "not_found"
    # Changing one within keeps the others, and the mode it is stored with.
    set_mode(ctx, user, "direct")
    work = {k: v for k, v in new_account("work").items() if k != "password"}
    capped = ctx.within(Reach(frozenset({1}), "drafts"))
    accounts_service.change_account(
        capped, db_session, user.id, 1, {**work, "send_mode": "direct"}, None
    )
    kept = ctx.accounts(user.id).settings.account
    assert [(a.id, a.send_mode) for a in kept] == [(1, "direct"), (2, "drafts")]


@pytest.mark.usefixtures("two_accounts")
def test_removing_an_account_takes_it_out_of_the_grants(
    ctx: AppContext, db_session: Session, user: User
) -> None:
    grants = []
    for account_ids in ([1, 2], [2], None):
        _, grant = agent_origin(db_session, user, ["mail_read"])
        grant.account_ids = account_ids
        grants.append(grant)
    db_session.flush()

    app_data.forget_account(ctx.store(user.id), db_session, user.id, 2)

    for grant in grants:
        db_session.refresh(grant)
    # One left without any reaches none: never all of them.
    assert [grant.account_ids for grant in grants] == [[1], [], None]


# On real mail


@pytest.fixture
def mail(mcp: Mcp, ctx: AppContext, user: User, imap_server: ImapTestServer) -> MailAccounts:
    return MailAccounts(ctx, user, imap_server)


@pytest.fixture
def writer(mail: MailAccounts, smtp_server: Any) -> Writer:
    return Writer(mail, smtp_of(smtp_server))


def test_a_search_over_all_accounts_covers_only_those_within(
    mcp: Mcp, whole: str, work_only: str, mail: MailAccounts
) -> None:
    work, home = mail.add(), mail.add("home")
    for login, count in ((work, 3), (home, 3)):
        for i in range(count):
            mail.seed(login, mime(f"hello {login} {i}"))
    everything = mcp.text(whole, "messages_search", account_id="*", subject="hello", limit=2)
    assert home in mcp.text(whole, "messages_search", account_id="*", subject="hello")
    within = mcp.text(work_only, "messages_search", account_id="*", subject="hello")
    assert work in within
    assert home not in within
    # The cursor a wider search gave reads nothing beyond either.
    cursor = cursor_in(everything)
    assert cursor is not None
    resumed = mcp.answer(
        work_only,
        "messages_search",
        account_id="*",
        subject="hello",
        cursor=cursor,
    )
    assert home not in str(resumed)
    assert mcp.error(work_only, "messages_search", query=f"account:{home}", subject="x")
    # A ref of home's as the server gives it, the mail there to be read.
    ref = inbox_ref_of(mcp, whole, 2)
    assert "hello" in mcp.text(whole, "message_read", ref=ref)
    assert mcp.error(work_only, "message_read", ref=ref) == NO_MESSAGE


def inbox_ref_of(mcp: Mcp, token: str, account: int) -> str:
    return refs_in(mcp.text(token, "messages_list", account_id=account, folder="INBOX"))[0]


def test_a_send_ceiling_is_kept_by_the_gate(
    mcp: Mcp, ctx: AppContext, user: User, writer: Writer, smtp_server: Any
) -> None:
    out = stranger()
    message: dict[str, Any] = {"to": [out], "subject": "s", "text": "t"}
    writer.mail.seed(writer.login, mime("Question", sender=f"Ana <{out}>", to=writer.me))

    drafts = limited(mcp, user, None, "drafts")
    ref = inbox_ref(mcp, drafts)
    answers = [
        mcp.text(drafts, "message_send", **message),
        mcp.text(drafts, "message_reply", ref=ref, text="t"),
        mcp.text(drafts, "message_forward", ref=ref, to=[out]),
    ]
    assert len(writer.contents("Drafts")) == 3, answers
    draft = draft_ref(mcp.text(drafts, "draft_create", **message))
    assert "send mode is drafts" in mcp.error(drafts, "draft_send", ref=draft)
    later = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    assert "send mode is drafts" in mcp.error(drafts, "draft_send", ref=draft, send_at=later)

    off = limited(mcp, user, None, "off")
    assert "send_mode off" in mcp.error(off, "message_send", **message)
    assert "send_mode off" in mcp.error(off, "draft_create", **message)
    assert "send_mode off" in mcp.error(off, "draft_send", ref=draft)
    assert "send_mode off" in mcp.error(off, "draft_delete", ref=draft)

    # Approval: only to the account's auto-approved recipients, as in that mode.
    approval = limited(mcp, user, None, "approval")
    mcp.text(approval, "message_send", **message)
    assert smtp_server.delivered_to(out) == []
    approved = stranger()
    set_mode(ctx, user, "direct", auto_approve=[approved])
    mcp.text(approval, "message_send", **{**message, "to": [approved]})
    settle_sends(ctx.send_runner)
    assert len(smtp_server.delivered_to(approved)) == 1

    # A ceiling above the account's own mode opens nothing.
    set_mode(ctx, user, "drafts")
    mcp.text(limited(mcp, user, None, "direct"), "message_send", **message)
    assert smtp_server.delivered_to(out) == []
    # The user's own send is not the app's: nothing limits it here.
    mail_service.send_draft(ctx, user.id, draft, by="user")
    settle_sends(ctx.send_runner)
    assert len(smtp_server.delivered_to(out)) == 1


def test_a_held_send_is_checked_against_the_limits_the_grant_has_when_it_goes(
    mcp: Mcp,
    ctx: AppContext,
    user: User,
    writer: Writer,
    whole: str,
    db_session: Session,
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    smtp_server: Any,
) -> None:
    origin, grant = agent_origin(db_session, user, ["mail_read", "mail_draft", "mail_send"])
    later = datetime.now(UTC) + timedelta(hours=1)

    def run(account_ids: list[int] | None, ceiling: str | None) -> tuple[str, int]:
        to = stranger()
        ref = draft_ref(mcp.text(whole, "draft_create", to=[to], subject="s", text="t"))
        held = mail_service.schedule_later(ctx, user.id, ref, later, origin=origin)
        grant.account_ids, grant.send_ceiling = account_ids, ceiling
        db_session.flush()
        outcome = send_scheduled(sessions, mailboxes, int(held.scheduled_id))
        return outcome, len(smtp_server.delivered_to(to))

    assert run([2], None) == ("not_approved", 0)
    assert run([], None) == ("not_approved", 0)
    assert run(None, "drafts") == ("invalid_argument", 0)
    assert run(None, "approval") == ("invalid_argument", 0)
    assert run(None, "off") == ("send_disabled", 0)
    assert run([1], "direct") == ("ok", 1)


def test_a_narrowed_app_works_on_its_own_account(
    mcp: Mcp, user: User, work_only: str, writer: Writer, smtp_server: Any, ctx: AppContext
) -> None:
    writer.mail.add("home")
    create_folders(writer.mail.server, writer.login, "Archive")
    writer.mail.seed(writer.login, mime("mine"))
    ref = inbox_ref(mcp, work_only)
    assert "mine" in mcp.text(work_only, "message_read", ref=ref)
    mcp.text(work_only, "messages_move", refs=[ref], folder="Archive")
    out = stranger()
    # With one account in reach, it need not be named.
    mcp.text(work_only, "message_send", to=[out], subject="s", text="t")
    settle_sends(ctx.send_runner)
    assert len(smtp_server.delivered_to(out)) == 1
