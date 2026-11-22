"""Filters that move to another account (the filter engine's ``move_to_account``): the target
checked when a filter is saved, a rule set enabled, and the run moves (``RunTargets``); the
ledger of the copies those moves made, which the target's runs leave alone (the loop guard); the
run's audit records of each message; and what removing the target account does.

Specification: ``docs/requirements/filters.md``, *Moves to another account*.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from mailroom.accounts.store import AccountNotFound, load_accounts
from mailroom.audit import Entry
from mailroom.errors import Refusal
from mailroom.mail.connections import unusable
from mailroom.models import Filter, RuleSet, TransferLedger
from mailroom_core.filters import AccountTarget
from mailroom_core.mail import MailError, parse_ref

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from sqlalchemy.orm import Session

    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.mail import MailConnections
    from mailroom_core.store import AccountStore

logger = logging.getLogger(__name__)

ACTION: Final = "move_to_account"
LEDGER_DAYS: Final = 90
"""A copy stays in the ledger this long, unless its folder's UIDVALIDITY changes first."""

TARGET_NOT_FOUND: Final = "move_target_not_found"
SAME_ACCOUNT: Final = "move_target_same_account"
IN_BOTH: Final = "in_both_accounts"
"""Why a rule set whose moves left messages in both accounts was turned off."""
IN_BOTH_TEXT: Final = (
    "Some messages were copied to another account but not removed from here, so they are in "
    "both: check both accounts before you enable it again."
)
TARGET_REMOVED: Final = "move_target_removed"
TARGET_REMOVED_TEXT: Final = (
    "A filter moves mail to an account that was removed: change or remove it before you enable "
    "the rule set again."
)


def targets(node: object) -> set[int]:
    """The accounts a ``move_to_account`` anywhere in the spec ``node`` moves to."""
    if isinstance(node, list):
        return set().union(*(targets(item) for item in node))
    if not isinstance(node, dict):
        return set()
    found: set[int] = set()
    args = node.get("args")
    if node.get("type") == ACTION and isinstance(args, dict):
        account = args.get("account")
        if isinstance(account, int) and not isinstance(account, bool):
            found.add(account)
    for value in node.values():
        found |= targets(value)
    return found


def check_targets(store: AccountStore, spec: Mapping[str, Any], account_id: int | None) -> None:
    """Refuse a filter spec that moves to an account that is not the user's
    (``move_target_not_found``), or to its rule set's own, which a rule set for all accounts
    (``account_id`` None) runs on whatever the target (``move_target_same_account``)."""
    named = targets(spec.get("action"))
    if not named:
        return
    if account_id is None:
        msg = (
            "A rule set for all accounts also runs on the account it would move to: put the "
            "move in a rule set of each account instead."
        )
        raise Refusal("invalid", SAME_ACCOUNT, msg)
    if account_id in named:
        msg = "A filter cannot move mail to the account its rule set runs on: use move_to."
        raise Refusal("invalid", SAME_ACCOUNT, msg)
    owned = {account.id for account in load_accounts(store).settings.account}
    if not named <= owned:
        msg = "A filter can move mail only to another of your accounts."
        raise Refusal("invalid", TARGET_NOT_FOUND, msg)


def check_rule_set_targets(
    store: AccountStore, rule_set: RuleSet, filters: Sequence[Filter]
) -> None:
    """``check_targets`` for each of ``filters``, the enabled ones of the rule set being turned
    on: a target removed since one was saved."""
    for row in filters:
        if row.enabled:
            check_targets(store, row.spec, rule_set.account_id)


