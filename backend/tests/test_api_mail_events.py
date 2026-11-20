"""Live updates (``docs/requirements/mail-client.md``, *Live updates*): the events an index change
becomes, the hub's bounded streams, and the event stream itself, driven as a browser would hold
it open (raw ASGI, on an application of its own whose lifespan runs in the test's loop)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from mailroom.api.deps import get_db_session
from mailroom.api.main import create_application
from mailroom.api.routes.mail import events as events_route
from mailroom.auth.sessions import SESSION_COOKIE_NAME, create_session, end_session
from mailroom.config.features import Features
from mailroom.errors import Refusal
from mailroom.mail import events
from mailroom.mail.events import EventHub, IndexNote, MailEvent, folder_events
from mailroom.mail.index import NOTIFY_CHANNEL, notify
from mailroom.models import MailIndexFolder, MailViewInterest
from mailroom_core import mail as core
from tests.test_api_mail import MAIL
from tests.test_mail_index_rows import indexed, message

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    import psycopg
    from fastapi import FastAPI, Request
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import sessionmaker
    from starlette.testclient import TestClient

    from mailroom.models import User

EVENTS = f"{MAIL}/events"
ACCOUNT = 7


def test_signed_out_browsers_are_refused_uncached(api_client: TestClient) -> None:
    response = api_client.get(EVENTS)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"


def test_without_a_hub_live_updates_are_unavailable(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(application.state, "mail_events", None)
    response = user_client.get(EVENTS)
    assert (response.status_code, response.json()["detail"]["code"]) == (503, "events_unavailable")
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.parametrize("query", [f"account={ACCOUNT}", "folder=INBOX"])
def test_half_a_viewed_folder_is_refused(
    user_client: TestClient, application: FastAPI, monkeypatch: pytest.MonkeyPatch, query: str
) -> None:
    monkeypatch.setattr(application.state, "mail_events", _hub())
    response = user_client.get(f"{EVENTS}?{query}")
    assert (response.status_code, response.json()["detail"]["code"]) == (400, "invalid_interest")


def test_one_stream_too_many_is_refused_before_it_opens(
    user_client: TestClient,
    application: FastAPI,
    user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events, "STREAMS_PER_USER", 1)
    hub = _hub()
    hub.open(user.id, None)
    monkeypatch.setattr(application.state, "mail_events", hub)
    response = user_client.get(EVENTS)
    assert (response.status_code, response.json()["detail"]["code"]) == (429, "too_many_streams")
    assert len(hub._streams[user.id]) == 1  # noqa: SLF001


def test_a_notification_carries_ids_and_the_new_mails_range(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as listening:
        listening.execute(text(f"LISTEN {NOTIFY_CHANNEL}"))
        listening.commit()
        with Session(migrated_engine) as db:
            notify(db, 1, 2, 3, new=(10, 12))
            notify(db, 1, 2, 4)
            db.commit()
        raw = cast("psycopg.Connection[Any]", listening.connection.driver_connection)
        announced = list(raw.notifies(timeout=5, stop_after=2))
    assert [IndexNote.parse(note.payload) for note in announced] == [
        IndexNote(1, 2, 3, (10, 12)),
        IndexNote(1, 2, 4),
    ]
    for broken in ("", "[]", '{"user_id": 1}', '{"user_id": "a", "account_id": 2, "folder_id": 3}'):
        assert IndexNote.parse(broken) is None


# What a change becomes.


def _folder(db: Session, user: User, path: str = "INBOX", **changes: Any) -> MailIndexFolder:
    folder_id = indexed(db, user.id, ACCOUNT, path, uids=())
    row = db.get(MailIndexFolder, folder_id)
    assert row is not None
    defaults = {"uid_validity": 5, "message_count": 4, "unread_count": 3}
    for name, value in (defaults | changes).items():
        setattr(row, name, value)
    db.commit()
    return row


def _by_name(found: list[MailEvent]) -> dict[str, dict[str, Any]]:
    return {event.name: event.data for event in found}


def test_a_change_becomes_the_folders_counts(db_session: Session, user: User) -> None:
    row = _folder(db_session, user)
    found = folder_events(db_session, IndexNote(user.id, ACCOUNT, row.id), {})
    assert _by_name(found) == {
        "folder_changed": {"account": ACCOUNT, "folder": "INBOX", "unread": 3, "total": 4}
    }


def test_another_users_folder_or_one_off_the_index_becomes_nothing(
    db_session: Session, user: User, admin: User
) -> None:
    row = _folder(db_session, user)
    assert folder_events(db_session, IndexNote(admin.id, ACCOUNT, row.id), {}) == []
    assert folder_events(db_session, IndexNote(user.id, ACCOUNT + 1, row.id), {}) == []
    row.enabled = False
    db_session.commit()
    assert folder_events(db_session, IndexNote(user.id, ACCOUNT, row.id), {}) == []


def test_a_sync_is_told_while_it_runs_and_once_it_is_ready(db_session: Session, user: User) -> None:
    row = _folder(db_session, user, state="syncing", message_count=500)
    note = IndexNote(user.id, ACCOUNT, row.id)
    states: dict[int, str] = {}
    syncing = _by_name(folder_events(db_session, note, states))
    assert syncing["sync"] == {
        "account": ACCOUNT,
        "folder": "INBOX",
        "state": "syncing",
        "progress": 500,
    }
    row.state = "ready"
    db_session.commit()
    assert _by_name(folder_events(db_session, note, states))["sync"]["state"] == "ready"
    assert "sync" not in _by_name(folder_events(db_session, note, states))


def test_new_mail_names_the_newest_unread_in_its_range(db_session: Session, user: User) -> None:
    from datetime import UTC, datetime

    row = _folder(db_session, user)
    long_subject = "s" * 300
    db_session.add_all(
        [
            message(row.id, user.id, 1, subject="old"),
            *(
                message(
                    row.id,
                    user.id,
                    uid,
                    received=datetime(2026, 11, 20, uid, tzinfo=UTC),
                    subject=f"new {uid}",
                )
                for uid in (10, 11, 12)
            ),
            message(row.id, user.id, 13, subject="read", flags=["\\Seen"]),
            message(
                row.id,
                user.id,
                14,
                received=datetime(2026, 11, 20, 20, tzinfo=UTC),
                sender=[{"name": None, "address": "bob@example.org"}],
                subject=long_subject,
            ),
        ]
    )
    db_session.commit()
    found = _by_name(folder_events(db_session, IndexNote(user.id, ACCOUNT, row.id, (10, 14)), {}))
    arrived = found["new_mail"]
    assert (arrived["account"], arrived["folder"], arrived["count"]) == (ACCOUNT, "INBOX", 4)
    assert [m["subject"] for m in arrived["messages"]] == ["s" * 200, "new 12", "new 11"]
    assert [m["sender"] for m in arrived["messages"]] == ["bob@example.org", "Ann", "Ann"]
    assert arrived["messages"][0]["ref"] == core.format_ref(
        core.MessageRef(ACCOUNT, "INBOX", 5, 14)
    )

    nothing_unread = IndexNote(user.id, ACCOUNT, row.id, (13, 13))
    assert "new_mail" not in _by_name(folder_events(db_session, nothing_unread, {}))


def test_on_gmail_new_mail_is_inboxs_with_all_mails_refs(db_session: Session, user: User) -> None:
    row = _folder(db_session, user, "[Gmail]/All Mail")
    db_session.add_all(
        [
            message(row.id, user.id, 20, gm_msgid=1, labels=["\\Inbox"], subject="to read"),
            message(row.id, user.id, 21, gm_msgid=2, labels=["\\Sent"], subject="sent"),
        ]
    )
    db_session.commit()
    found = _by_name(folder_events(db_session, IndexNote(user.id, ACCOUNT, row.id, (20, 21)), {}))
    arrived = found["new_mail"]
    assert (arrived["folder"], arrived["count"]) == ("INBOX", 1)
    [shown] = arrived["messages"]
    assert shown["ref"] == core.format_ref(core.MessageRef(ACCOUNT, "[Gmail]/All Mail", 5, 20))
    assert found["folder_changed"]["folder"] == "[Gmail]/All Mail"


def test_an_event_is_one_sse_frame_whatever_its_text() -> None:
    event = MailEvent("new_mail", {"subject": "line\r\nevent: forged\n\ndata: x"})
    frame = event.encode().decode()
    assert frame.startswith("event: new_mail\ndata: ")
    assert frame.endswith("\n\n")
    assert frame.count("\n") == 3


# The hub's streams.


def _hub() -> EventHub:
    async def never() -> Any:
        raise AssertionError

    return EventHub(lambda: None, never)


def test_a_slow_stream_is_dropped_and_the_others_go_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "QUEUE_BOUND", 2)
    hub = _hub()
    slow = hub.open(1, None)
    other = hub.open(2, None)
    event = MailEvent("folder_changed", {})
    for _ in range(3):
        hub.publish(1, [event])
    hub.publish(2, [event])
    assert slow.ended
    assert not other.ended
    assert other.queue.qsize() == 1
    hub.publish(1, [event])
    assert slow.queue.qsize() == 2


def test_a_user_may_hold_a_few_streams_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events, "STREAMS_PER_USER", 2)
    hub = _hub()
    first = hub.open(1, (ACCOUNT, "INBOX"))
    hub.open(1, None)
    with pytest.raises(Refusal) as refused:
        hub.open(1, None)
    assert (refused.value.kind, refused.value.code) == ("busy", "too_many_streams")
    hub.open(2, None)
    assert hub.viewing(1, (ACCOUNT, "INBOX"))
    hub.close_stream(first)
    assert not hub.viewing(1, (ACCOUNT, "INBOX"))
    hub.open(1, None)


# The stream, as a browser holds it.


@dataclass
class Opened:
    """An open stream: its status, headers and what has arrived; ``leave()`` disconnects."""

    status: int
    headers: dict[str, str]
    messages: asyncio.Queue[dict[str, Any]]
    gone: asyncio.Event
    task: asyncio.Task[None]
    buffer: str = ""
    frames: list[str] = field(default_factory=list)

    async def frame(self) -> str:
        """The next frame, an event's or a comment's."""
        while not self.frames:
            sent = await asyncio.wait_for(self.messages.get(), 10)
            assert sent["type"] == "http.response.body"
            assert sent.get("more_body", False), "the stream ended"
            self.buffer += sent["body"].decode()
            *complete, self.buffer = self.buffer.split("\n\n")
            self.frames.extend(complete)
        return self.frames.pop(0)

    async def event(self) -> tuple[str, dict[str, Any]]:
        """The next event, past any comment."""
        while (frame := await self.frame()).startswith(":"):
            pass
        name, data = frame.split("\n")
        return name.removeprefix("event: "), json.loads(data.removeprefix("data: "))

    async def leave(self) -> None:
        self.gone.set()
        await asyncio.wait_for(self.task, 10)


def _scope(cookie: str, query: str = "") -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": EVENTS,
        "raw_path": EVENTS.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"cookie", f"{SESSION_COOKIE_NAME}={cookie}".encode()),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 443),
    }


def _receive(gone: asyncio.Event) -> Callable[[], Awaitable[dict[str, Any]]]:
    """The request, then a disconnect once ``gone`` is set."""
    asked = False

    async def receive() -> dict[str, Any]:
        nonlocal asked
        if not asked:
            asked = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    return receive


async def open_stream(application: FastAPI, cookie: str, query: str = "") -> Opened:
    messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    gone = asyncio.Event()

    async def send(sent: dict[str, Any]) -> None:
        await messages.put(sent)

    task = asyncio.create_task(application(_scope(cookie, query), _receive(gone), send))  # type: ignore[arg-type]
    start = await asyncio.wait_for(messages.get(), 10)
    assert start["type"] == "http.response.start"
    headers = {name.decode(): value.decode() for name, value in start["headers"]}
    return Opened(start["status"], headers, messages, gone, task)


type Scenario = Callable[[FastAPI, EventHub, str], Awaitable[None]]


@pytest.fixture
def run_stream(
    db_session: Session, sessions: sessionmaker[Session], user: User
) -> Callable[[Scenario], None]:
    """Runs a scenario on an application of its own with the mail client, its lifespan in the
    scenario's loop, its routes and its hub's reads inside the test's transaction."""
    cookie = create_session(db_session, user)

    def run(scenario: Scenario) -> None:
        async def main() -> None:
            application = create_application(features=Features(mail=True, mcp=False, filters=False))
            application.dependency_overrides[get_db_session] = lambda: db_session
            async with application.router.lifespan_context(application):
                hub = application.state.mail_events
                assert hub is not None
                application.state.session_maker = sessions
                await scenario(application, hub, cookie)

        asyncio.run(main())

    return run


