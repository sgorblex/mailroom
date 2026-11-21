"""Lists, searches and folder counts read from the mail index (``mailroom.mail.index.reads`` and
the mail service): the index's cursor, Gmail's label views, the rows a search finds (only the
user's own), a move out of a label view, and, against the test Dovecot (``IMAP_TEST_HOST``;
skipped without it), the API's pages compared with the live ones, counts, the live read of what
the age limit leaves out and a folder switched off between pages."""

from __future__ import annotations

import base64
import json
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, ClassVar, cast

import pytest
from sqlalchemy import select

from mailroom.auth.users import create_user
from mailroom.mail.index import reads
from mailroom.mail.index.reads import IndexCursor, IndexSource, decode_cursor, gmail_label
from mailroom.mail.service import organize
from mailroom.models import MailIndexFolder
from mailroom_core import mail as core
from mailroom_core.mail import (
    MESSAGES,
    TO_SORT,
    Folder,
    MailError,
    MessageRef,
    PageFailure,
    SearchQuery,
    Source,
    format_ref,
)
from mailroom_core.render import Address
from mailroom_core.testing import MimeAttachment, create_folders, mime, seed
from tests.conftest import PASSWORD
from tests.test_mail_index_rows import message
from tests.test_mail_index_sync import folder_row, synced

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from fastapi import FastAPI
    from sqlalchemy.orm import Session, sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import User
    from mailroom.worker.context import Mailboxes
    from mailroom_core.testing import ImapTestServer

MAIL = "/api/v1/mail"
DAY = datetime(2026, 11, 20, tzinfo=UTC)
ALL_MAIL = "[Gmail]/All Mail"


@pytest.fixture(autouse=True)
def _every_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILROOM_FEATURES", raising=False)


def ready(db: Session, user: User, account: int, path: str, **changes: Any) -> int:
    row = MailIndexFolder(
        user_id=user.id,
        account_id=account,
        path=path,
        **({"uid_validity": 7, "state": "ready", "enabled": True} | changes),
    )
    db.add(row)
    db.commit()
    return row.id


def folder(path: str, *flags: str, role: str | None = None) -> Folder:
    return Folder(path=path, delimiter="/", role=role, flags=flags)  # type: ignore[arg-type]


def unasked(_account: int) -> Any:
    raise AssertionError("not asked")


GMAIL = [
    folder("INBOX", role="inbox"),
    folder("[Gmail]", "\\Noselect"),
    folder(ALL_MAIL, "\\All", role="all"),
    folder("[Gmail]/Spam", "\\Junk", role="junk"),
    folder("[Gmail]/Sent Mail", "\\Sent", role="sent"),
    folder("[Gmail]/Starred", "\\Flagged", role="flagged"),
    folder("Work"),
    folder("Home"),
]


# Without a mail server.


def test_a_cursor_of_the_index_reads_back() -> None:
    source = IndexSource(1, "INBOX", 5, 7, None, None, ("INBOX",))
    labelled = IndexSource(1, ALL_MAIL, 6, 9, ("\\Inbox", "Work"), None, ("INBOX", "Work"))
    key = (datetime(2026, 11, 20, 9, 30, 1, 123456, tzinfo=UTC), 6, 42)
    failure = PageFailure.of(2, None, MailError("timeout"))

    back = decode_cursor(IndexCursor.index([source, labelled], key, [failure], {}).encode())
    assert back is not None
    assert (back.mode, back.sources, back.key) == (reads.INDEX, (source, labelled), key)
    assert back.failures({}) == [failure]
    assert failure.message == MESSAGES["timeout"]

    tail = IndexCursor.tail_from([(1, "INBOX", date(2026, 10, 21))], None, [], {})
    assert decode_cursor(tail.encode()) == tail
    assert decode_cursor(None) is None
    assert decode_cursor(core.encode_cursor([])) is None
    assert decode_cursor("not a cursor") is None

    forged = json.dumps([3, "index", [["1", "INBOX"]], [1, 2, 3], None]).encode()
    with pytest.raises(MailError) as refused:
        decode_cursor(base64.urlsafe_b64encode(forged).decode())
    assert refused.value.code == "invalid_argument"

    real = IndexCursor.index([source], key, [], {}).encode()
    value = json.loads(base64.urlsafe_b64decode(real + "=" * (-len(real) % 4)))
    value[3][0] = 10**20  # an arrival time no datetime holds
    far = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
    with pytest.raises(MailError) as overflowed:
        decode_cursor(far)
    assert overflowed.value.code == "invalid_argument"


