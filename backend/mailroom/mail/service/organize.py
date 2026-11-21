"""Organizing batches of refs: moves, within an account or to another, archiving, reporting as
spam, trashing and flag changes."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Final, cast

from mailroom.audit import WEB, AuditTrail, Origin
from mailroom.errors import Refusal, mail_refusal
from mailroom.mail.index import reads as index_reads
from mailroom.mail.index import writes as index_writes
from mailroom.mail.service._shared import (
    _Accounts,
    _accounts,
    _index_read_failed,
    _ref_accounts,
    _run,
    _Stoppable,
    _usable,
)
from mailroom_core import mail as core
from mailroom_core.mail import MailError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from mailroom.context import AppContext
    from mailroom_core.mail import (
        MailConnections,
        MessageRef,
        OrganizeResult,
        TransferRemoval,
    )

logger = logging.getLogger(__name__)

MAX_REFS: Final = core.MAX_REFS
"""The most refs one organize call takes."""
MOVE_TO_ACCOUNT_SECONDS: Final = 55.0
"""A move to another account's deadline, longer than the pool's (it reads, writes and removes
each message whole) but within the web container's 60 s proxy read timeout. One cut off is safe
to repeat: what reached the target is not copied again."""


def _organize(
    ctx: AppContext,
    user_id: int,
    event: str,
    action: str,
    origin: Origin,
    refs: Sequence[str],
    work: Callable[[MailConnections, _Accounts, Sequence[str]], OrganizeResult],
    *,
    target: int | None = None,
    deadline: float | None = None,
    source_folder: str | None = None,
) -> OrganizeResult:
    """``work`` on the batch ``refs`` once each ref is checked (``_ref_accounts``), and the
    ``target`` account it writes to besides theirs, logged with its accounts, counts and failure
    codes only: never a folder, a ref or a message's data, which go to the audit trail as
    ``action`` (the result's items, each under its own account), recorded by the thread that did
    the work. A call cut at the deadline (``deadline`` seconds, else the pool's) writes no group
    after the cut (``_Stoppable``); the ones done before it stay done, and are recorded once that
    thread finishes. The mail index is brought in step with the result
    (``mailroom.mail.index.writes``) once the call answered, outside its deadline; by that thread
    when the deadline cut the call, as no one waits for it then. ``source_folder``: the folder the
    user acted from, where a Gmail label view's refs are acted on (``_from_label_views``);
    ``work`` is given the refs it acts on."""
    via = origin.via
    trail = AuditTrail(ctx.session_maker, user_id, origin, action)
    accounts = _accounts(ctx, user_id)
    used = _ref_accounts(ctx, user_id, accounts, refs)
    if target is not None:
        _usable(ctx, user_id, accounts, target)
        used = [*used, target] if target not in used else used
    started = time.monotonic()
    result: OrganizeResult | None = None
    outcome: str = "ok"
    stop = threading.Event()
    handing = threading.Lock()
    handed: list[tuple[OrganizeResult, list[str]]] = []
    """The work's result and the refs it acted on, once handed back in time: the caller brings
    the index in step."""

    def index(done: OrganizeResult, acting: Sequence[str]) -> None:
        index_writes.organized(ctx.session_maker, user_id, done.items, done.groups, refs=acting)

    def act(connections: MailConnections) -> OrganizeResult:
        stoppable = cast("MailConnections", _Stoppable(connections, stop))
        acting = _from_label_views(ctx, user_id, stoppable, refs, source_folder)
        done = work(stoppable, accounts, acting)
        trail.notes(done.items)
        with handing:
            if not stop.is_set():
                handed.append((done, acting))
                return done
        index(done, acting)
        return done

    def cut() -> None:
        with handing:
            stop.set()

    try:
        result = _run(
            ctx,
            user_id,
            accounts,
            event,
            used,
            act,
            on_deadline=cut,
            deadline=deadline,
        )
        outcome = result.outcome
        return result
    except Refusal as refusal:
        outcome = refusal.code
        raise
    finally:
        if handed:
            # Also when the deadline passed just after the work handed its result back.
            index(*handed[0])
        groups = () if result is None else result.groups
        logger.info(
            event,
            extra={
                "event": event,
                "user_id": user_id,
                "via": via,
                "account_ids": used,
                "ref_count": len(refs),
                "changed": sum(group.count for group in groups),
                "groups": len(groups),
                "failed_groups": sum(not group.ok for group in groups),
                "codes": sorted({group.error.code for group in groups if group.error is not None}),
                "outcome": outcome,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )


def move(
    ctx: AppContext,
    user_id: int,
    refs: Sequence[str],
    folder: str,
    *,
    source_folder: str | None = None,
    origin: Origin = WEB,
) -> OrganizeResult:
    """Moves the messages ``refs`` name (1 to ``MAX_REFS``, any folders and accounts) to
    ``folder`` of each one's account, never Drafts or Sent: mailroom-core's ``move_messages``,
    one result per source folder. ``source_folder``: the folder the user acted from
    (``_from_label_views``)."""
    return _organize(
        ctx,
        user_id,
        "mail_moved",
        "move",
        origin,
        refs,
        lambda connections, _, acting: core.move_messages(connections, acting, folder),
        source_folder=source_folder,
    )


def move_to_account(
    ctx: AppContext,
    user_id: int,
    refs: Sequence[str],
    account: int,
    folder: str,
    removal: TransferRemoval,
    *,
    origin: Origin = WEB,
) -> OrganizeResult:
    """Moves the messages ``refs`` name (1 to ``MAX_REFS``, in any of the user's accounts but
    ``account``) to ``folder`` of ``account``, another of the user's, never Drafts or Sent:
    mailroom-core's ``move_to_account``, each message at most the user's ``move_max_mb``. The
    originals go to Trash, or with ``removal="delete"`` are deleted for good once their copies
    are confirmed. Each message moved is recorded in both accounts."""
    try:
        return _organize(
            ctx,
            user_id,
            "mail_moved_to_account",
            "move_to_account",
            origin,
            refs,
            lambda connections, accounts, acting: core.move_to_account(
                connections, acting, account, folder, removal=removal, max_bytes=accounts.move_cap
            ),
            target=account,
            deadline=MOVE_TO_ACCOUNT_SECONDS,
        )
    except Refusal as refusal:
        # A refused delete for good is on record too (the deletion safeguards); a timeout's
        # groups record themselves once their thread finishes.
        if removal == "delete" and refusal.code != "timeout":
            _record_refused(ctx, user_id, origin, refs, account, folder, refusal.code)
        raise


def _record_refused(
    ctx: AppContext,
    user_id: int,
    origin: Origin,
    refs: Sequence[str],
    account: int,
    folder: str,
    code: str,
) -> None:
    """A record per ref of the user's own accounts for a move to ``account`` refused whole."""
    owned = _accounts(ctx, user_id)
    items: list[core.AuditItem] = []
    for text in refs:
        try:
            ref = core.parse_ref(text)
        except MailError:
            continue
        if owned.has(ref.account):
            items.append(
                core.AuditItem(
                    account=ref.account,
                    folder=ref.folder,
                    uid=ref.uid,
                    destination=folder,
                    destination_account=account,
                    result=cast("core.AuditResult", code),
                )
            )
    AuditTrail(ctx.session_maker, user_id, origin, "move_to_account").notes(items)


def archive(
    ctx: AppContext,
    user_id: int,
    refs: Sequence[str],
    *,
    source_folder: str | None = None,
    origin: Origin = WEB,
) -> OrganizeResult:
    """Moves them to each account's folder with role ``archive``: ``unsupported`` for an
    account's group when it has none. ``source_folder`` as ``move``'s."""
    return _organize(
        ctx,
        user_id,
        "mail_archived",
        "archive",
        origin,
        refs,
        lambda connections, _, acting: core.archive_messages(connections, acting),
        source_folder=source_folder,
    )


def junk(
    ctx: AppContext,
    user_id: int,
    refs: Sequence[str],
    *,
    source_folder: str | None = None,
    origin: Origin = WEB,
) -> OrganizeResult:
    """Reports them as spam: moves them to each account's folder with role ``junk``, teaching
    the spam filter; ``unsupported`` for an account's group when it has none. ``source_folder``
    as ``move``'s."""
    return _organize(
        ctx,
        user_id,
        "mail_junked",
        "junk",
        origin,
        refs,
        lambda connections, _, acting: core.junk_messages(connections, acting),
        source_folder=source_folder,
    )


def trash(
    ctx: AppContext,
    user_id: int,
    refs: Sequence[str],
    *,
    source_folder: str | None = None,
    origin: Origin = WEB,
) -> OrganizeResult:
    """Moves them to each account's Trash; never a permanent delete, so a group already in Trash
    is refused. ``source_folder`` as ``move``'s."""
    return _organize(
        ctx,
        user_id,
        "mail_trashed",
        "trash",
        origin,
        refs,
        lambda connections, _, acting: core.trash_messages(connections, acting),
        source_folder=source_folder,
    )


def flag(
    ctx: AppContext,
    user_id: int,
    refs: Sequence[str],
    *,
    seen: bool | None = None,
    flagged: bool | None = None,
    to_sort: bool | None = None,
    origin: Origin = WEB,
) -> OrganizeResult:
    """Sets (True) or clears (False) ``\\Seen``, ``\\Flagged`` and the To sort mark, at least
    one of them (else refused before anything connects), and reads them back; the refs stay
    valid. To sort is ``unsupported`` in a folder that keeps no keywords."""
    if seen is None and flagged is None and to_sort is None:
        neither = MailError("invalid_argument", "Give `seen`, `flagged`, `to_sort` or several.")
        raise mail_refusal(neither.code, neither.message)
    return _organize(
        ctx,
        user_id,
        "mail_flagged",
        "flag",
        origin,
        refs,
        lambda connections, _, acting: core.set_flags(
            connections, acting, seen=seen, flagged=flagged, to_sort=to_sort
        ),
    )


def _from_label_views(
    ctx: AppContext,
    user_id: int,
    connections: MailConnections,
    refs: Sequence[str],
    source_folder: str | None,
) -> list[str]:
    """``refs`` with each one naming an indexed Gmail All Mail row (the ref a label view lists)
    replaced by the message's ref in ``source_folder``, the folder of the view acted from (INBOX
    or a label's), found by its X-GM-MSGID, one session per account: acted on there, it leaves
    that view only, its other labels staying. Without ``source_folder``, or from All Mail
    itself, the refs stay as given; so does one whose message ``source_folder`` does not hold,
    and so do they all when the index cannot be read."""
    if source_folder is None:
        return list(refs)
    path = "INBOX" if source_folder.upper() == "INBOX" else source_folder
    parsed = [core.parse_ref(text) for text in refs]
    try:
        with ctx.session_maker() as db:
            found = index_reads.gmail_ids(db, user_id, [r for r in parsed if r.folder != path])
    except Exception as exc:
        _index_read_failed(user_id, exc)
        return list(refs)
    wanted: dict[int, list[int]] = {}
    for ref, gmail_id in found.items():
        wanted.setdefault(ref.account, []).append(gmail_id)
    resolved: dict[int, Mapping[int, MessageRef]] = {}
    for account, gmail_ids in wanted.items():
        try:
            resolved[account] = core.gmail_label_refs(connections, account, path, gmail_ids)
        except MailError:
            resolved[account] = {}
    acting: list[str] = []
    for text, ref in zip(refs, parsed, strict=True):
        known = found.get(ref)
        moved = None if known is None else resolved[ref.account].get(known)
        acting.append(text if moved is None else core.format_ref(moved))
    return acting
