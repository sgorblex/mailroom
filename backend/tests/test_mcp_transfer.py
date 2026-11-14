"""The ``mail_transfer`` tools over ``/mcp``: a preview group, served only when named, moving
mail to another of the user's accounts, its originals to Trash or, with a tool of its own,
deleted for good. The mail cases run against the Dovecot ``IMAP_TEST_HOST`` names (skipped
without it)."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

import pytest
from starlette.testclient import TestClient

from mailroom.api.main import create_application
from mailroom.config.features import ALL_FEATURES
from mailroom.context import app_context
from mailroom.mcp.app import instructions, tools_for
from mailroom.mcp.groups import selected_groups
from mailroom.mcp.settings import McpSettings
from mailroom.models import AuditRecord
from mailroom_core.mail import MailError
from mailroom_core.testing import mime
from tests.support.mail import mail_servers_ssl_context
from tests.test_mcp import ENVELOPE, PUBLIC_URL, FakeVerifier, MailAccounts, Mcp, refs_in, stored

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.models import User
    from mailroom_core.testing import ImapTestServer

TOOLS = ("messages_move_to_account", "messages_move_to_account_for_good")


@pytest.fixture(scope="module")
def _transfer_application(
    _worker_engine: Engine,
) -> Iterator[tuple[TestClient, FakeVerifier, FastAPI]]:
    verifier = FakeVerifier()
    groups = selected_groups("mail_read,mail_transfer")
    settings = McpSettings(public_url=PUBLIC_URL, auth="builtin", groups=groups)
    application = create_application(
        features=ALL_FEATURES, mcp_settings=settings, mcp_verifier=verifier
    )
    with TestClient(application, base_url=PUBLIC_URL) as client:
        yield client, verifier, application


@pytest.fixture
def mcp(
    _transfer_application: tuple[TestClient, FakeVerifier, FastAPI],
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Mcp]:
    client, verifier, application = _transfer_application
    verifier.tokens.clear()
    monkeypatch.setattr(application.state, "session_maker", sessions)
    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    yield Mcp(client, verifier, application)
    pool = application.state.mail_pool
    pool.close_idle(time.monotonic() + pool.idle_seconds)


@pytest.fixture
def token(mcp: Mcp, user: User) -> str:
    return mcp.verifier.issue(user.id)


@pytest.fixture
def mail(mcp: Mcp, user: User, imap_server: ImapTestServer) -> MailAccounts:
    ctx = app_context(mcp.app.state)
    assert ctx is not None
    return MailAccounts(ctx, user, imap_server)


def work_inbox_ref(mcp: Mcp, token: str) -> str:
    """The ref of the newest message in work's INBOX."""
    return refs_in(mcp.text(token, "messages_list", account_id="work", folder="INBOX"))[0]


def test_the_group_is_served_only_when_named() -> None:
    assert not {tool.name for tool in tools_for(selected_groups(None))} & set(TOOLS)
    assert "messages_move_to_account" not in instructions(selected_groups(None))
    groups = selected_groups("mail_read,mail_transfer")
    names = [tool.name for tool in tools_for(groups)]
    assert set(TOOLS) <= set(names)
    assert names[:2] == ["accounts_list", "folders_list"]
    text = instructions(groups)
    assert "messages_move_to_account_for_good" in text
    assert "only when the user explicitly asks" in text


def test_both_tools_are_destructive_and_bounded(mcp: Mcp, token: str) -> None:
    tools = {tool["name"]: tool for tool in mcp.tools(token)}
    for name in TOOLS:
        annotations = tools[name]["annotations"]
        assert (annotations["readOnlyHint"], annotations["destructiveHint"]) == (False, True)
        assert annotations["openWorldHint"] is True
        assert "Invalid arguments" in mcp.error(
            token, name, refs=[], account_id="b", folder="INBOX"
        )
        assert "Invalid arguments" in mcp.error(token, name, refs=["x"], folder="INBOX")
        assert "Invalid arguments" in mcp.error(
            token, name, refs=["x"], account="b", folder="INBOX"
        )
    assert "permanently" in tools["messages_move_to_account_for_good"]["description"]


def test_moves_to_another_account_and_trashes_the_originals(
    mcp: Mcp, token: str, mail: MailAccounts, user: User, db_session: Session
) -> None:
    work = mail.add()
    home = mail.add("home")
    mail.seed(work, mime("for home"))
    ref = work_inbox_ref(mcp, token)

    moved = mcp.text(
        token, "messages_move_to_account", refs=[ref], account_id="home", folder="INBOX"
    )

    assert ENVELOPE.search(moved)
    assert re.search(r"work/INBOX: moved 1 to home/INBOX\. New refs: [\w-]+", moved)
    assert stored(mail, home, "INBOX") == [("for home", ())]
    assert stored(mail, work, "INBOX") == []
    assert stored(mail, work, "Trash") == [("for home", ())]
    records = db_session.query(AuditRecord).filter(AuditRecord.user_id == user.id).all()
    assert sorted((r.actor, r.action, r.account_id) for r in records) == [
        ("agent", "move_to_account", "home"),
        ("agent", "move_to_account", "work"),
    ]


def test_deletes_the_originals_for_good_only_through_its_own_tool(
    mcp: Mcp, token: str, mail: MailAccounts
) -> None:
    work = mail.add()
    home = mail.add("home")
    mail.seed(work, mime("gone for good"))
    ref = work_inbox_ref(mcp, token)

    mcp.text(
        token, "messages_move_to_account_for_good", refs=[ref], account_id="home", folder="INBOX"
    )

    assert stored(mail, home, "INBOX") == [("gone for good", ())]
    assert stored(mail, work, "INBOX") == []
    assert stored(mail, work, "Trash") == []


def test_refusals_are_tool_errors_and_write_nothing(
    mcp: Mcp, token: str, mail: MailAccounts
) -> None:
    work = mail.add()
    home = mail.add("home")
    mail.seed(work, mime("stay"))
    ref = work_inbox_ref(mcp, token)

    for tool in TOOLS:
        for account, folder, text in (
            ("work", "INBOX", "move them within it"),
            ("home", "Drafts", "Drafts or Sent"),
            ("elsewhere", "INBOX", "No such account"),
        ):
            error = mcp.error(token, tool, refs=[ref], account_id=account, folder=folder)
            assert text in error, error
    assert stored(mail, work, "INBOX") == [("stay", ())]
    assert stored(mail, home, "INBOX") == []


def test_messages_left_in_both_accounts_are_answered_not_refused(
    mcp: Mcp, token: str, mail: MailAccounts, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = mail.add()
    home = mail.add("home")
    mail.seed(work, mime("stuck"))
    ref = work_inbox_ref(mcp, token)

    def fails(*_args: object) -> None:
        raise MailError("server_error")

    # Inside mailroom-core's move: its removal fails once the copy is made.
    monkeypatch.setattr("mailroom_core.mail.transfer.move_uids", fails)
    answer = mcp.text(
        token, "messages_move_to_account", refs=[ref], account_id="home", folder="INBOX"
    )

    assert ENVELOPE.search(answer)
    assert "in both" in answer
    assert "do not move them again" in answer
    assert stored(mail, home, "INBOX") == [("stuck", ())]
    assert stored(mail, work, "INBOX") == [("stuck", ())]