def test_gmail_folders_are_read_by_their_labels() -> None:
    labels = {f.path: gmail_label(f) for f in GMAIL}
    assert labels == {
        "INBOX": "\\Inbox",
        "[Gmail]": None,
        ALL_MAIL: None,
        "[Gmail]/Spam": None,
        "[Gmail]/Sent Mail": "\\Sent",
        "[Gmail]/Starred": "\\Starred",
        "Work": "Work",
        "Home": "Home",
    }


def test_a_search_finds_the_users_own_rows(db_session: Session, user: User) -> None:
    other = create_user(db_session, "someone-else", PASSWORD)
    inbox = ready(db_session, user, 1, "INBOX")
    theirs = ready(db_session, other, 1, "INBOX")
    at = DAY + timedelta(hours=9)
    db_session.add_all(
        [
            message(inbox, user.id, 1, received=at, subject="Quarterly report"),
            message(
                inbox,
                user.id,
                2,
                received=at + timedelta(hours=1),
                subject="Lunch 100%",
                sender_text="Bob bob@example.org",
                flags=["\\Flagged", "\\Seen"],
                has_attachments=True,
            ),
            message(
                inbox,
                user.id,
                3,
                received=at + timedelta(hours=2),
                to_text="Carol carol@example.org",
                subject="Minutes",
                keywords=[TO_SORT],
            ),
            message(theirs, other.id, 9, received=at, subject="Quarterly report"),
        ]
    )
    db_session.commit()
    sources = reads.index_sources(
        db_session, user.id, [Source(1, "inbox")], gmail=unasked, listing=unasked
    )
    assert sources is not None

    def uids(query: SearchQuery | None, **options: Any) -> list[int]:
        found = reads.read_rows(db_session, user.id, sources, query, limit=10, **options)
        return [summary.uid for summary, _ in found]

    assert uids(None) == [3, 2, 1]
    assert uids(SearchQuery(subject="QUARTERLY")) == [1]
    assert uids(SearchQuery(subject="%")) == [2]
    assert uids(SearchQuery(sender="bob")) == [2]
    assert uids(SearchQuery(to="carol")) == [3]
    assert uids(SearchQuery(unread=True)) == [3, 1]
    assert uids(SearchQuery(unread=False, flagged=True, has_attachment=True)) == [2]
    assert uids(SearchQuery(to_sort=True)) == [3]
    assert uids(SearchQuery(since=DAY.date(), before=DAY.date() + timedelta(days=1))) == [3, 2, 1]
    assert uids(SearchQuery(before=DAY.date())) == []

    first = reads.read_rows(db_session, user.id, sources, None, limit=2)
    assert uids(None, after=first[-1][1]) == [1]
    [(summary, _)] = reads.read_rows(db_session, user.id, sources, None, limit=1)
    assert summary.ref == format_ref(MessageRef(1, "INBOX", 7, 3))
    assert summary.flags == frozenset({TO_SORT})


def test_a_gmail_label_view_reads_all_mails_rows(db_session: Session, user: User) -> None:
    every = ready(db_session, user, 1, ALL_MAIL)
    db_session.add_all(
        [
            message(every, user.id, 1, labels=["\\Inbox"], received=DAY),
            message(every, user.id, 2, labels=["Work"], received=DAY + timedelta(hours=1)),
            message(every, user.id, 3, labels=[], received=DAY + timedelta(hours=2)),
        ]
    )
    db_session.commit()

    def views(*paths: str) -> list[IndexSource] | None:
        wanted = [Source(1, path) for path in paths]
        return reads.index_sources(
            db_session, user.id, wanted, gmail=lambda _: True, listing=lambda _: GMAIL
        )

    sources = views("INBOX", "Work")
    assert sources is not None
    [source] = sources
    assert (source.folder, source.labels, source.live) == (
        ALL_MAIL,
        ("Work", "\\Inbox"),
        ("INBOX", "Work"),
    )
    assert source.listed == (("\\Inbox", "INBOX"), ("Work", "Work"))
    found = reads.read_rows(db_session, user.id, sources, None, limit=10)
    assert [summary.uid for summary, _ in found] == [2, 1]
    assert {summary.folder for summary, _ in found} == {ALL_MAIL}
    # Each row says the folder it was listed under, where an action on it acts.
    assert [cast("reads.ListedSummary", summary).listed_folder for summary, _ in found] == [
        "Work",
        "INBOX",
    ]
    key = found[0][1]
    resumed = decode_cursor(reads.IndexCursor.index(sources, key, [], {}).encode())
    assert resumed is not None
    assert resumed.sources == tuple(sources)

    every_message = views(ALL_MAIL)
    assert every_message is not None
    assert (every_message[0].labels, every_message[0].listed) == (None, ())
    [(whole, _), *_] = reads.read_rows(db_session, user.id, every_message, None, limit=10)
    assert cast("reads.ListedSummary", whole).listed_folder is None
    assert views("INBOX", "[Gmail]/Spam") is None
    assert (
        reads.index_sources(
            db_session, user.id, [Source(1, "INBOX")], gmail=lambda _: False, listing=unasked
        )
        is None
    )


