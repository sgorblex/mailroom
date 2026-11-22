"""The addresses a recipient field suggests as the user types (``mailroom.mail.index.addresses``),
from the index only: the server is asked just which of each account's indexed folders are its Sent
and Junk (one folder list per account, at most ``LISTING_SECONDS`` for all, each kept for
``ROLES_SECONDS`` so typing does not list again). An account that cannot say in time gives no sent
folder, so its suggestions are the people who wrote to the user, Junk's included."""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Final

from mailroom.errors import Refusal
from mailroom.mail.connections import PasswordMissing
from mailroom.mail.index import addresses
from mailroom.mail.index import reads as index_reads
from mailroom.mail.service._shared import _Accounts, _accounts, _run
from mailroom_core.mail import MailError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from mailroom.accounts.service import Via
    from mailroom.context import AppContext
    from mailroom.mail.index.addresses import Suggestion
    from mailroom_core.mail import MailConnections

logger = logging.getLogger(__name__)

LISTING_SECONDS: Final = 5.0
"""How long the folder lists may take, all together: a slower server's Sent is left out."""
ROLES_SECONDS: Final = 300.0
"""How long an account's listed roles are reused, under the same settings revision."""
MAX_PREFIX: Final = 200

_roles: dict[tuple[int, int, int], tuple[float, dict[str, str]]] = {}
"""``(user_id, account, revision)`` to when the listing expires and its roles; only listings that
answered are kept."""
_roles_lock = threading.Lock()


def _known_roles(user_id: int, revision: int, used: Sequence[int]) -> dict[int, dict[str, str]]:
    now = time.monotonic()
    with _roles_lock:
        kept = {account: _roles.get((user_id, account, revision)) for account in used}
    return {account: entry[1] for account, entry in kept.items() if entry and entry[0] > now}


def _keep_roles(user_id: int, revision: int, listed: dict[int, dict[str, str]]) -> None:
    now = time.monotonic()
    with _roles_lock:
        for key in [key for key, (until, _) in _roles.items() if until <= now]:
            del _roles[key]
        for account, roles in listed.items():
            _roles[(user_id, account, revision)] = (now + ROLES_SECONDS, roles)


def folder_roles(
    ctx: AppContext, user_id: int, accounts: _Accounts, used: Sequence[int]
) -> dict[int, dict[str, str]]:
    """Each account's folders that have a role, by path: kept from a listing in the last
    ``ROLES_SECONDS``, else listed now. An account that cannot be listed is left out, and so is
    each one not listed yet when the lists take over ``LISTING_SECONDS``."""
    found = _known_roles(user_id, accounts.revision, used)
    missing = [account for account in used if account not in found]
    if not missing:
        return found
    listed: dict[int, dict[str, str]] = {}
    lock = threading.Lock()

    def work(connections: MailConnections) -> None:
        for account in missing:
            try:
                folders = connections.folders(account)
            except MailError, PasswordMissing:
                continue
            roles: dict[str, str] = {f.path: f.role for f in folders if f.role is not None}
            with lock:
                listed[account] = roles

    # A refusal (the deadline, mostly) leaves the accounts that answered in time.
    with contextlib.suppress(Refusal):
        _run(ctx, user_id, accounts, "mail_addresses", missing, work, deadline=LISTING_SECONDS)
    with lock:
        answered = dict(listed)
    _keep_roles(user_id, accounts.revision, answered)
    return found | answered


def suggest_addresses(
    ctx: AppContext,
    db: Session,
    user_id: int,
    prefix: str,
    *,
    limit: int = addresses.MAX_SUGGESTIONS,
    via: Via = "web",
) -> list[Suggestion]:
    """Up to ``limit`` addresses starting with ``prefix`` (``addresses.suggest``) from the user's
    indexed, ready folders, their own identities (every account's) left out."""
    started = time.monotonic()
    accounts = _accounts(ctx, user_id)
    identities = {
        identity.address.strip().lower()
        for account in accounts.ids()
        for identity in accounts.config(account).identities
    }
    ready = index_reads.ready_rows(db, user_id, accounts.ids())
    found: list[Suggestion] = []
    if ready and prefix.strip():
        roles = folder_roles(ctx, user_id, accounts, sorted({account for account, _ in ready}))
        role = {row.id: roles.get(account, {}).get(path) for (account, path), row in ready.items()}
        sent = [folder for folder, kind in role.items() if kind == "sent"]
        inbound = [folder for folder, kind in role.items() if kind != "junk"]
        found = addresses.suggest(
            db,
            user_id,
            prefix,
            folders=list(role),
            sent=sent,
            inbound=inbound,
            identities=identities,
            limit=limit,
        )
    logger.info(
        "mail_addresses_suggested",
        extra={
            "event": "mail_addresses_suggested",
            "user_id": user_id,
            "via": via,
            "count": len(found),
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return found
