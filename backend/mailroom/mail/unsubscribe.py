"""One-click unsubscribing (RFC 8058): the one request the app sends to a host a message names,
and only when the user asks. The server reads the message itself and checks it
(``mailroom_core.mail.one_click_unsubscribe``); the POST goes through ``safe_fetch`` (no cookies,
no ``Referer``, no redirect). Every attempt, a refused one included, is an audit record
(``unsubscribe``); the log line has the outcome alone, never the URL or the list."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool

from mailroom.accounts.store import load_accounts
from mailroom.audit import Entry, write_records
from mailroom.errors import Refusal
from mailroom.mail import service
from mailroom.net.safe_fetch import FetchError, safe_fetch
from mailroom_core import mail as core

if TYPE_CHECKING:
    from mailroom.context import AppContext
    from mailroom_core.mail import MessageView, OneClickRefusal

logger = logging.getLogger(__name__)

Outcome = Literal["ok", "rejected", "refused", "failed"]
"""``ok``: the list answered 2xx (its page over the cap included). ``rejected``: it answered
another status, a redirect too, which a POST never follows. ``refused``: ``safe_fetch`` would not
send to that URL or address. ``failed``: no answer (timeout, DNS, transport)."""

MAX_ANSWER_BYTES: Final = 64 * 1024
"""Of the list's answer, which is read and thrown away."""
ONE_CLICK_FORM: Final = {"List-Unsubscribe": "One-Click"}
REFUSALS: Final[dict[OneClickRefusal, str]] = {
    "unavailable": "This message offers no one-click unsubscribe.",
    "repeated": "This message has more than one unsubscribe field, so none is used.",
    "not_https": "This message's unsubscribe link is not https.",
    "unverified": "Set the account's authentication server to unsubscribe in one click.",
    "not_signed": "The unsubscribe fields are not signed by a domain the account's server checked.",
}


@dataclass(frozen=True, slots=True)
class Unsubscribed:
    outcome: Outcome
    status: int | None
    """The list's HTTP status, when it answered."""
    list_id: str | None


@dataclass(frozen=True, slots=True)
class _Target:
    view: MessageView
    url: str


def _detail(view: MessageView, **more: object) -> dict[str, object]:
    info = view.headers.mailing_list
    return {
        "folder": view.folder,
        "uid": view.uid,
        "message_id": view.message_id,
        "sender": [a.address for a in view.sender],
        "list_id": None if info is None else info.id,
        **more,
    }


def _record(ctx: AppContext, user_id: int, view: MessageView, result: str, **more: object) -> None:
    entry = Entry(
        actor="user",
        action="unsubscribe",
        account_id=view.account,
        result=result,
        detail=_detail(view, **more),
    )
    write_records(ctx.session_maker, user_id, [entry])


def _log(user_id: int, account_id: int, outcome: str) -> None:
    logger.info(
        "mail_unsubscribed",
        extra={
            "event": "mail_unsubscribed",
            "user_id": user_id,
            "account_id": account_id,
            "outcome": outcome,
        },
    )


def _one_click(ctx: AppContext, user_id: int, view: MessageView) -> core.OneClick:
    authserv_id = load_accounts(ctx.store(user_id)).account(view.account).authserv_id
    return core.one_click_unsubscribe(view.headers, authserv_id)


def offered(ctx: AppContext, user_id: int, view: MessageView) -> bool:
    """Whether the message read as ``view`` may be unsubscribed from in one click."""
    return _one_click(ctx, user_id, view).url is not None


def _target(ctx: AppContext, user_id: int, ref: str) -> _Target:
    """The message ``ref`` names, read now, and its one-click URL; a refusal, recorded, when it
    has none the account's server vouches for."""
    view, account = service.read_headers(ctx, user_id, ref, event="mail_unsubscribe_checked")
    found = core.one_click_unsubscribe(view.headers, account.authserv_id)
    if found.url is None:
        refused = found.refused or "unavailable"
        code = f"unsubscribe_{refused}"
        _record(ctx, user_id, view, code)
        _log(user_id, view.account, code)
        raise Refusal("invalid", code, REFUSALS[refused])
    return _Target(view, found.url)


async def unsubscribe(ctx: AppContext, user_id: int, ref: str) -> Unsubscribed:
    """POST ``List-Unsubscribe=One-Click`` to the message's own one-click URL, for the user."""
    target = await run_in_threadpool(_target, ctx, user_id, ref)
    status: int | None = None
    try:
        fetched = await safe_fetch(target.url, max_bytes=MAX_ANSWER_BYTES, form=ONE_CLICK_FORM)
    except FetchError as error:
        status = error.status
        outcome: Outcome = "refused" if error.problem in ("url", "address") else "failed"
    else:
        status = fetched.status_code
    if status is not None:
        outcome = "ok" if 200 <= status < 300 else "rejected"
    view = target.view
    await run_in_threadpool(
        _record, ctx, user_id, view, outcome, host=urlsplit(target.url).hostname, status=status
    )
    _log(user_id, view.account, outcome)
    info = view.headers.mailing_list
    return Unsubscribed(outcome, status, None if info is None else info.id)