def test_a_read_of_every_folder_leaves_out_virtual_folders_off_the_index(
    db_session: Session, user: User
) -> None:
    listed = [
        folder("INBOX", role="inbox"),
        folder("Virtual/All", "\\All", role="all"),
        folder("Virtual/Flagged", "\\Flagged", role="flagged"),
        folder("Archive", "\\Archive", role="archive"),
        folder("Junk", "\\Junk", role="junk"),
    ]
    ready(db_session, user, 1, "INBOX")
    ready(db_session, user, 1, "Archive")
    ready(db_session, user, 1, "Virtual/All", enabled=False, state="pending")
    ready(db_session, user, 1, "Virtual/Flagged", enabled=False, state="pending")

    def every_folder(*paths: str) -> list[str] | None:
        picked = reads.index_sources(
            db_session,
            user.id,
            [Source(1, path) for path in paths],
            gmail=lambda _: False,
            listing=lambda _: listed,
            every_folder=True,
        )
        return None if picked is None else [source.folder for source in picked]

    # mailroom-core reads All Mail alone where there is one; the index, the folders it repeats.
    assert every_folder("Virtual/All") == ["INBOX", "Archive"]
    assert every_folder("INBOX", "Virtual/Flagged", "Archive") == ["INBOX", "Archive"]

    switched = db_session.scalars(
        select(MailIndexFolder).where(MailIndexFolder.path == "Virtual/All")
    )
    switched.one().enabled = True
    db_session.commit()
    # Switched on, it is read, once ready.
    assert every_folder("Virtual/All") is None


class _GmailMailbox:
    """Gmail as far as an organize call goes: a message is in All Mail and, under the same UID,
    in the folder of each of its labels. A move out of a label's folder takes that label off and
    puts the destination's on; a copy only puts it on."""

    LABELS: ClassVar[dict[str, str]] = {"INBOX": "\\Inbox", "Work": "Work", "Home": "Home"}

    def __init__(self, labels: dict[int, set[str]]) -> None:
        self.labels = labels
        """By UID; Gmail's message id is 100 more."""
        self.calls: list[tuple[str, str, str]] = []

    def holds(self, path: str, uid: int) -> bool:
        return uid in self.labels and (path == ALL_MAIL or self.LABELS[path] in self.labels[uid])

    def connections(self) -> Any:
        return SimpleNamespace(
            folders=lambda _account: GMAIL,
            capabilities=lambda _account: SimpleNamespace(gmail=True),
            serves=lambda _account: True,
            with_mailbox=self._with_mailbox,
        )

    def _with_mailbox(self, account: int, path: str, read_only: bool, fn: Any) -> Any:
        validity = 7 if path == ALL_MAIL else 3
        client = cast("Any", _GmailClient(self, path))
        return fn(core.MailboxContext(client, path, read_only, validity, 0, None, account))


class _GmailClient:
    def __init__(self, box: _GmailMailbox, path: str) -> None:
        self.box, self.path = box, path
        self._imap = SimpleNamespace(untagged_responses={})

    def has_capability(self, name: str) -> bool:
        return name in ("X-GM-EXT-1", "MOVE", "UIDPLUS")

    def _raw_command_untagged(self, command: bytes, arguments: list[bytes]) -> list[bytes]:
        assert (command, arguments[0]) == (b"SEARCH", b"X-GM-MSGID")
        uid = int(arguments[-1]) - 100
        return [str(uid).encode() if self.box.holds(self.path, uid) else b""]

    def fetch(self, uids: list[int], items: list[str]) -> dict[int, dict[bytes, object]]:
        assert items == ["ENVELOPE"]
        return {uid: {b"ENVELOPE": None} for uid in uids if self.box.holds(self.path, uid)}

    def _put(self, kind: str, uids: list[int], destination: str) -> None:
        self.box.calls.append((kind, self.path, destination))
        for uid in uids:
            if kind == "move":
                self.box.labels[uid].discard(_GmailMailbox.LABELS[self.path])
            self.box.labels[uid].add(_GmailMailbox.LABELS[destination])
        moved = ",".join(str(uid) for uid in uids).encode()
        self._imap.untagged_responses["COPYUID"] = [b"3 " + moved + b" " + moved]

    def move(self, uids: list[int], destination: str) -> None:
        self._put("move", uids, destination)

    def copy(self, uids: list[int], destination: str) -> None:
        self._put("copy", uids, destination)


