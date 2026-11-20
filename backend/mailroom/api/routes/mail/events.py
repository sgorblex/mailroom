"""Live updates (``docs/requirements/mail-client.md``, *Live updates*): the signed-in user's
server-sent event stream, which holds the viewing interest of the folder its browser shows."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Final

import anyio
from fastapi import Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from mailroom.api.deps import CurrentUser, DbSession, SessionCookie
from mailroom.api.routes.mail._shared import mail_router
from mailroom.auth.sessions import session_user
from mailroom.errors import Refusal
from mailroom.mail import service
from mailroom.mail.index import drop_interest, hold_interest
from mailroom_core.config import MAX_SAFE_INTEGER

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.orm import Session

    from mailroom.mail.events import EventHub, Interest, Stream

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS: Final = 25.0
"""A comment this often keeps proxies from closing a quiet stream and renews its interest."""
_HEARTBEAT: Final = b": heartbeat\n\n"
_OPEN: Final = b": open\n\n"
"""Sent once the interest is held, so that a proxy passes the headers on at once."""
_UNAVAILABLE: Final = "Live updates are not available now."
_HALF_INTEREST: Final = "Name both the account and the folder viewed, or neither."


@mail_router.get("/events", response_class=StreamingResponse)
async def mail_events(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    session_cookie: SessionCookie = None,
    account: Annotated[int | None, Query(ge=1, le=MAX_SAFE_INTEGER)] = None,
    folder: Annotated[str | None, Query(min_length=1, max_length=service.MAX_FOLDER)] = None,
) -> StreamingResponse:
    """The user's live updates as server-sent events, until the browser goes or its session
    ends. ``account`` and ``folder`` (both, or neither) name the folder the browser views, which
    the worker then keeps fresh."""
    hub: EventHub | None = request.app.state.mail_events
    if hub is None:
        raise Refusal("unavailable", "events_unavailable", _UNAVAILABLE)
    if (account is None) != (folder is None):
        raise Refusal("invalid", "invalid_interest", _HALF_INTEREST)
    interest = None
    if account is not None and folder is not None:
        interest = (account, "INBOX" if folder.upper() == "INBOX" else folder)
    hub.check_capacity(user.id)
    return StreamingResponse(
        _stream(hub, user.id, interest, db, session_cookie),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no"},
    )


async def _stream(
    hub: EventHub, user_id: int, interest: Interest | None, db: Session, token: str | None
) -> AsyncIterator[bytes]:
    # Registered here, not in the route: a body that never starts (the browser gone before it)
    # must not leave its stream behind.
    try:
        stream = hub.open(user_id, interest)
    except Refusal:
        return
    loop = asyncio.get_running_loop()
    try:
        if not await run_in_threadpool(_hold, db, stream, token, announce=True):
            return
        yield _OPEN
        beat_at = loop.time() + HEARTBEAT_SECONDS
        while True:
            try:
                event = await asyncio.wait_for(stream.queue.get(), max(beat_at - loop.time(), 0.0))
            except TimeoutError:
                if not await run_in_threadpool(_hold, db, stream, token, announce=False):
                    return
                yield _HEARTBEAT
                beat_at = loop.time() + HEARTBEAT_SECONDS
                continue
            if event is None or stream.ended:
                return
            yield event.encode()
    finally:
        hub.close_stream(stream)
        held = stream.interest
        if held is not None and not hub.viewing(stream.user_id, held):
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(_drop, db, stream.user_id, held)


def _hold(db: Session, stream: Stream, token: str | None, *, announce: bool) -> bool:
    """Hold (or renew) the stream's interest while the session that opened it lasts; False once
    it has ended (signed out, expired). The session's transaction ends either way, so no
    connection stays checked out while the stream waits."""
    try:
        user = session_user(db, token)
        if user is None or user.id != stream.user_id:
            db.rollback()
            return False
        if stream.interest is not None:
            account, folder = stream.interest
            hold_interest(db, stream.user_id, account, folder, datetime.now(UTC), announce=announce)
        db.commit()
    except Exception as error:
        _interest_failed(db, stream.user_id, error)
    return True


def _drop(db: Session, user_id: int, interest: Interest) -> None:
    try:
        drop_interest(db, user_id, *interest)
        db.commit()
    except Exception as error:
        _interest_failed(db, user_id, error)


def _interest_failed(db: Session, user_id: int, error: Exception) -> None:
    db.rollback()
    logger.warning(
        "mail_interest_failed",
        extra={"event": "mail_interest_failed", "user_id": user_id, "error": type(error).__name__},
    )
