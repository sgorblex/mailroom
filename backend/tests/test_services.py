"""The accounts and filters services on their own, as any front end calls them: each user reaches
only their own, and every refusal is a ``Refusal`` the REST routes word as they always have."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from mailroom.accounts import service as accounts
from mailroom.api.refusals import http_refusal
from mailroom.auth.users import create_user
from mailroom.context import AppContext
from mailroom.errors import Refusal, mail_refusal
from mailroom.filters import service as filters
from mailroom.filters.runs import dry_run_slot
from mailroom.filters.service import FilterFields, FilterPatch, RuleSetFields, RuleSetPatch
from mailroom.mail.connections import mail_ssl_context
from mailroom.mail.pool import MailPool
from mailroom.mail.sends import SendRunner
from mailroom_core.mail import MESSAGES
from tests.conftest import PASSWORD
from tests.test_api_accounts import MAILBOX_PASSWORD, new_account
from tests.test_api_filters import SHOP_SPEC

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User
    from mailroom_core.crypto import SecretsSubkey


@pytest.fixture
def ctx(
    migrated_engine: Engine, sessions: sessionmaker[Session], secrets_subkey: SecretsSubkey
) -> Iterator[AppContext]:
    pool = MailPool()
    runner = SendRunner()
    try:
        yield AppContext(
            migrated_engine, sessions, secrets_subkey, mail_ssl_context(), pool, runner
        )
    finally:
        runner.close()
        pool.close()


def _fields() -> dict[str, Any]:
    return {k: v for k, v in new_account().items() if k not in {"id", "password"}}


@pytest.fixture
def account(ctx: AppContext, user: User) -> str:
    accounts.add_account(ctx, user.id, "work", _fields(), MAILBOX_PASSWORD)
    return "work"


@pytest.fixture
def rule_set_id(ctx: AppContext, db_session: Session, user: User, account: str) -> int:
    fields = RuleSetFields(name="Newsletters", folder=["INBOX"], trigger="manual")
    return filters.add_rule_set(ctx, db_session, user.id, account, fields).id


@pytest.fixture
def filter_id(db_session: Session, user: User, rule_set_id: int) -> int:
    fields = FilterFields(name="Shop", spec=SHOP_SPEC)
    return filters.add_filter(db_session, user.id, rule_set_id, fields).id


@pytest.fixture
def bob(db_session: Session) -> User:
    return create_user(db_session, "bob", PASSWORD)


def refusal(call: Callable[[], object]) -> Refusal:
    with pytest.raises(Refusal) as caught:
        call()
    return caught.value


def test_another_users_rows_are_not_found(
    ctx: AppContext,
    db_session: Session,
    user: User,
    bob: User,
    account: str,
    rule_set_id: int,
    filter_id: int,
) -> None:
    calls: list[Callable[[], object]] = [
        lambda: accounts.change_account(ctx, db_session, bob.id, account, _fields(), None),
        lambda: accounts.remove_account(ctx, db_session, bob.id, account),
        lambda: filters.run_filters(ctx, db_session, bob.id, account),
        lambda: filters.set_run_filters(ctx, db_session, bob.id, account, on=True),
        lambda: filters.account_folders(ctx, bob.id, account),
        lambda: filters.rule_sets(ctx, db_session, bob.id, account),
        lambda: filters.add_rule_set(
            ctx,
            db_session,
            bob.id,
            account,
            RuleSetFields(name="x", folder=["INBOX"], trigger="manual"),
        ),
        lambda: filters.rule_set_detail(db_session, bob.id, rule_set_id),
        lambda: filters.change_rule_set(db_session, bob.id, rule_set_id, RuleSetPatch(name="x")),
        lambda: filters.remove_rule_set(db_session, bob.id, rule_set_id),
        lambda: filters.add_filter(
            db_session, bob.id, rule_set_id, FilterFields(name="x", spec=SHOP_SPEC)
        ),
        lambda: filters.reorder_filters(db_session, bob.id, rule_set_id, [filter_id]),
        lambda: filters.change_filter(db_session, bob.id, filter_id, FilterPatch(name="x")),
        lambda: filters.remove_filter(db_session, bob.id, filter_id),
        lambda: filters.dry_run(ctx, db_session, bob.id, rule_set_id),
        lambda: filters.run_now(db_session, bob.id, rule_set_id),
        lambda: filters.runs(db_session, bob.id, rule_set_id, 20),
    ]
    for call in calls:
        assert refusal(call).kind == "not_found"
    assert accounts.listed_accounts(ctx, db_session, bob.id) == []

    # Alice's are untouched.
    detail = filters.rule_set_detail(db_session, user.id, rule_set_id)
    assert (detail.name, [row.name for row in detail.filters]) == ("Newsletters", ["Shop"])
    assert [entry.id for entry in accounts.listed_accounts(ctx, db_session, user.id)] == [account]


def test_an_enabled_rule_set_is_refused_where_asked(
    db_session: Session, user: User, rule_set_id: int, filter_id: int
) -> None:
    filters.change_rule_set(db_session, user.id, rule_set_id, RuleSetPatch(enabled=True))
    calls: list[Callable[[], object]] = [
        lambda: filters.change_rule_set(
            db_session, user.id, rule_set_id, RuleSetPatch(name="x"), disabled_only=True
        ),
        lambda: filters.remove_rule_set(db_session, user.id, rule_set_id, disabled_only=True),
        lambda: filters.add_filter(
            db_session,
            user.id,
            rule_set_id,
            FilterFields(name="x", spec=SHOP_SPEC),
            disabled_only=True,
        ),
        lambda: filters.change_filter(
            db_session, user.id, filter_id, FilterPatch(name="x"), disabled_only=True
        ),
        lambda: filters.remove_filter(db_session, user.id, filter_id, disabled_only=True),
        lambda: filters.reorder_filters(
            db_session, user.id, rule_set_id, [filter_id], disabled_only=True
        ),
    ]
    for call in calls:
        refused = refusal(call)
        assert (refused.kind, refused.code, refused.message) == (
            "conflict",
            "rule_set_enabled",
            "Disable the rule set first",
        )
    detail = filters.rule_set_detail(db_session, user.id, rule_set_id)
    assert (detail.name, [row.name for row in detail.filters]) == ("Newsletters", ["Shop"])

    # Without the guard, as the web app asks, the change goes through; once disabled, so does
    # a guarded one.
    filters.change_filter(db_session, user.id, filter_id, FilterPatch(name="Shop 2"))
    filters.change_rule_set(db_session, user.id, rule_set_id, RuleSetPatch(enabled=False))
    changed = filters.change_rule_set(
        db_session, user.id, rule_set_id, RuleSetPatch(name="News"), disabled_only=True
    )
    assert (changed.name, changed.enabled) == ("News", False)


def test_run_now_keeps_its_guards(
    ctx: AppContext, db_session: Session, user: User, account: str, rule_set_id: int
) -> None:
    assert refusal(lambda: filters.run_now(db_session, user.id, rule_set_id)).code == (
        "run_filters_off"
    )
    filters.set_run_filters(ctx, db_session, user.id, account, on=True)
    assert refusal(lambda: filters.run_now(db_session, user.id, rule_set_id)).code == (
        "rule_set_disabled"
    )
    filters.change_rule_set(db_session, user.id, rule_set_id, RuleSetPatch(enabled=True))
    run = filters.run_now(db_session, user.id, rule_set_id)
    assert (run.trigger, run.dry_run, run.started_at) == ("manual", False, None)
    pending = refusal(lambda: filters.run_now(db_session, user.id, rule_set_id))
    assert (pending.kind, pending.code) == ("conflict", "run_pending")


@pytest.mark.usefixtures("filter_id")  # an enabled filter: the dry run reaches for the mailbox
def test_one_dry_run_at_a_time(
    ctx: AppContext, db_session: Session, user: User, rule_set_id: int
) -> None:
    with dry_run_slot(ctx.engine, user.id):
        busy = refusal(lambda: filters.dry_run(ctx, db_session, user.id, rule_set_id))
    assert (busy.kind, busy.code) == ("busy", "dry_run_busy")
    assert filters.runs(db_session, user.id, rule_set_id, 20) == []
    # Port 9 on loopback is closed: the mailbox's refusal, recorded.
    unreachable = refusal(lambda: filters.dry_run(ctx, db_session, user.id, rule_set_id))
    assert (unreachable.kind, unreachable.code) == ("mail", "unreachable")
    [run] = filters.runs(db_session, user.id, rule_set_id, 20)
    assert run.error is not None
    assert run.error.startswith("unreachable: ")


def test_checks_refuse_as_invalid(
    ctx: AppContext, db_session: Session, user: User, account: str, rule_set_id: int
) -> None:
    scheduled = RuleSetFields(name="x", folder=["INBOX"], trigger="scheduled")
    assert refusal(
        lambda: filters.add_rule_set(ctx, db_session, user.id, account, scheduled)
    ).code == ("invalid_rule_set")
    bad_spec = FilterFields(name="x", spec={"criterion": {"type": "nope"}})
    assert refusal(lambda: filters.add_filter(db_session, user.id, rule_set_id, bad_spec)).code == (
        "invalid_spec"
    )
    assert refusal(lambda: filters.reorder_filters(db_session, user.id, rule_set_id, [1])).code == (
        "invalid_order"
    )
    taken = refusal(lambda: accounts.add_account(ctx, user.id, account, _fields(), "x"))
    assert (taken.kind, taken.code) == ("invalid", "account_exists")
    assert taken.issues == [{"path": ["id"], "message": "There already is an account 'work'."}]


def test_writes_log_where_they_came_from(
    ctx: AppContext,
    db_session: Session,
    user: User,
    rule_set_id: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    filters.change_rule_set(db_session, user.id, rule_set_id, RuleSetPatch(name="x"), via="mcp")
    [record] = [r for r in caplog.records if r.msg == "rule_set_changed"]
    assert (record.__dict__["via"], record.__dict__["user_id"]) == ("mcp", user.id)


def test_accounts_change_without_the_mail_layer(
    ctx: AppContext, db_session: Session, user: User, account: str
) -> None:
    bare = replace(ctx, send_runner=None)
    changed = accounts.change_account(bare, db_session, user.id, account, _fields(), None)
    assert changed.id == account
    accounts.remove_account(bare, db_session, user.id, account)
    assert accounts.listed_accounts(bare, db_session, user.id) == []


def test_without_a_secrets_key_no_account_is_added(
    ctx: AppContext, db_session: Session, user: User
) -> None:
    keyless = replace(ctx, subkey=None)
    refused = refusal(lambda: accounts.add_account(keyless, user.id, "work", _fields(), "x"))
    assert (refused.kind, refused.code) == ("unavailable", "secrets_key_missing")
    assert accounts.listed_accounts(ctx, db_session, user.id) == []


@pytest.mark.parametrize(
    ("refused", "status", "detail"),
    [
        (Refusal("not_found", "rule_set_not_found", "No such rule set"), 404, "No such rule set"),
        (
            Refusal("invalid", "invalid_spec", "bad"),
            400,
            {"code": "invalid_spec", "message": "bad"},
        ),
        (
            Refusal("invalid", "invalid_account", "bad", issues=[{"path": ["id"], "message": "m"}]),
            400,
            {
                "code": "invalid_account",
                "message": "bad",
                "issues": [{"path": ["id"], "message": "m"}],
            },
        ),
        (Refusal("conflict", "run_pending", "m"), 409, {"code": "run_pending", "message": "m"}),
        (Refusal("busy", "dry_run_busy", "m"), 429, {"code": "dry_run_busy", "message": "m"}),
        (
            Refusal("unavailable", "accounts_busy", "m"),
            503,
            {"code": "accounts_busy", "message": "m"},
        ),
        (
            Refusal("timeout", "dry_run_timeout", "m"),
            504,
            {"code": "dry_run_timeout", "message": "m"},
        ),
        (Refusal("failed", "run_failed", "m"), 500, {"code": "run_failed", "message": "m"}),
        (mail_refusal("unreachable", "m"), 502, {"code": "unreachable", "message": "m"}),
        (mail_refusal("timeout", "m"), 504, {"code": "timeout", "message": "m"}),
        (mail_refusal("stale_reference", "m"), 409, {"code": "stale_reference", "message": "m"}),
        (mail_refusal("too_large", "m"), 413, {"code": "too_large", "message": "m"}),
        (
            mail_refusal("too_large", MESSAGES["too_large"]),
            413,
            {
                "code": "too_large",
                "message": "Too large: the message, attachment or upload is over its limit.",
            },
        ),
        (mail_refusal("delivery_unknown", "m"), 409, {"code": "delivery_unknown", "message": "m"}),
        (
            mail_refusal("recipient_refused", "m"),
            422,
            {"code": "recipient_refused", "message": "m"},
        ),
        (mail_refusal("mailbox_missing", "m"), 404, {"code": "mailbox_missing", "message": "m"}),
        (mail_refusal("busy", "m"), 429, {"code": "busy", "message": "m"}),
        (mail_refusal("invalid_argument", "m"), 400, {"code": "invalid_argument", "message": "m"}),
        (mail_refusal("folder_exists", "m"), 409, {"code": "folder_exists", "message": "m"}),
        (
            Refusal("mail", "folder_changed", "m", extra={"messages": 3}),
            409,
            {"messages": 3, "code": "folder_changed", "message": "m"},
        ),
        (
            Refusal(
                "conflict", "folder_in_use", "m", extra={"rule_sets": [{"id": 1, "name": "n"}]}
            ),
            409,
            {"rule_sets": [{"id": 1, "name": "n"}], "code": "folder_in_use", "message": "m"},
        ),
        (mail_refusal("some_new_code", "m"), 502, {"code": "some_new_code", "message": "m"}),
        (mail_refusal("password_missing", "m"), 409, {"code": "password_missing", "message": "m"}),
        (
            mail_refusal("secrets_key_missing", "m"),
            503,
            {"code": "secrets_key_missing", "message": "m"},
        ),
    ],
)
def test_the_api_words_each_kind(refused: Refusal, status: int, detail: object) -> None:
    answer = http_refusal(refused)
    assert (answer.status_code, answer.detail) == (status, detail)