def test_a_move_from_a_gmail_view_takes_off_that_views_label_only(
    db_session: Session, sessions: sessionmaker[Session], user: User
) -> None:
    box = _GmailMailbox({uid: {"\\Inbox", "Work"} for uid in (1, 2, 3, 4)})
    every = ready(db_session, user, 1, ALL_MAIL)
    db_session.add_all(
        message(every, user.id, uid, gm_msgid=100 + uid, labels=sorted(labels))
        for uid, labels in box.labels.items()
    )
    db_session.commit()
    ctx = cast("Any", SimpleNamespace(session_maker=sessions))
    connections = box.connections()
    ref = {uid: format_ref(MessageRef(1, ALL_MAIL, 7, uid)) for uid in box.labels}

    def acting(
        refs: Sequence[str], source_folder: str | None, own: dict[str, str] | None = None
    ) -> list[str]:
        return organize._from_label_views(  # noqa: SLF001
            ctx, user.id, connections, refs, source_folder, own or {}
        )

    def move(uid: int, source_folder: str | None) -> core.OrganizeResult:
        return core.move_messages(connections, acting([ref[uid]], source_folder), "Home")

    assert move(1, "Work").groups[0].folder == "Work"
    assert box.labels[1] == {"\\Inbox", "Home"}
    assert move(2, "inbox").groups[0].folder == "INBOX"
    assert box.labels[2] == {"Work", "Home"}
    # From All Mail itself, a move only adds the destination's label.
    for uid, source_folder in ((3, ALL_MAIL), (4, None)):
        copied = move(uid, source_folder)
        assert copied.groups[0].ok
        assert copied.groups[0].folder == ALL_MAIL
        assert box.labels[uid] == {"\\Inbox", "Work", "Home"}
        assert [item.copied for item in copied.items] == [True]
    assert [call[:2] for call in box.calls] == [
        ("move", "Work"),
        ("move", "INBOX"),
        ("copy", ALL_MAIL),
        ("copy", ALL_MAIL),
    ]
    # Archiving from All Mail changes nothing, and says so.
    archived = core.archive_messages(connections, [ref[3]])
    error = archived.groups[0].error
    assert error is not None
    assert error.code == "invalid_argument"
    assert box.labels[3] == {"\\Inbox", "Work", "Home"}

    # A ref the index does not hold as All Mail's (another account's, a live list's) stays, and
    # so does one the view's folder does not hold.
    elsewhere = format_ref(MessageRef(2, "INBOX", 5, 1))
    assert acting([elsewhere, ref[1]], "Work") == [elsewhere, ref[1]]
    assert acting([ref[2], ref[3]], "Work") == [
        format_ref(MessageRef(1, "Work", 3, 2)),
        format_ref(MessageRef(1, "Work", 3, 3)),
    ]


def test_a_move_from_a_mixed_gmail_view_takes_off_each_rows_own_label(
    db_session: Session, sessions: sessionmaker[Session], user: User
) -> None:
    box = _GmailMailbox({uid: {"\\Inbox", "Work"} for uid in (1, 2, 3)})
    every = ready(db_session, user, 1, ALL_MAIL)
    db_session.add_all(
        message(every, user.id, uid, gm_msgid=100 + uid, labels=sorted(labels))
        for uid, labels in box.labels.items()
    )
    db_session.commit()
    ctx = cast("Any", SimpleNamespace(session_maker=sessions))
    connections = box.connections()
    ref = {uid: format_ref(MessageRef(1, ALL_MAIL, 7, uid)) for uid in box.labels}

    # A view over INBOX and Work listed 1 under Work and 2 under INBOX; 3 has no folder of its
    # own and takes the call's.
    own = {ref[1]: "Work", ref[2]: "INBOX"}
    acting = organize._from_label_views(  # noqa: SLF001
        ctx, user.id, connections, [ref[1], ref[2], ref[3]], "Work", own
    )
    moved = core.move_messages(connections, acting, "Home")
    assert sorted(group.folder for group in moved.groups) == ["INBOX", "Work"]
    assert box.labels == {
        1: {"\\Inbox", "Home"},
        2: {"Work", "Home"},
        3: {"\\Inbox", "Home"},
    }