class RunTargets:
    """A real run's ``AccountResolver``: checks the target again when the move is made (the
    user's, not the run's own account, able to log in) and gives the run's ``connections``, the
    user's move cap (``max_bytes``) and ``copied``, which writes the copies to the ledger."""

    def __init__(
        self,
        store: AccountStore,
        subkey: SecretsSubkey | None,
        connections: MailConnections,
        account_id: int,
        max_bytes: int,
        copied: Callable[[Sequence[str]], None],
    ) -> None:
        self._store = store
        self._subkey = subkey
        self._connections = connections
        self._account_id = account_id
        self._max_bytes = max_bytes
        self._copied = copied

    def __call__(self, target: int) -> AccountTarget:
        if target == self._account_id:
            raise MailError("invalid_argument", "A filter cannot move to the account it runs on.")
        try:
            refused = unusable(self._store, self._subkey, target)
        except AccountNotFound:
            raise MailError("invalid_argument", "The account it moves to is gone.") from None
        if refused is not None:
            raise MailError("auth_failed", refused[1])
        return AccountTarget(self._connections, self._max_bytes, copied=self._copied)


_INBOX: Final = re.compile(r"^inbox(?=$|[/.])", re.IGNORECASE)
"""A leading INBOX segment, in any case, before the usual delimiters."""


def _ledger_folder(path: str) -> str:
    """``path`` with a leading INBOX segment in capitals, as the folder's every name is one."""
    return _INBOX.sub("INBOX", path, count=1)


