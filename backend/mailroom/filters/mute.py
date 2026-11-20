"""Muted threads (``app.muted_thread``): a conversation the user muted on one account, whose new
messages in INBOX arrive marked read; nothing is moved.

Muting rides on the account's filter runs: the account's one hidden rule set of kind ``mute``, on
INBOX and triggered by new mail, made enabled with the first muted thread and removed with the
last, holds one filter, ``MUTE_SPEC``: an unread message of a muted thread is marked read. It runs
only while the account's Run filters is on, as any rule set. Its ``in_thread`` names ``THREAD``,
which a run resolves to every muted thread of the account (``ThreadJoins``); each message it
matched then joins its thread by its Message-ID, so later replies that cite only it are found, in
the same run too. A run reads INBOX's newest ``NEWEST`` messages only. Never by subject.

Specification: ``docs/requirements/filters.md`` (*Muted threads*).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from mailroom.accounts import app_data
from mailroom.audit import WEB, Entry, Origin, write_records
from mailroom.errors import Refusal
from mailroom.mail import service as mail_service
from mailroom.models import Filter, MutedThread, RuleSet
from mailroom_core.filters import Criterion, ThreadMembers
from mailroom_core.filters import Filter as EngineFilter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from mailroom.accounts.service import Via
    from mailroom.context import AppContext
    from mailroom_core.filters import Message

logger = logging.getLogger(__name__)

MAX_IDS: Final = 200
"""Message-IDs kept per muted thread: the first (its root, as far as the muted message knew),
then the newest."""
THREAD: Final = "muted"
"""The ``in_thread`` name of the mute rule set's filter: every muted thread of its account."""
MUTE_SPEC: Final[dict[str, Any]] = {
    "criterion": {
        "and": [{"type": "in_thread", "args": {"thread": THREAD}}, {"type": "is_unread"}]
    },
    "action": {"type": "mark_as_read"},
}
"""Unread only, so a message already read is neither touched nor reported again."""
RULE_SET_NAME: Final = "Muted threads"
NEWEST: Final = 500
"""Of INBOX's messages, the newest a mute run reads: each new mail runs it, and its criterion has
no server search to narrow the unread ones it fetches."""
_MAX_GMAIL_THREAD: Final = 2**63 - 1
"""A Gmail thread id past ``bigint`` is not kept: the thread is then muted by its ids only."""


class MutedThreadOut(BaseModel):
    id: int
    account_id: int
    ids: int
    """The Message-IDs it is known by (at most ``MAX_IDS``)."""
    gmail: bool
    """Known by its Gmail thread too."""
    run_filters: bool
    """The account's Run filters: off, muting has no effect until it is turned on."""
    created_at: datetime


def capped(ids: Sequence[str]) -> list[str]:
    """``ids`` each once, at most ``MAX_IDS``: the first, then the newest."""
    unique = list(dict.fromkeys(ids))
    if len(unique) <= MAX_IDS:
        return unique
    return [unique[0], *unique[len(unique) - MAX_IDS + 1 :]]


def _out(row: MutedThread, run_filters: dict[int, bool]) -> MutedThreadOut:
    return MutedThreadOut(
        id=row.id,
        account_id=row.account_id,
        ids=len(row.message_ids),
        gmail=row.gm_thrid is not None,
        run_filters=run_filters.get(row.account_id, False),
        created_at=row.created_at,
    )


def _log(event: str, user_id: int, account_id: int, muted_thread_id: int, via: Via) -> None:
    logger.info(
        event,
        extra={
            "event": event,
            "user_id": user_id,
            "account_id": account_id,
            "muted_thread_id": muted_thread_id,
            "via": via,
        },
    )


def _lock_account(db: Session, user_id: int, account_id: int) -> None:
    """Hold the account's muting until commit: a mute and an unmute of its last thread take
    turns, so its rule set never outlives its threads or is missing beside one."""
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"mailroom.mute:{user_id}:{account_id}"},
    )