def test_a_move_whose_index_cannot_be_read_keeps_its_refs(
    user: User, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def unreadable(*_args: object) -> Any:
        raise RuntimeError("Subject: the sender's text")

    @contextmanager
    def session() -> Iterator[None]:
        yield None

    monkeypatch.setattr(reads, "gmail_ids", unreadable)
    ctx = cast("Any", SimpleNamespace(session_maker=session))
    refs = [format_ref(MessageRef(1, ALL_MAIL, 7, 1))]
    nowhere = cast("Any", None)
    assert organize._from_label_views(ctx, user.id, nowhere, refs, "INBOX", {}) == refs  # noqa: SLF001
    [record] = [r for r in caplog.records if r.msg == "mail_index_read_failed"]
    assert record.__dict__["error"] == "RuntimeError"
    assert "the sender's text" not in caplog.text


# Through the API, against the test Dovecot.


@pytest.fixture
def login(imap_server: ImapTestServer) -> str:
    return imap_server.unique_user("reads")


@pytest.fixture
def mail_account(
    user_client: TestClient,
    imap_server: ImapTestServer,
    login: str,
    application: FastAPI,
    sessions: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    """The user's account on a fresh Dovecot user, the app's index reads in the test's
    transaction."""
    from tests.support.mail import mail_servers_ssl_context
    from tests.test_api_accounts import added, new_account

    monkeypatch.setattr(application.state, "mail_ssl_context", mail_servers_ssl_context())
    monkeypatch.setattr(application.state, "session_maker", sessions)
    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    return added(
        user_client, new_account(login=login, password=imap_server.password, imap=endpoint)
    )


def version(cursor: str) -> int:
    value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    return cast("int", value[0])


def every_page(client: TestClient, request: dict[str, Any]) -> tuple[list[Any], list[int]]:
    """Every message of a list (``folder``, ``subfolders``) or a search (any other field), two
    to a page, and the version of each cursor met."""
    messages: list[Any] = []
    versions: list[int] = []
    cursor: str | None = None
    while True:
        if "account" in request:
            response = client.post(f"{MAIL}/search", json={**request, "limit": 2, "cursor": cursor})
        else:
            account = request["account_id"]
            params = {**request, "limit": 2} | ({} if cursor is None else {"cursor": cursor})
            params.pop("account_id")
            response = client.get(f"{MAIL}/accounts/{account}/messages", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["failures"] == []
        messages += page["messages"]
        cursor = page["next_cursor"]
        if cursor is None:
            return messages, versions
        versions.append(version(cursor))


def requests(account: int) -> Sequence[dict[str, Any]]:
    today = datetime.now(UTC).date()
    return [
        {"account_id": account, "folder": "INBOX"},
        {"account_id": account, "folder": "Projects", "subfolders": "true"},
        {"account": account, "folder": "INBOX", "from": "BOB"},
        {"account": account, "folder": "INBOX", "unread": True},
        {"account": account, "folder": "INBOX", "flagged": True},
        {"account": account, "folder": "INBOX", "has_attachment": True},
        {"account": account, "folder": "INBOX", "to": "carol"},
        {"account": account, "folder": "Projects", "subject": "plan", "subfolders": True},
        {"account": "*", "folder": "*", "subject": "alpha"},
        {"account": "*", "folder": "*"},
        {
            "account": account,
            "folder": "*",
            "since": (today - timedelta(days=1)).isoformat(),
            "before": (today + timedelta(days=2)).isoformat(),
        },
    ]


def test_lists_and_searches_read_from_the_index_as_live(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    create_folders(imap_server, login, "Projects", "Projects/Old")
    start = datetime.now(UTC) - timedelta(hours=1)
    attachment = MimeAttachment("notes.txt", "text/plain", b"notes")
    seeded: list[tuple[str, str, Any, list[str]]] = [
        ("INBOX", "alpha report", mime("alpha report", text="The report is ready."), []),
        ("INBOX", "beta", mime("beta", sender="Bob <bob@example.org>"), ["\\Seen", "\\Flagged"]),
        ("INBOX", "gamma", mime("gamma", attachments=[attachment]), ["\\Seen"]),
        ("INBOX", "delta", mime("delta", to="Carol <carol@example.org>"), []),
        ("INBOX", "epsilon", mime("epsilon", text="Short."), ["\\Seen"]),
        ("Projects", "plan one", mime("plan one"), []),
        ("Projects/Old", "plan two", mime("plan two"), ["\\Seen"]),
        ("Trash", "alpha trashed", mime("alpha trashed"), []),
        ("Sent", "alpha sent", mime("alpha sent"), ["\\Seen"]),
    ]
    for minute, (path, _, raw, flags) in enumerate(seeded):
        seed(imap_server, login, path, raw, flags, start + timedelta(minutes=minute))
    asked = requests(mail_account)
    live = [every_page(user_client, request) for request in asked]
    assert all(version == 2 for _, versions in live for version in versions)
    assert [m["subject"] for m in live[-3][0]] == ["alpha sent", "alpha report"]

    for path in ("INBOX", "Projects", "Projects/Old", "Sent", "Drafts", "Trash"):
        assert synced(db_session, mailboxes, user, mail_account, path).state == "ready"
    for request, (messages, _) in zip(asked, live, strict=True):
        indexed, versions = every_page(user_client, request)
        assert indexed == messages, request
        assert all(version == 3 for version in versions), request


def test_folder_counts_come_from_the_index(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    seed(imap_server, login, "INBOX", mime("read"), ["\\Seen"])
    seed(imap_server, login, "INBOX", mime("unread"))
    inbox = synced(db_session, mailboxes, user, mail_account)
    seed(imap_server, login, "INBOX", mime("after the sync"))
    seed(imap_server, login, "Sent", mime("sent"), ["\\Seen"])

    listed = user_client.get(f"{MAIL}/accounts/{mail_account}/folders").json()["folders"]
    counts = {f["path"]: (f["unread"], f["total"], f["uid_next"]) for f in listed}
    assert counts["INBOX"] == (1, 2, inbox.uid_next)
    assert counts["Sent"][:2] == (0, 1)
    assert folder_row(db_session, user, "Sent").state == "pending"


def test_the_age_limit_leaves_older_mail_to_a_live_read(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    now = datetime.now(UTC)
    for subject, days in (("old", 90), ("older than the limit", 60), ("new", 0)):
        seed(imap_server, login, "INBOX", mime(subject), (), now - timedelta(days=days))
    user.index_max_age_days = 30
    db_session.commit()
    inbox = synced(db_session, mailboxes, user, mail_account)
    assert inbox.message_count == 1

    url = f"{MAIL}/accounts/{mail_account}/messages"
    subjects: list[str] = []
    cursors: list[int] = []
    params: dict[str, Any] = {"folder": "INBOX", "limit": 1}
    while True:
        page = user_client.get(url, params=params).json()
        subjects += [m["subject"] for m in page["messages"]]
        if page["next_cursor"] is None:
            break
        cursors.append(version(page["next_cursor"]))
        params["cursor"] = page["next_cursor"]
    assert subjects == ["new", "older than the limit", "old"]
    assert set(cursors) == {3}

    # Grouped, the page that crosses into the live tail and the one after it pair each message
    # with its conversation, the live ones alone.
    grouped: list[Any] = []
    params = {"folder": "INBOX", "limit": 2, "group": "thread"}
    while True:
        page = user_client.get(url, params=params).json()
        assert len(page["conversations"]) == len(page["messages"])
        grouped += [
            (m["subject"], c["count"])
            for m, c in zip(page["messages"], page["conversations"], strict=True)
        ]
        if page["next_cursor"] is None:
            break
        params["cursor"] = page["next_cursor"]
    assert grouped == [("new", 1), ("older than the limit", 1), ("old", 1)]

    whole = user_client.get(url, params={"folder": "INBOX", "limit": 5}).json()
    assert [m["subject"] for m in whole["messages"]] == subjects
    assert whole["next_cursor"] is None
    recent = user_client.post(
        f"{MAIL}/search",
        json={"account": mail_account, "since": (now - timedelta(days=7)).date().isoformat()},
    ).json()
    assert [m["subject"] for m in recent["messages"]] == ["new"]
    assert recent["next_cursor"] is None


def test_a_folder_switched_off_between_pages_starts_over(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    for subject in ("one", "two"):
        seed(imap_server, login, "INBOX", mime(subject))
    synced(db_session, mailboxes, user, mail_account)
    url = f"{MAIL}/accounts/{mail_account}/messages"
    first = user_client.get(url, params={"folder": "INBOX", "limit": 1}).json()
    assert version(first["next_cursor"]) == 3

    switched = user_client.put(
        f"{MAIL}/index/accounts/{mail_account}/folders", json={"folder": "INBOX", "enabled": False}
    )
    assert switched.status_code == 200, switched.text
    refused = user_client.get(
        url, params={"folder": "INBOX", "limit": 1, "cursor": first["next_cursor"]}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "stale_reference"
    again = user_client.get(url, params={"folder": "INBOX", "limit": 1}).json()
    assert version(again["next_cursor"]) == 2


def test_an_index_that_cannot_be_read_is_read_live(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    for subject in ("one", "two"):
        seed(imap_server, login, "INBOX", mime(subject))
    synced(db_session, mailboxes, user, mail_account)

    def unreadable(*_args: object) -> Any:
        raise RuntimeError("Subject: the sender's text")

    monkeypatch.setattr(reads, "ready_rows", unreadable)
    monkeypatch.setattr(reads, "counted_folders", unreadable)
    url = f"{MAIL}/accounts/{mail_account}/messages"
    first = user_client.get(url, params={"folder": "INBOX", "limit": 1})
    assert first.status_code == 200, first.text
    assert [m["subject"] for m in first.json()["messages"]] == ["two"]
    assert version(first.json()["next_cursor"]) == 2
    listed = user_client.get(f"{MAIL}/accounts/{mail_account}/folders")
    assert listed.status_code == 200, listed.text
    inbox = next(f for f in listed.json()["folders"] if f["path"] == "INBOX")
    assert inbox["total"] == 2
    failures = [r for r in caplog.records if r.msg == "mail_index_read_failed"]
    assert failures
    assert {r.__dict__["error"] for r in failures} == {"RuntimeError"}
    assert "the sender's text" not in caplog.text


def test_a_cursor_whose_folder_was_rebuilt_is_refused(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    for subject in ("one", "two"):
        seed(imap_server, login, "INBOX", mime(subject))
    synced(db_session, mailboxes, user, mail_account)
    url = f"{MAIL}/accounts/{mail_account}/messages"
    first = user_client.get(url, params={"folder": "INBOX", "limit": 1}).json()
    assert version(first["next_cursor"]) == 3

    inbox = folder_row(db_session, user, "INBOX")
    inbox.uid_validity = (inbox.uid_validity or 0) + 1
    db_session.commit()
    refused = user_client.get(
        url, params={"folder": "INBOX", "limit": 1, "cursor": first["next_cursor"]}
    )
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "stale_reference"


def test_an_index_cursor_of_another_account_is_refused(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    from tests.test_api_accounts import added, new_account

    endpoint = {"host": imap_server.host, "port": imap_server.port, "tls": "implicit"}
    home_login = imap_server.unique_user("reads-home")
    home = added(
        user_client,
        new_account("home", login=home_login, password=imap_server.password, imap=endpoint),
    )
    for subject in ("one", "two"):
        seed(imap_server, login, "INBOX", mime(subject))
        seed(imap_server, home_login, "INBOX", mime(subject))
    synced(db_session, mailboxes, user, mail_account)
    first = user_client.get(
        f"{MAIL}/accounts/{mail_account}/messages", params={"folder": "INBOX", "limit": 1}
    ).json()
    assert version(first["next_cursor"]) == 3

    refused = user_client.get(
        f"{MAIL}/accounts/{home}/messages",
        params={"folder": "INBOX", "limit": 1, "cursor": first["next_cursor"]},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"]["code"] == "stale_reference"


def test_a_read_by_conversation_groups_each_accounts_rows(
    db_session: Session, user: User, admin: User
) -> None:
    inbox = ready(db_session, user, 1, "INBOX")
    sent = ready(db_session, user, 1, "Sent")
    home = ready(db_session, user, 2, "INBOX", uid_validity=9)
    theirs = ready(db_session, admin, 1, "INBOX")

    def person(name: str | None, address: str) -> list[dict[str, Any]]:
        return [{"name": name, "address": address}]

    def at(hours: int) -> datetime:
        return DAY + timedelta(hours=hours)

    db_session.add_all(
        [
            message(inbox, user.id, 1, received=at(1), thread_key="<root>", flags=["\\Seen"]),
            message(
                sent,
                user.id,
                1,
                received=at(2),
                thread_key="<root>",
                sender=person(None, "me@example.org"),
                flags=["\\Seen", "\\Flagged"],
            ),
            message(
                inbox,
                user.id,
                2,
                received=at(3),
                thread_key="<root>",
                sender=person("Bob", "bob@example.org"),
                has_attachments=True,
            ),
            message(inbox, user.id, 3, received=at(4)),
            message(inbox, user.id, 4, received=at(5), thread_key="<other>"),
            message(home, user.id, 1, received=at(6), thread_key="<root>"),
            message(theirs, admin.id, 5, received=at(7), thread_key="<root>"),
        ]
    )
    db_session.commit()
    sources = reads.index_sources(
        db_session,
        user.id,
        [Source(1, "INBOX"), Source(1, "Sent"), Source(2, "INBOX")],
        gmail=unasked,
        listing=unasked,
    )
    assert sources is not None

    def ref(account: int, path: str, uid: int, validity: int = 7) -> str:
        return format_ref(MessageRef(account, path, validity, uid))

    def listing(_account: int) -> list[Folder]:
        return [folder("INBOX", role="inbox"), folder("Sent", "\\Sent", role="sent")]

    found = reads.read_conversations(db_session, user.id, sources, None, limit=10, listing=listing)
    assert [(summary.account, summary.uid) for summary, _, _ in found] == [
        (2, 1),
        (1, 4),
        (1, 3),
        (1, 2),
    ]
    root = found[-1][2]
    assert root == reads.Conversation(
        count=3,
        unread=1,
        participants=("Ann", "me@example.org", "Bob"),
        has_attachments=True,
        flagged=True,
        # The user's own copy in Sent is counted, but no action takes it.
        refs=(ref(1, "INBOX", 2), ref(1, "INBOX", 1)),
        latest_from_new_sender=True,
    )
    # The same root in another account is another conversation; a row without one is alone.
    assert found[0][2].refs == (ref(2, "INBOX", 1, 9),)
    assert [conversation.count for _, _, conversation in found] == [1, 1, 1, 3]

    following = reads.read_conversations(
        db_session, user.id, sources, None, limit=10, after=found[1][1], listing=listing
    )
    assert [summary.uid for summary, _, _ in following] == [3, 2]
    unread = reads.read_conversations(
        db_session, user.id, sources, SearchQuery(unread=True), limit=10, listing=listing
    )
    assert [(summary.uid, c.count, c.unread) for summary, _, c in unread][-1] == (2, 1, 1)


def test_a_conversation_names_its_first_senders_once() -> None:
    def summary(uid: int, name: str | None, address: str, *flags: str) -> core.MessageSummary:
        return core.MessageSummary(
            ref=format_ref(MessageRef(1, "INBOX", 7, uid)),
            account=1,
            folder="INBOX",
            uid=uid,
            uid_validity=7,
            date=DAY,
            received=DAY,
            sender=(Address(name=name, address=address),),
            subject="",
            flags=frozenset(flags),
            has_attachments=False,
            preview=None,
            size=1,
        )

    alone = reads.Conversation.single(summary(3, None, "ann@example.org", "\\Flagged"))
    assert (alone.count, alone.unread, alone.flagged, alone.participants) == (
        1,
        1,
        True,
        ("ann@example.org",),
    )
    senders = [(Address(name=name, address=None),) for name in "ABACDEF"]
    assert reads._participants(senders) == ("A", "B", "C", "D")  # noqa: SLF001


def test_a_folder_is_grouped_by_conversation_once_on_the_index(
    user_client: TestClient,
    db_session: Session,
    mailboxes: Mailboxes,
    imap_server: ImapTestServer,
    login: str,
    user: User,
    mail_account: int,
) -> None:
    start = datetime.now(UTC) - timedelta(hours=1)
    root = "<plan@example.org>"
    seeded: list[tuple[str, str]] = [
        ("INBOX", mime("plan", message_id=root)),
        ("Sent", mime("Re: plan", sender="Ale <ale@example.test>", in_reply_to=root)),
        ("INBOX", mime("lunch")),
        (
            "INBOX",
            mime(
                "Re: plan",
                sender="Bob <bob@example.org>",
                in_reply_to="<reply@example.org>",
                references=f"{root} <reply@example.org>",
            ),
        ),
    ]
    for minute, (path, raw) in enumerate(seeded):
        seed(imap_server, login, path, raw, (), start + timedelta(minutes=minute))
    url = f"{MAIL}/accounts/{mail_account}/messages"
    params = {"folder": "INBOX", "group": "thread"}

    live = user_client.get(url, params=params).json()
    assert [m["subject"] for m in live["messages"]] == ["Re: plan", "lunch", "plan"]
    assert live["conversations"] is None

    synced(db_session, mailboxes, user, mail_account)
    grouped = user_client.get(url, params=params).json()
    assert [m["subject"] for m in grouped["messages"]] == ["Re: plan", "lunch"]
    latest = grouped["messages"][0]["ref"]
    plan = grouped["conversations"][0]
    assert (plan["count"], plan["unread"], plan["participants"]) == (
        2,
        2,
        ["Maria Rossi", "Bob"],
    )
    assert plan["refs"][0] == latest

    thread = user_client.get(f"{MAIL}/messages/{latest}/thread")
    assert thread.status_code == 200, thread.text
    assert [m["subject"] for m in thread.json()["messages"]] == ["Re: plan", "Re: plan", "plan"]
    assert {m["folder"] for m in thread.json()["messages"]} == {"INBOX", "Sent"}
    assert (
        user_client.get(f"{MAIL}/messages/{latest}/thread", params={"limit": 0}).status_code == 422
    )
