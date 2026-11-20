"""The action that moves messages to a folder of another account, once the run's session closed."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import batched
from typing import TYPE_CHECKING, ClassVar

from mailroom_core.filters.actions.action import Action
from mailroom_core.filters.spec.nodes import PATH, codecs
from mailroom_core.mail import (
    MAX_REFS,
    MailError,
    MessageRef,
    format_ref,
    mail_error,
    move_to_account,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from mailroom_core.filters.path import Path
    from mailroom_core.filters.report import TransferredMessage, TransferReport
    from mailroom_core.filters.selected import Selected
    from mailroom_core.mail import MailConnections, OrganizeResult

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AccountTarget:
    """How a run moves messages to another account: connections serving both that account and
    the run's, and the size a moved message may have (`move_to_account`'s `max_bytes`)."""

    connections: MailConnections
    max_bytes: int


type AccountResolver = Callable[[int], AccountTarget]
"""The host's lookup of the account a `MoveToAccount` names, when the run moves to it: raises
`MailError` when the run may not move there."""


class MoveToAccount(Action):
    """Action that moves messages to `folder` of another account, `account`.

    During the run it only records the matched messages, which later filters of the run then no
    longer match, as after a move. Once the run's session has closed, they are moved by
    `mailroom_core.mail.move_to_account`, the original going to its account's Trash; the outcome
    per message goes into the filter's report (`transfers`). A message with no Message-ID or over
    the size cap is refused on its own, with its `reason`, and the others move. The folder's
    segments are joined with the target account's delimiter. The connections and the size cap
    are the host's, which `accounts` looks up (`load_filter(..., accounts=...)`); without it
    every message fails `unsupported`.
    """

    spec_type = "move_to_account"
    spec_codecs = codecs(folder=PATH)
    spec_host_args: ClassVar[frozenset[str]] = frozenset({"accounts"})

    def __init__(
        self, account: int, folder: Path, *, accounts: AccountResolver | None = None
    ) -> None:
        """Initialize a MoveToAccount action.

        Args:
            account: The id of the account to move to.
            folder: Its destination folder, resolved with that account's delimiter.
            accounts: The host's lookup of the account, when the run moves to it.

        Raises:
            TypeError: account is not an integer.
            ValueError: account is below 1.

        """
        if not isinstance(account, int) or isinstance(account, bool):
            raise TypeError(f"account must be an integer, got {type(account).__name__}")
        if account < 1:
            raise ValueError("account must be at least 1")
        self._account = account
        self._folder = folder
        self._accounts = accounts
        super().__init__(self._record, name=f"move to {folder.as_string()} in account {account}")

    def _record(self, selected: Selected, uids: Sequence[int]) -> None:
        context = selected.context
        refs = [
            format_ref(MessageRef(context.account, context.path, context.uid_validity, uid))
            for uid in uids
        ]
        taken = list(uids)
        selected.defer(taken, lambda: self._transfer(taken, refs))

    def _transfer(self, uids: list[int], refs: list[str]) -> TransferReport:
        report: TransferReport = {
            "account": self._account,
            "folder": list(self._folder.segments),
            "messages": [],
            "new_refs": [],
            "error": None,
        }
        noted: set[int] = set()
        try:
            if self._accounts is None:
                raise MailError("unsupported", "Moves to another account are not available here.")
            target = self._accounts(self._account)
            delimiter = target.connections.delimiter(self._account)
            folder = self._folder.as_string(delimiter or ".")
            for chunk in batched(refs, MAX_REFS, strict=False):
                result = move_to_account(
                    target.connections,
                    chunk,
                    self._account,
                    folder,
                    removal="trash",
                    max_bytes=target.max_bytes,
                    refuse_each=True,
                )
                _note(report, result, noted)
        except Exception as exc:
            # Logged by `mail_error`, never in the report: the transfers after it still run.
            error = mail_error(exc, self._account)
            report["error"] = report["error"] or f"{error.code}: {error.message}"
            report["messages"].extend(
                {"uid": uid, "result": error.code} for uid in uids if uid not in noted
            )
        moved = sum(message["result"] == "ok" for message in report["messages"])
        log.info("moved %s of %s message(s) to account %s", moved, len(uids), self._account)
        return report


def _note(report: TransferReport, result: OrganizeResult, noted: set[int]) -> None:
    """`result`'s outcome per original message (why, for one refused on its own), its copies'
    refs and its first error, a refusal of one message included."""
    reasons = {uid: error for group in result.groups for uid, error in group.refused}
    for item in result.items:
        if item.source_account is not None or item.uid is None:
            continue  # a copy's item, in the target account
        message: TransferredMessage = {"uid": item.uid, "result": item.result}
        if item.removed is not None:
            message["removed"] = item.removed
        if item.copied is not None:
            message["copied"] = item.copied
        if item.uid in reasons:
            message["reason"] = reasons[item.uid].detail or reasons[item.uid].message
        report["messages"].append(message)
        noted.add(item.uid)
    for group in result.groups:
        report["new_refs"].extend(group.new_refs or ())
        for error in (group.error, *(error for _uid, error in group.refused)):
            if error is not None and report["error"] is None:
                report["error"] = f"{error.code}: {error.message}"