def _interests(db: Session) -> list[tuple[int, int, str]]:
    query = select(MailViewInterest.user_id, MailViewInterest.account_id, MailViewInterest.folder)
    return [tuple(row) for row in db.execute(query)]  # type: ignore[misc]


def test_the_stream_brings_the_users_events_and_holds_the_viewed_folder(
    db_session: Session,
    migrated_engine: Engine,
    user: User,
    run_stream: Callable[[Scenario], None],
) -> None:
    inbox = _folder(db_session, user)
    db_session.add(message(inbox.id, user.id, 9, subject="hello"))
    db_session.commit()

    async def scenario(application: FastAPI, hub: EventHub, cookie: str) -> None:
        stream = await open_stream(application, cookie, f"account={ACCOUNT}&folder=inbox")
        assert stream.status == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["cache-control"] == "private, no-store"
        assert stream.headers["x-accel-buffering"] == "no"
        assert await stream.frame() == ": open"
        assert _interests(db_session) == [(user.id, ACCOUNT, "INBOX")]

        await asyncio.wait_for(hub.listening.wait(), 10)
        payload = {"user_id": user.id, "account_id": ACCOUNT, "folder_id": inbox.id, "new": [9, 9]}
        with migrated_engine.connect() as announcing:
            announcing.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": NOTIFY_CHANNEL, "payload": json.dumps(payload)},
            )
            announcing.commit()
        assert await stream.event() == (
            "folder_changed",
            {"account": ACCOUNT, "folder": "INBOX", "unread": 3, "total": 4},
        )
        name, arrived = await stream.event()
        assert (name, arrived["count"], arrived["messages"][0]["subject"]) == (
            "new_mail",
            1,
            "hello",
        )

        await stream.leave()
        assert not hub.viewing(user.id, (ACCOUNT, "INBOX"))
        assert _interests(db_session) == []

    run_stream(scenario)


