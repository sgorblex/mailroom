"""An account's folders as the filter engine runs on them, over mailroom-core's connections.

A run is one `MailConnections.with_mailbox` session: its folder selected once (read-only for a
dry run), every filter searching, fetching and acting through `mailroom_core.mail` on that one
connection. The connections are the host's (`connections_from` for plain config, the app's pool):
pooled, retried, interruptible, with STARTTLS and AUTH PLAIN.
"""

from __future__ import annotations

import imaplib
import logging
from typing import TYPE_CHECKING

from mailroom_core.filters.filter import Filter
from mailroom_core.filters.message import Message
from mailroom_core.filters.path import Path
from mailroom_core.filters.pattern import MatchTimeoutError
from mailroom_core.filters.report import summaries
from mailroom_core.filters.selected import Selected
from mailroom_core.filters.spec.nodes import NotSerializableError
from mailroom_core.mail import (
    MailError,
    fetch_envelopes,
    fetch_sources,
    parsed_message,
    search_uids,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from mailroom_core.filters.report import FilterReport, FolderReport, MessageSummary
    from mailroom_core.filters.spec.nodes import SpecNode
    from mailroom_core.mail import MailboxContext, MailConnections

_SAMPLE = 10
"""UIDs a log line lists at most."""
_UNDELETED = "UNDELETED"
"""ANDed into every search: a message marked ``\\Deleted`` (an original a move copied but could
not expunge) never matches again, so no run acts on it twice."""


class _Escaped(BaseException):
    """A non-mail exception (a pattern's `MatchTimeoutError`, a bug) carried out of
    `with_mailbox`, which would otherwise report it as the server's `server_error`. A
    BaseException, so the session drops its connection, whose state is then unknown."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error


def _is_mail_error(error: Exception) -> bool:
    """Whether `error` is the server's or the connection's, for `with_mailbox` to map. A
    `MatchTimeoutError` is a TimeoutError, which is an OSError, but it is the engine's."""
    if isinstance(error, MatchTimeoutError):
        return False
    return isinstance(error, MailError | imaplib.IMAP4.error | OSError | EOFError)


class Folder:
    """An account plus a folder path; supports run(filters) and path navigation."""

    path: Path
    account: Account

    def __init__(self, account: Account, path: Path) -> None:
        """Build a Folder from an account and a path."""
        self.path = path
        self.account = account
        self.log = account.log.getChild("/".join(path.segments) or "root")

    def imap_name(self) -> str:
        """The folder's name on the server: its segments joined with the account's delimiter
        (asked of the server once, on a connection of the pool, then cached)."""
        return self.path.as_string(self.account.delimiter() or ".")

    def __str__(self) -> str:
        return f"{self.account.account_id}.{'/'.join(self.path.segments)}"

    def run(
        self,
        filter_or_filters: Filter | Iterable[Filter],
        *,
        dry_run: bool = False,
        newest: int | None = None,
    ) -> FolderReport:
        """Run one or more filters on this folder, in one session of the account's connections.

        The folder is selected once (EXAMINE for a dry run, so nothing can change) and every
        filter searches, fetches and acts on that connection, in order: an earlier filter's
        action (a move, say) changes what a later one finds. A dry run applies no action, so its
        filters all see the folder as it is.

        Criteria with is_efficient=True (e.g. FromIs, ToIs, SubjectContains) are answered by
        the server's SEARCH alone. The others fetch the candidates' source (`BODY.PEEK[]`,
        which never sets ``\\Seen``), parse it with ``mailroom_core.mail.parsed_message`` and
        match it here; a message is fetched once per run, and fetched again only after an action
        of the run acted on it.

        Every search skips messages marked ``\\Deleted``. ``newest`` (at least 1, else a
        ValueError) bounds every search to the folder's newest ``newest`` messages as it was when
        selected, resolved to their UIDs first, so an earlier filter's move leaves the window in
        place and later mail stays out; the report's ``limited_to_newest`` says so when the
        folder held more.

        Returns a report (see :mod:`mailroom_core.filters.report`): per filter its spec,
        the matched messages (UID, From, Subject, Date, fetched as ENVELOPE)
        and whether its action was executed. Mail errors are `mailroom_core.mail.MailError`s
        (a dry run that lost its connection is retried once); anything else, such as a
        pattern's `MatchTimeoutError`, is raised as it is. Either way the report of the filters
        before it is then lost (``run_into`` keeps it).
        """
        report = self.new_report(dry_run)
        self.run_into(report, filter_or_filters, dry_run=dry_run, newest=newest)
        return report

    def new_report(self, dry_run: bool) -> FolderReport:
        """Return an empty report for a run of this folder."""
        return {
            "account": self.account.account_id,
            "folder": list(self.path.segments),
            "dry_run": dry_run,
            "filters": [],
            "error": None,
        }

    def run_into(
        self,
        report: FolderReport,
        filter_or_filters: Filter | Iterable[Filter],
        *,
        dry_run: bool,
        newest: int | None = None,
    ) -> None:
        """Like run(), appending each filter's report to report as soon as it finishes.

        For callers that need the partial report when the run raises.
        """
        if newest is not None and newest < 1:
            msg = f"newest must be at least 1, not {newest}"
            raise ValueError(msg)
        filters = (
            [filter_or_filters]
            if isinstance(filter_or_filters, Filter)
            else list(filter_or_filters)
        )
        if not filters:
            return
        # Before the session: asking it inside would need a second connection of the account.
        delimiter = self.account.delimiter()
        path = self.path.as_string(delimiter or ".")
        start = len(report["filters"])

        def session(context: MailboxContext) -> None:
            # A read-only attempt that lost its connection is run again from the start.
            del report["filters"][start:]
            report.pop("limited_to_newest", None)
            try:
                self._run_all(report, filters, Selected(context, delimiter), dry_run, newest)
            except Exception as error:
                if _is_mail_error(error):
                    raise
                raise _Escaped(error) from None

        self.log.info("running %s filter(s)", len(filters))
        try:
            self.account.connections.with_mailbox(self.account.account_id, path, dry_run, session)
        except _Escaped as escaped:
            raise escaped.error from None

    def _run_all(
        self,
        report: FolderReport,
        filters: list[Filter],
        selected: Selected,
        dry_run: bool,
        newest: int | None,
    ) -> None:
        context = selected.context
        exists = context.exists
        window: list[object] = []
        if newest is not None and exists > newest:
            # An explicit range: `*` would also take the last message when the folder is empty.
            # Sequence numbers shift when an earlier filter expunges, UIDs never do.
            in_range = f"{exists - newest + 1}:{exists}"
            newest_uids = search_uids(context, [in_range])
            window = [in_range]
            if newest_uids:
                window = ["UID", f"{min(newest_uids)}:{max(newest_uids)}"]
            report["limited_to_newest"] = newest
        fetched: dict[int, Message] = {}
        for f in filters:
            report["filters"].append(self._run_one(f, selected, dry_run, window, fetched))

    def _run_one(
        self,
        f: Filter,
        selected: Selected,
        dry_run: bool,
        window: list[object],
        fetched: dict[int, Message],
    ) -> FilterReport:
        spec: SpecNode | None
        try:
            spec = f.to_spec()
        except NotSerializableError:
            spec = None
        result: FilterReport = {
            "spec": spec,
            "action": repr(f.action) if spec is not None else str(f.action),
            "matched": [],
            "executed": False,
        }
        context = selected.context
        uids = search_uids(context, [*window, _UNDELETED, *f.criterion.imap_query.criteria()])
        if not f.criterion.is_efficient:
            uids = self._select(f, context, uids, fetched)
        if not uids:
            self.log.debug("no messages matched for action %s", f.action)
            return result
        result["matched"] = self._summaries(context, uids)
        sample = uids[:_SAMPLE]
        if dry_run:
            self.log.info(
                "[Dry-Run] would select %s message(s) and execute %s: %s",
                len(uids),
                f.action,
                sample,
            )
            return result
        self.log.info("selected %s message(s): %s", len(uids), sample)
        f.action.execute(selected, uids)
        self.log.info("executed %s", f.action)
        result["executed"] = True
        for uid in uids:
            fetched.pop(uid, None)
        return result

    def _select(
        self, f: Filter, context: MailboxContext, uids: list[int], fetched: dict[int, Message]
    ) -> list[int]:
        """The candidates `uids` that `f`'s criterion selects once fetched and parsed."""
        missing = [uid for uid in uids if uid not in fetched]
        if missing:
            for uid, message in fetch_sources(context, missing).items():
                try:
                    parsed = parsed_message(message.source)
                except Exception as error:
                    self.log.debug("uid %s: parse error: %s", uid, type(error).__name__)
                    continue
                fetched[uid] = Message(uid, parsed, [flag.encode() for flag in message.flags])
        candidates = {uid: fetched[uid] for uid in uids if uid in fetched}
        if len(candidates) < len(uids):
            skipped = [uid for uid in uids if uid not in candidates]
            self.log.warning(
                "Skipping %s message(s) that could not be fetched or parsed: %s",
                len(skipped),
                skipped[:_SAMPLE],
            )
        return list(f.criterion.select(candidates))

    def _summaries(self, context: MailboxContext, uids: list[int]) -> list[MessageSummary]:
        """The matched messages' summaries; a refused ENVELOPE fetch leaves them their UIDs
        only, since the report must not block the action."""
        try:
            envelopes = fetch_envelopes(context, uids)
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error as error:
            self.log.warning("ENVELOPE fetch failed: %s", type(error).__name__)
            envelopes = {}
        return summaries(uids, envelopes)

    def __truediv__(self, path: str) -> Folder:
        return Folder(self.account, (self.path / path))


class Account:
    """One account of a `mailroom_core.mail.MailConnections`, as the filter engine sees it.

    Builds nothing: the connections are the host's (`mailroom_core.mail.connections_from` for
    plain config), with their pool, TLS and login rules. A run takes one connection of the
    account for its whole length, so with a pool of one, two runs on one account take turns
    (the second waits up to `Timeouts.lock`, then fails `busy`).
    """

    def __init__(self, connections: MailConnections, account_id: str) -> None:
        """The account `account_id` of `connections`."""
        self.connections = connections
        self.account_id = account_id
        self.log: logging.Logger = logging.getLogger(__name__).getChild(account_id)
        self.inbox = Folder(self, Path("INBOX"))

    def delimiter(self) -> str | None:
        """The server's hierarchy delimiter (None without a hierarchy), asked once, then cached
        until the account's connections are closed."""
        return self.connections.delimiter(self.account_id)

    def folders(self) -> list[Path]:
        r"""The paths of the folders that can hold mail, in mailroom-core's order (special-use
        first, then the rest by path), each split on its own delimiter, so ``account / path``
        selects it again. Folders flagged ``\Noselect`` or ``\NonExistent`` are left out."""
        return [
            Path(folder.path.split(folder.delimiter) if folder.delimiter else [folder.path])
            for folder in self.connections.folders(self.account_id)
            if folder.selectable
        ]

    def __truediv__(self, path: str | Path) -> Folder:
        return Folder(self, path if isinstance(path, Path) else Path(path))

    def __str__(self) -> str:
        return self.account_id