def ledger_copies(db: Session, user_id: int, refs: Sequence[str]) -> None:
    """Add the messages ``refs`` name to the user's ledger (a move's copies, or snoozed messages
    brought back, ``mailroom.worker.snoozes``), and forget the entries older than
    ``LEDGER_DAYS``. Commits ``db``. Raises ``MailError`` (``unsupported``) when the database
    refuses: the moves left then stop, a copy unrecorded being one a run could move on."""
    rows = []
    for text in refs:
        ref = parse_ref(text)
        rows.append(
            {
                "user_id": user_id,
                "account_id": ref.account,
                "folder": _ledger_folder(ref.folder),
                "uid_validity": ref.uid_validity,
                "uid": ref.uid,
            }
        )
    try:
        if rows:
            db.execute(insert(TransferLedger).values(rows).on_conflict_do_nothing())
        cutoff = datetime.now(UTC) - timedelta(days=LEDGER_DAYS)
        db.execute(
            delete(TransferLedger).where(
                TransferLedger.user_id == user_id, TransferLedger.created_at < cutoff
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "ledger_write_failed",
            extra={"event": "ledger_write_failed", "user_id": user_id, "error": type(exc).__name__},
        )
        msg = "The copies could not be recorded, so the moves left were stopped."
        raise MailError("unsupported", msg) from None


def ledger_skip(
    db: Session, user_id: int, account_id: int, path: str, uid_validity: int
) -> set[int]:
    """The UIDs of ``path`` of the account a filter's move copied there: every run there leaves
    them alone. The folder's entries of another UIDVALIDITY go (its UIDs name other messages
    now). Commits ``db``."""
    folder = _ledger_folder(path)
    of_folder = (
        TransferLedger.user_id == user_id,
        TransferLedger.account_id == account_id,
        TransferLedger.folder == folder,
    )
    current = TransferLedger.uid_validity == uid_validity
    db.execute(delete(TransferLedger).where(*of_folder, ~current))
    uids = set(db.scalars(select(TransferLedger.uid).where(*of_folder, current)))
    db.commit()
    return uids


def rename_in_ledger(
    db: Session, user_id: int, account_id: int, old: str, new: str, delimiter: str | None
) -> None:
    """Point the account's ledger entries of the folder ``old``, and of every folder under it, at
    ``new``, renamed. The caller commits."""
    if _ledger_folder(old) == _ledger_folder(new):
        return
    of_account = (TransferLedger.user_id == user_id, TransferLedger.account_id == account_id)
    db.execute(
        update(TransferLedger)
        .where(*of_account, TransferLedger.folder == _ledger_folder(old))
        .values(folder=_ledger_folder(new))
    )
    if delimiter:
        prefix = _ledger_folder(old) + delimiter
        renamed = _ledger_folder(new) + delimiter
        db.execute(
            update(TransferLedger)
            .where(*of_account, func.starts_with(TransferLedger.folder, prefix))
            .values(folder=renamed + func.substr(TransferLedger.folder, len(prefix) + 1))
        )


def forget_target(db: Session, user_id: int, account_id: int) -> list[int]:
    """The account is being removed: turn off the user's other rule sets with a filter moving to
    it (``move_target_removed``) and forget its ledger entries. The ids turned off; the caller
    commits."""
    rule_sets = db.scalars(
        select(RuleSet)
        .where(
            RuleSet.user_id == user_id,
            RuleSet.kind == "user",
            RuleSet.account_id.is_distinct_from(account_id),
        )
        .order_by(RuleSet.id)
        .with_for_update()
    ).all()
    by_id = {rule_set.id: rule_set for rule_set in rule_sets}
    targeting: set[int] = set()
    if by_id:
        for row in db.scalars(select(Filter).where(Filter.rule_set_id.in_(by_id))):
            if account_id in targets(row.spec.get("action")):
                targeting.add(row.rule_set_id)
    for rule_set_id in sorted(targeting):
        rule_set = by_id[rule_set_id]
        rule_set.enabled = False
        rule_set.disabled_reason = f"{TARGET_REMOVED}: {TARGET_REMOVED_TEXT}"
    db.execute(
        delete(TransferLedger).where(
            TransferLedger.user_id == user_id, TransferLedger.account_id == account_id
        )
    )
    return sorted(targeting)


def left_in_both(result: Mapping[str, Any]) -> bool:
    """Whether a run's moves to another account left a message in both accounts: copied, then
    not removed from where it was."""
    return any(
        message.get("removed") == "failed"
        for entry in result.get("filters", [])
        for transfer in entry.get("transfers", [])
        for message in transfer.get("messages", [])
    )


def moves_only(entry: Mapping[str, Any]) -> bool:
    """Whether a filter's action is a move to another account alone, whose records are then its
    transfers' (``transfer_records``)."""
    spec = entry.get("spec")
    action = spec.get("action") if isinstance(spec, dict) else None
    return isinstance(action, dict) and action.get("type") == ACTION


def transfer_records(
    entry: Mapping[str, Any],
    about: Mapping[str, Any],
    summaries: Mapping[int, Mapping[str, Any]],
    *,
    account_id: int,
    rule_set_id: int,
    run_id: int,
) -> Iterator[Entry]:
    """A filter's moves to another account, for the audit trail as the mail client's are: per
    message its result (``ok`` or the error code, a refusal of the target included), how it
    left (``removed``), whether it was ``copied`` when it failed and why one was refused on its
    own (``reason``), under the run's account; each copy made, under the target's.
    ``summaries``: the matched messages' sender, date and subject hash by UID."""
    record = partial(Entry, actor="filter", action=ACTION, rule_set_id=rule_set_id, run_id=run_id)
    for transfer in entry.get("transfers", []):
        target = transfer.get("account")
        destination = "/".join(transfer.get("folder") or [])
        moved = {"destination": destination, "destination_account": target}
        for message in transfer.get("messages", []):
            uid = message.get("uid")
            detail: dict[str, Any] = {**about, **summaries.get(uid, {"uid": uid}), **moved}
            for key in ("removed", "copied", "reason"):
                if key in message:
                    detail[key] = message[key]
            yield record(account_id=account_id, result=str(message.get("result")), detail=detail)
        for text in transfer.get("new_refs", []):
            try:
                ref = parse_ref(text)
            except MailError:
                continue
            detail = {
                "folder": ref.folder,
                "uid": ref.uid,
                "source_account": account_id,
                "source_folder": about.get("folder"),
                "filter_id": about.get("filter_id"),
                "filter": about.get("filter"),
            }
            yield record(account_id=ref.account, result="ok", detail=detail)
