"""The mail index's settings (``docs/requirements/mail-client.md``, *Index*): the age limit in
force, each account's folders as the index keeps them (a row on first sight, Junk off, Gmail's
labels read from All Mail), and switching a folder on or off. The routes against the Dovecot
``IMAP_TEST_HOST`` names; skipped without it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import func, select

from mailroom.mail.index import MAX_AGE_ENV, effective_max_age, max_age_cap
from mailroom.models import MailIndexFolder, MailIndexMessage
from mailroom_core.mail import Capabilities, Folder, MailConnections
from mailroom_core.testing import create_folders
from tests.test_api_mail import MAIL, Mailbox, detail
from tests.test_api_mail import mailbox as mailbox  # the fixture
from tests.test_mail_index_rows import message

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from starlette.testclient import TestClient

    from mailroom.models import User

STATUS = f"{MAIL}/index/accounts/1"
SWITCH = f"{MAIL}/index/accounts/1/folders"


@pytest.mark.parametrize(
    ("cap", "user_days", "expected_cap", "expected"),
    [
        (None, None, None, None),
        ("", 30, None, 30),
        ("90", None, 90, 90),
        ("90", 30, 90, 30),
        ("30", 90, 30, 30),
        ("0", None, 1, 1),
        ("99999999", None, 36500, 36500),
        ("a year", 30, None, 30),
    ],
)
def test_the_age_limit_is_the_users_capped_by_the_instances(
    monkeypatch: pytest.MonkeyPatch,
    cap: str | None,
    user_days: int | None,
    expected_cap: int | None,
    expected: int | None,
) -> None:
    if cap is None:
        monkeypatch.delenv(MAX_AGE_ENV, raising=False)
    else:
        monkeypatch.setenv(MAX_AGE_ENV, cap)
    assert max_age_cap() == expected_cap
    assert effective_max_age(user_days) == expected


def _by_path(status: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {folder["path"]: folder for folder in status["folders"]}


def _row(db: Session, user: User, path: str) -> MailIndexFolder:
    db.expire_all()
    row = db.scalar(
        select(MailIndexFolder).where(
            MailIndexFolder.user_id == user.id, MailIndexFolder.path == path
        )
    )
    assert row is not None
    return row


def test_every_folder_but_junk_is_indexed_from_first_sight(
    mailbox: Mailbox, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(MAX_AGE_ENV, "400")
    create_folders(mailbox.server, mailbox.login, "Junk", "Projects")
    mailbox.seed("Subject: one\r\n\r\nbody\r\n")
    limit = {"index_max_age_days": 500}
    assert mailbox.client.put("/api/v1/auth/me/index-max-age", json=limit).status_code == 200

    status = mailbox.get("/index/accounts/1")
    assert status["account_id"] == 1
    assert status["gmail"] is False
    assert (status["max_age_days"], status["max_age_cap_days"]) == (400, 400)
    folders = _by_path(status)
    assert folders["INBOX"] == {
        "path": "INBOX",
        "role": "inbox",
        "indexed_as": "folder",
        "enabled": True,
        "total": 1,
        "state": "pending",
        "error": None,
        "indexed": 0,
        "unread": 0,
        "oldest_indexed": None,
        "last_synced_at": None,
    }
    assert folders["Projects"]["enabled"] is True
    assert folders["Junk"]["role"] == "junk"
    assert folders["Junk"]["enabled"] is False
    assert folders["Junk"]["state"] is None
    assert [folder["path"] for folder in status["folders"]] == [
        folder["path"] for folder in mailbox.get("/accounts/1/folders")["folders"]
    ]

    # Seen again, a folder keeps the user's choice.
    _row(db_session, user, "INBOX").enabled = False
    db_session.commit()
    assert _by_path(mailbox.get("/index/accounts/1"))["INBOX"]["enabled"] is False
    count = select(func.count()).select_from(MailIndexFolder)
    assert db_session.scalar(count) == len(status["folders"])


def test_a_page_following_a_first_sync_counts_only_the_folders_waiting(
    mailbox: Mailbox, db_session: Session, user: User
) -> None:
    """``totals=waiting``: STATUS of the folders waiting for or in their sync only, and no row
    for a folder first seen (the Profile page's read gives it)."""
    create_folders(mailbox.server, mailbox.login, "Projects")
    mailbox.seed("Subject: one\r\n\r\nbody\r\n")
    assert _by_path(mailbox.get("/index/accounts/1", totals="waiting"))["INBOX"]["enabled"] is False
    assert db_session.scalar(select(func.count()).select_from(MailIndexFolder)) == 0

    mailbox.get("/index/accounts/1")
    _row(db_session, user, "Projects").state = "ready"
    db_session.commit()
    folders = _by_path(mailbox.get("/index/accounts/1", totals="waiting"))
    assert (folders["INBOX"]["state"], folders["INBOX"]["total"]) == ("pending", 1)
    assert (folders["Projects"]["state"], folders["Projects"]["total"]) == ("ready", None)
    assert mailbox.client.get(f"{STATUS}?totals=some").status_code == 422


def test_switching_a_folder_off_deletes_its_rows(
    mailbox: Mailbox, db_session: Session, user: User
) -> None:
    create_folders(mailbox.server, mailbox.login, "Projects")
    mailbox.get("/index/accounts/1")
    inbox = _row(db_session, user, "INBOX")
    projects = _row(db_session, user, "Projects")
    db_session.add_all(message(inbox.id, user.id, uid) for uid in (1, 2))
    db_session.add(message(projects.id, user.id, 1))
    inbox.state, inbox.uid_validity, inbox.message_count = "ready", 7, 2
    db_session.commit()

    switched = mailbox.client.put(SWITCH, json={"folder": "inbox", "enabled": False})
    assert switched.status_code == 200, switched.text
    assert switched.json() == {"account_id": 1, "folder": "INBOX", "enabled": False}
    inbox = _row(db_session, user, "INBOX")
    assert (inbox.enabled, inbox.state, inbox.uid_validity, inbox.message_count) == (
        False,
        "pending",
        None,
        0,
    )
    left = select(MailIndexMessage.folder_id, MailIndexMessage.uid)
    assert list(db_session.execute(left)) == [(projects.id, 1)]
    off = _by_path(mailbox.get("/index/accounts/1"))["INBOX"]
    assert (off["enabled"], off["state"], off["indexed"]) == (False, None, None)

    switched = mailbox.client.put(SWITCH, json={"folder": "INBOX", "enabled": True})
    assert switched.status_code == 200
    assert _by_path(mailbox.get("/index/accounts/1"))["INBOX"]["state"] == "pending"
    assert _row(db_session, user, "INBOX").enabled is True


def test_a_folder_never_listed_is_looked_up_before_its_switch(
    mailbox: Mailbox, db_session: Session, user: User
) -> None:
    create_folders(mailbox.server, mailbox.login, "Junk")
    switched = mailbox.client.put(SWITCH, json={"folder": "Junk", "enabled": True})
    assert switched.status_code == 200, switched.text
    assert _row(db_session, user, "Junk").enabled is True
    assert _row(db_session, user, "INBOX").enabled is True

    missing = mailbox.client.put(SWITCH, json={"folder": "Nowhere", "enabled": True})
    assert detail(missing) == (404, "mailbox_missing")
    inbox_on = {"folder": "INBOX", "enabled": True}
    other = mailbox.client.put(f"{MAIL}/index/accounts/2/folders", json=inbox_on)
    assert detail(other) == (404, "not_found")
    assert detail(mailbox.client.get(f"{MAIL}/index/accounts/2")) == (404, "not_found")
    for body in (
        {"folder": "INBOX", "enabled": "false"},
        {"folder": "", "enabled": False},
        {"folder": "INBOX"},
        {"folder": "INBOX", "enabled": False, "other": 1},
    ):
        assert mailbox.client.put(SWITCH, json=body).status_code == 422, body


def test_virtual_folders_are_off_by_default_but_can_be_switched_on(
    mailbox: Mailbox, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = [
        Folder("INBOX", "/", "inbox", ("\\HasNoChildren",), 3, 1),
        Folder("Virtual/All", "/", "all", ("\\All",), 9, 2),
        Folder("Virtual/Flagged", "/", "flagged", ("\\Flagged",), 1, 0),
        Folder("Virtual/Starred", "/", None, ("\\HasNoChildren", "\\Flagged"), 1, 0),
        Folder("Archive", "/", "archive", ("\\Archive",), 5, 0),
    ]
    monkeypatch.setattr(MailConnections, "folders", lambda *_, **__: listed)

    folders = _by_path(mailbox.get("/index/accounts/1"))
    assert {path: (f["indexed_as"], f["enabled"]) for path, f in folders.items()} == {
        "INBOX": ("folder", True),
        "Virtual/All": ("folder", False),
        "Virtual/Flagged": ("folder", False),
        "Virtual/Starred": ("folder", False),
        "Archive": ("folder", True),
    }
    switched = mailbox.client.put(SWITCH, json={"folder": "Virtual/All", "enabled": True})
    assert switched.status_code == 200, switched.text
    assert _row(db_session, user, "Virtual/All").enabled is True
    assert _by_path(mailbox.get("/index/accounts/1"))["Virtual/All"]["state"] == "pending"


def test_on_gmail_only_all_mail_is_indexed(
    mailbox: Mailbox, db_session: Session, user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    gmail = Capabilities(
        gmail=True,
        thread=True,
        move=True,
        uidplus=True,
        condstore=True,
        special_use=True,
        quota=False,
    )
    listed = [
        Folder("INBOX", "/", "inbox", ("\\HasNoChildren",), 3, 1),
        Folder("[Gmail]/All Mail", "/", "all", ("\\All",), 9, 2),
        Folder("[Gmail]/Spam", "/", "junk", ("\\Junk",), 4, 4),
        Folder("[Gmail]/Trash", "/", "trash", ("\\Trash",), 2, 0),
        Folder("Receipts", "/", None, (), 1, 0),
    ]
    monkeypatch.setattr(MailConnections, "capabilities", lambda *_: gmail)
    monkeypatch.setattr(MailConnections, "folders", lambda *_, **__: listed)

    status = mailbox.get("/index/accounts/1")
    assert status["gmail"] is True
    folders = _by_path(status)
    assert {path: folder["indexed_as"] for path, folder in folders.items()} == {
        "INBOX": "label",
        "[Gmail]/All Mail": "folder",
        "[Gmail]/Spam": "live",
        "[Gmail]/Trash": "live",
        "Receipts": "label",
    }
    assert [row.path for row in db_session.scalars(select(MailIndexFolder))] == ["[Gmail]/All Mail"]
    assert folders["INBOX"]["enabled"] is True
    assert (folders["INBOX"]["state"], folders["INBOX"]["indexed"]) == ("pending", None)
    assert folders["INBOX"]["total"] == 3
    assert folders["[Gmail]/Spam"]["enabled"] is False

    for path in ("INBOX", "[Gmail]/Spam"):
        refused = mailbox.client.put(SWITCH, json={"folder": path, "enabled": False})
        assert detail(refused) == (409, "index_gmail_label")
    switched = mailbox.client.put(SWITCH, json={"folder": "[Gmail]/All Mail", "enabled": False})
    assert switched.status_code == 200
    folders = _by_path(mailbox.get("/index/accounts/1"))
    assert folders["INBOX"]["enabled"] is False
    assert folders["Receipts"]["state"] is None

    # A Gmail account whose All Mail is hidden from IMAP indexes nothing.
    monkeypatch.setattr(MailConnections, "folders", lambda *_, **__: [listed[0], listed[4]])
    kinds = {f["indexed_as"] for f in mailbox.get("/index/accounts/1")["folders"]}
    assert kinds == {"live"}


def test_index_routes_need_a_session(api_client: TestClient) -> None:
    assert api_client.get(STATUS).status_code == 401
    refused = api_client.put(SWITCH, json={"folder": "INBOX", "enabled": False})
    assert refused.status_code == 401
    assert refused.headers["cache-control"] == "private, no-store"