def _ensure_rule_set(db: Session, user_id: int, account_id: int) -> None:
    """The account's mute rule set with its one filter, made enabled when it has none."""
    made = db.scalar(
        insert(RuleSet)
        .values(
            user_id=user_id,
            account_id=account_id,
            folder=["INBOX"],
            name=RULE_SET_NAME,
            trigger="new_mail",
            marker="none",
            enabled=True,
            kind="mute",
        )
        .on_conflict_do_nothing(
            index_elements=[RuleSet.user_id, RuleSet.account_id],
            index_where=RuleSet.kind == "mute",
        )
        .returning(RuleSet.id)
    )
    if made is not None:
        db.add(Filter(rule_set_id=made, position=0, name=RULE_SET_NAME, spec=MUTE_SPEC))


def mute(
    ctx: AppContext, db: Session, user_id: int, ref: str, *, origin: Origin = WEB
) -> MutedThreadOut:
    """Mute the conversation of the message ``ref`` names, on its account: by the ids its headers
    tie it to its conversation with (``mailroom_core.mail.thread_key``), and on Gmail its thread.
    A thread already muted that shares one of them takes the others instead, and threads it
    bridges become the oldest of them. Refused
    ``nothing_to_mute`` for a message with neither; the read's refusals as reading the message's."""
    via = origin.via
    message, key = mail_service.thread_key(ctx, user_id, ref, via=via)
    gmail = key.gmail_thread
    if gmail is not None and not 0 <= gmail <= _MAX_GMAIL_THREAD:
        gmail = None
    if not key.message_ids and gmail is None:
        msg = "This message has no Message-ID to follow."
        raise Refusal("invalid", "nothing_to_mute", msg)
    account_id = message.account
    _lock_account(db, user_id, account_id)
    threads = db.scalars(
        select(MutedThread)
        .where(MutedThread.user_id == user_id, MutedThread.account_id == account_id)
        .order_by(MutedThread.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    seeds = set(key.message_ids)
    matching = [
        thread
        for thread in threads
        if (gmail is not None and thread.gm_thrid == gmail)
        or seeds.intersection(thread.message_ids)
    ]
    merged = matching[1:]
    if not matching:
        row = MutedThread(
            user_id=user_id,
            account_id=account_id,
            message_ids=capped(key.message_ids),
            gm_thrid=gmail,
        )
        db.add(row)
    else:
        row = matching[0]
        bridged = [i for thread in merged for i in thread.message_ids]
        row.message_ids = capped([*row.message_ids, *bridged, *key.message_ids])
        row.gm_thrid = next(
            (thread.gm_thrid for thread in (row, *merged) if thread.gm_thrid is not None), gmail
        )
        for thread in merged:
            db.delete(thread)
    _ensure_rule_set(db, user_id, account_id)
    db.flush()
    entry = Entry(
        actor=origin.actor,
        action="mute",
        account_id=account_id,
        result="ok",
        detail={
            "muted_thread_id": row.id,
            "folder": message.folder,
            "uid": message.uid,
            **({"merged": [thread.id for thread in merged]} if merged else {}),
        },
        client_id=origin.client_id,
        grant_id=origin.grant_id,
    )
    db.commit()
    # Before the session reads again: no transaction of it stays open around the audit write.
    write_records(ctx.session_maker, user_id, [entry])
    db.refresh(row)
    _log("thread_muted", user_id, account_id, row.id, via)
    return _out(row, app_data.run_filters_by_account(db, user_id))


def unmute(
    ctx: AppContext, db: Session, user_id: int, muted_thread_id: int, *, origin: Origin = WEB
) -> None:
    """Unmute the user's muted thread, or ``not_found``; the account's mute rule set goes with
    its last one."""
    account_id = db.scalar(
        select(MutedThread.account_id).where(
            MutedThread.id == muted_thread_id, MutedThread.user_id == user_id
        )
    )
    if account_id is None:
        raise _not_found()
    _lock_account(db, user_id, account_id)
    removed = db.scalar(
        delete(MutedThread)
        .where(MutedThread.id == muted_thread_id, MutedThread.user_id == user_id)
        .returning(MutedThread.id)
    )
    if removed is None:  # unmuted meanwhile
        db.rollback()
        raise _not_found()
    left = db.scalar(
        select(func.count()).where(
            MutedThread.user_id == user_id, MutedThread.account_id == account_id
        )
    )
    if not left:
        db.execute(
            delete(RuleSet).where(
                RuleSet.user_id == user_id,
                RuleSet.account_id == account_id,
                RuleSet.kind == "mute",
            )
        )
    db.commit()
    entry = Entry(
        actor=origin.actor,
        action="unmute",
        account_id=account_id,
        result="ok",
        detail={"muted_thread_id": muted_thread_id},
        client_id=origin.client_id,
        grant_id=origin.grant_id,
    )
    write_records(ctx.session_maker, user_id, [entry])
    _log("thread_unmuted", user_id, account_id, muted_thread_id, origin.via)


def _not_found() -> Refusal:
    return Refusal("not_found", "muted_thread_not_found", "No such muted thread")


def muted_threads(db: Session, user_id: int, account_id: int | None = None) -> list[MutedThreadOut]:
    """The user's muted threads, oldest first; with ``account_id``, that account's only."""
    query = select(MutedThread).where(MutedThread.user_id == user_id)
    if account_id is not None:
        query = query.where(MutedThread.account_id == account_id)
    rows = db.scalars(query.order_by(MutedThread.id))
    run_filters = app_data.run_filters_by_account(db, user_id)
    return [_out(row, run_filters) for row in rows]


class ThreadJoins:
    """One run of a mute rule set: its account's muted threads as its ``in_thread``'s members
    (``members``), and each message the run matched (``watched``), whose Message-ID then joins
    the threads it belongs to (``save``). An unread message citing one the run matched before it
    matches too, so a reply that came with its parent is not left for a later run."""

    def __init__(self, threads: Sequence[MutedThread]) -> None:
        self._threads = {row.id: (frozenset(row.message_ids), row.gm_thrid) for row in threads}
        self._matched: list[tuple[frozenset[str], int | None, str]] = []
        self._joined: set[str] = set()

    @classmethod
    def of(cls, db: Session, user_id: int, account_id: int) -> ThreadJoins:
        return cls(
            db.scalars(
                select(MutedThread).where(
                    MutedThread.user_id == user_id, MutedThread.account_id == account_id
                )
            ).all()
        )

    def members(self, name: str) -> ThreadMembers | None:
        """``THREAD``'s members: every muted thread's ids and Gmail threads together."""
        if name != THREAD or not self._threads:
            return None
        return ThreadMembers(
            message_ids=frozenset().union(*(ids for ids, _ in self._threads.values())),
            gmail_threads=frozenset(gm for _, gm in self._threads.values() if gm is not None),
        )

    def watched(self, filters: Sequence[EngineFilter]) -> list[EngineFilter]:
        """``filters``, each noting the messages it matches."""
        return [self._watched(each) for each in filters]

    def _watched(self, ran: EngineFilter) -> EngineFilter:
        inner = ran.criterion

        def func(msg: Message) -> bool:
            parsed = msg.parsed
            cited = frozenset((*parsed.references, *parsed.in_reply_to))
            joins = b"\\Seen" not in msg.flags and not self._joined.isdisjoint(cited)
            if not (inner.func(msg) or joins):
                return False
            if parsed.message_id is not None:
                self._matched.append((cited, msg.gmail_thread, parsed.message_id))
                self._joined.add(parsed.message_id)
            return True

        return EngineFilter(Criterion(func, inner.imap_query, inner.is_efficient), ran.action)

    def save(self, db: Session) -> None:
        """Add each matched message's Message-ID to the threads it cites one of or shares the
        Gmail thread of (``capped``); a thread unmuted meanwhile is left out. The caller
        commits."""
        if not self._matched:
            return
        rows = db.scalars(
            select(MutedThread)
            .where(MutedThread.id.in_(self._threads))
            .order_by(MutedThread.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
        for row in rows:
            known = set(row.message_ids)
            joined: list[str] = []
            for cited, gmail, own in self._matched:
                in_thread = (row.gm_thrid is not None and gmail == row.gm_thrid) or bool(
                    known.intersection(cited)
                )
                if in_thread and own not in known:
                    joined.append(own)
                    known.add(own)
            if joined:
                row.message_ids = capped([*row.message_ids, *joined])
