"""A new message answered with its account's vacation reply: the ``vacation_reply`` job, on the
user's connections from the worker's pool (``docs/requirements/mail-client.md``, *Vacation
reply*).

The job reads the message's header fields and asks mailroom-core's checks (RFC 3834: no list,
bulk or automatic mail, no no-reply or own sender, only mail addressed to one of the account's
identities). A message they let through has its sender claimed in ``app.vacation_replied``,
committed before anything is sent, so a sender gets one reply in ``REPLY_INTERVAL`` however often
the job runs; the reply then goes through the send gate as the user, under a key of its own
message, in every send mode but ``off``. A reply that surely did not leave, refused by the gate or
failed before the server took it, gives the claim back; one whose delivery is unknown keeps it.
Each reply and each refusal is audited ``vacation_reply``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from mailroom.accounts.store import AccountNotFound, load_accounts, user_store
from mailroom.audit import WEB, AuditTrail, Entry, write_records
from mailroom.auth.users import time_zone_of
from mailroom.mail.connections import PASSWORD_MISSING, PasswordMissing, unusable
from mailroom.mail.service._shared import _Accounts
from mailroom.mail.service.send import smtp_sender
from mailroom.mail.vacation import (
    answers_on,
    claim_reply,
    own_addresses,
    release_reply,
    today_of,
    vacation_of,
)
from mailroom_core import mail as core
from mailroom_core import send as gate
from mailroom_core.mail import Addressed, MailError, from_identity, parse_ref

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from mailroom.mail.vacation import VacationSettings
    from mailroom.worker.context import Mailboxes
    from mailroom_core.config import AccountConfig, TimeZoneName
    from mailroom_core.mail import Incoming, Mailbox, MailConnections, MessageRef

logger = logging.getLogger(__name__)

ACTION: Final = "vacation_reply"
OK: Final = "ok"
OFF: Final = "off"
"""Switched off, or past its days, since the message was queued: nothing to record."""
GONE: Final = "gone"
"""Its account removed."""
REPLIED_RECENTLY: Final = "replied_recently"


def reply_vacation(
    sessions: sessionmaker[Session], mailboxes: Mailboxes, user_id: int, account_id: int, ref: str
) -> str:
    """Answer the message ``ref`` with the account's vacation reply; the outcome: ``ok``, ``off``,
    ``gone``, or why it was not answered (a refusal of the checks, ``replied_recently``,
    ``not_found``, ``send_disabled`` or the gate's code)."""
    with sessions() as db:
        settings = vacation_of(db, user_id, account_id)
        answering = answers_on(settings, today_of(db, user_id))
        zone = time_zone_of(db, user_id)
    outcome = OFF
    if answering:
        outcome = _reply(sessions, mailboxes, user_id, account_id, parse_ref(ref), settings, zone)
    logger.info(
        "mail_vacation_reply",
        extra={
            "event": "mail_vacation_reply",
            "user_id": user_id,
            "account_id": account_id,
            "outcome": outcome,
        },
    )
    return outcome


def _record(
    sessions: sessionmaker[Session],
    user_id: int,
    ref: MessageRef,
    result: str,
    sender: str | None = None,
) -> str:
    detail: dict[str, object] = {"folder": ref.folder, "uid": ref.uid}
    if sender is not None:
        detail["from"] = sender
    write_records(
        sessions,
        user_id,
        [Entry(actor="user", action=ACTION, account_id=ref.account, result=result, detail=detail)],
    )
    return result


def _reply(
    sessions: sessionmaker[Session],
    mailboxes: Mailboxes,
    user_id: int,
    account_id: int,
    ref: MessageRef,
    settings: VacationSettings,
    zone: TimeZoneName,
) -> str:
    store = user_store(mailboxes.engine, user_id)
    try:
        refused = unusable(store, mailboxes.subkey, account_id)
    except AccountNotFound:
        return GONE
    if refused is not None:
        return _record(sessions, user_id, ref, refused[0])
    accounts = _Accounts(load_accounts(store))
    account = accounts.config(account_id)
    try:
        with mailboxes.lease(user_id, accounts.revision) as connections:
            deps = gate.GateDeps(
                connections=connections,
                smtp=smtp_sender(store, mailboxes.subkey, mailboxes.ssl_context),
                send_log=store,
                limits=accounts.write_limits,
                time_zone=zone,
            )
            message = _incoming(connections, ref)
            if message is None:
                return _record(sessions, user_id, ref, "not_found")
            own = own_addresses(accounts.loaded.settings.account)
            identities = frozenset(i.address.lower() for i in account.identities)
            refusal = core.vacation_refusal(message, identities=identities, own=own)
            sender = None if message.sender is None else message.sender.address
            if refusal is not None or sender is None:
                return _record(sessions, user_id, ref, refusal or "no_sender", sender)
            if account.send_mode == "off":
                return _record(sessions, user_id, ref, "send_disabled", sender)
            return _send(sessions, deps, account, user_id, ref, message, sender, settings)
    except MailError as error:
        return _record(sessions, user_id, ref, error.code)
    except PasswordMissing:
        return _record(sessions, user_id, ref, PASSWORD_MISSING)


def _incoming(connections: MailConnections, ref: MessageRef) -> Incoming | None:
    try:
        return core.incoming_message(connections, ref)
    except MailError as error:
        if error.code in {"stale_reference", "not_found", "mailbox_missing"}:
            return None
        raise


def _identity(account: AccountConfig, settings: VacationSettings, message: Incoming) -> Mailbox:
    """The identity chosen, while it is still the account's; else the one the message was
    addressed to."""
    addressed = Addressed(to=message.to, cc=message.cc)
    try:
        return from_identity(account, settings.identity, addressed)
    except MailError:
        return from_identity(account, None, addressed)


def _send(
    sessions: sessionmaker[Session],
    deps: gate.GateDeps,
    account: AccountConfig,
    user_id: int,
    ref: MessageRef,
    message: Incoming,
    sender: str,
    settings: VacationSettings,
) -> str:
    now = datetime.now(UTC)
    with sessions() as db:
        if not claim_reply(db, user_id, account.id, sender, now):
            return _record(sessions, user_id, ref, REPLIED_RECENTLY, sender)
    reply = core.vacation_reply(
        message, _identity(account, settings, message), subject=settings.subject, html=settings.html
    )
    trail = AuditTrail(sessions, user_id, WEB, ACTION)
    try:
        admitted = gate.admit(
            deps,
            account,
            reply,
            trail.note,
            by="user",
            key=f"{account.id}/vacation/{core.format_ref(ref)}",
        )
    except MailError as error:
        with sessions() as db:
            release_reply(db, user_id, account.id, sender, now)
        return error.code
    if isinstance(admitted, gate.Earlier):
        return OK if admitted.send.state == "sent" else "delivery_unknown"
    if isinstance(admitted, gate.Drafted):  # only the send mode `off` drafts, refused above
        return "drafted"
    try:
        sent = gate.deliver(deps, admitted, trail.note)
    except MailError as error:
        if error.code != "delivery_unknown":
            # The gate recorded it ``failed``: refused before the server took it (refused
            # connection, failed login, recipients refused), so the sender may be answered later.
            with sessions() as db:
                release_reply(db, user_id, account.id, sender, now)
        return error.code
    except Exception as error:
        # ``deliver`` recorded it ``unknown``: nothing says the reply did not leave.
        logger.warning(
            "mail_vacation_reply_broke",
            extra={"event": "mail_vacation_reply_broke", "error_type": type(error).__name__},
        )
        return "delivery_unknown"
    filed = gate.file_sent(deps, sent, trail.note)
    try:
        deps.send_log.finish_send(
            admitted.send, "sent", {"rejected": list(sent.rejected), "filed": filed}
        )
    except Exception as error:
        logger.warning(
            "mail_send_result_lost",
            extra={"event": "mail_send_result_lost", "error_type": type(error).__name__},
        )
    return OK