def test_a_quiet_stream_beats_and_a_folder_off_the_index_is_not_held(
    db_session: Session,
    user: User,
    run_stream: Callable[[Scenario], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events_route, "HEARTBEAT_SECONDS", 0.05)

    async def scenario(application: FastAPI, hub: EventHub, cookie: str) -> None:
        stream = await open_stream(application, cookie, f"account={ACCOUNT}&folder=Archive")
        assert await stream.frame() == ": open"
        assert await stream.frame() == ": heartbeat"
        assert await stream.frame() == ": heartbeat"
        assert _interests(db_session) == []
        hub.publish(user.id, [MailEvent("folder_changed", {"folder": "Archive"})])
        assert await stream.event() == ("folder_changed", {"folder": "Archive"})
        await stream.leave()
        assert user.id not in hub._streams  # noqa: SLF001

    run_stream(scenario)


def test_a_stream_ends_when_the_hub_drops_it(
    db_session: Session,
    user: User,
    run_stream: Callable[[Scenario], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(events, "QUEUE_BOUND", 1)

    async def scenario(application: FastAPI, hub: EventHub, cookie: str) -> None:
        stream = await open_stream(application, cookie)
        assert await stream.frame() == ": open"
        event = MailEvent("folder_changed", {})
        hub.publish(user.id, [event, event])
        await asyncio.wait_for(stream.task, 10)
        sent = []
        while not stream.messages.empty():
            sent.append(stream.messages.get_nowait())
        assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
        assert user.id not in hub._streams  # noqa: SLF001

    run_stream(scenario)


def test_a_stream_counts_only_once_its_body_starts(user: User) -> None:
    hub = _hub()
    state = SimpleNamespace(mail_events=hub)
    request = cast("Request", SimpleNamespace(app=SimpleNamespace(state=state)))

    async def main() -> None:
        response = await events_route.mail_events(request, cast("Session", None), user)
        assert user.id not in hub._streams  # noqa: SLF001
        # The browser gone before the body starts: the body is closed unstarted.
        await cast("AsyncGenerator[bytes]", response.body_iterator).aclose()

    asyncio.run(main())
    assert user.id not in hub._streams  # noqa: SLF001


def test_a_stream_ends_with_its_session(
    db_session: Session,
    user: User,
    run_stream: Callable[[Scenario], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _folder(db_session, user)
    monkeypatch.setattr(events_route, "HEARTBEAT_SECONDS", 0.2)

    async def scenario(application: FastAPI, hub: EventHub, cookie: str) -> None:
        stream = await open_stream(application, cookie, f"account={ACCOUNT}&folder=INBOX")
        assert await stream.frame() == ": open"
        assert _interests(db_session) == [(user.id, ACCOUNT, "INBOX")]
        # Signed out (run here, on the loop, while the stream waits for its heartbeat).
        end_session(db_session, cookie)
        await asyncio.wait_for(stream.task, 10)
        assert user.id not in hub._streams  # noqa: SLF001
        assert _interests(db_session) == []

    run_stream(scenario)


def test_an_interest_that_cannot_be_held_is_logged_and_the_stream_goes_on(
    db_session: Session,
    user: User,
    run_stream: Callable[[Scenario], None],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _folder(db_session, user)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("Quarterly report from ann@example.org")

    monkeypatch.setattr(events_route, "hold_interest", fail)

    async def scenario(application: FastAPI, hub: EventHub, cookie: str) -> None:
        stream = await open_stream(application, cookie, f"account={ACCOUNT}&folder=INBOX")
        assert await stream.frame() == ": open"
        hub.publish(user.id, [MailEvent("folder_changed", {"folder": "INBOX"})])
        assert await stream.event() == ("folder_changed", {"folder": "INBOX"})
        await stream.leave()

    with caplog.at_level("WARNING", logger=events_route.__name__):
        run_stream(scenario)
    [record] = [r for r in caplog.records if r.getMessage() == "mail_interest_failed"]
    assert (record.user_id, record.error) == (user.id, "RuntimeError")  # type: ignore[attr-defined]
    assert "ann@example.org" not in caplog.text
    assert _interests(db_session) == []
