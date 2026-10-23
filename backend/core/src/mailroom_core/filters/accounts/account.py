"""Email account management and IMAP connection handling."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from imapclient import IMAPClient

from imap_thingy.core import Message, Path
from imap_thingy.core.spec import NotSerializableError
from imap_thingy.filters.filter import Filter
from imap_thingy.get_mail import fetch_mail, search_mail, summarize_mail

if TYPE_CHECKING:
    import ssl
    from collections.abc import Iterable

    from imap_thingy.core.spec import SpecNode
    from imap_thingy.report import FilterReport, FolderReport


_UNSELECTABLE = frozenset({b"\\noselect", b"\\nonexistent"})


class Folder:
    """An account plus a folder path; supports run(filter) and path navigation."""

    path: Path
    account: Account

    def __init__(self, account: Account, path: Path) -> None:
        """Build a Folder from an account and a path."""
        self.path = path
        self.account = account
        self.log = account.log.getChild(self.imap_name())

    def imap_name(self) -> str:
        """Return the IMAP folder name (e.g. INBOX or delimiter-joined path)."""
        return self.path.as_string(self.account.delimiter)

    def __str__(self) -> str:
        return f"{self.account.name}.{self.imap_name()}"

    def connect(self, readonly: bool = False) -> IMAPClient:
        """Open IMAP connection and select this folder."""
        conn = self.account.connect()
        conn.select_folder(self.imap_name(), readonly=readonly)
        return conn

    def run(
        self,
        filter_or_filters: Filter | Iterable[Filter],
        dry_run: bool = False,
    ) -> FolderReport:
        """Run one or more filters on this folder using a shared IMAP connection.

        A single IMAP connection is opened and this folder is selected once per
        call to run(). All filters passed in filter_or_filters are then executed
        sequentially using that same connection and session state, before the
        connection is logged out.

        Because the connection is shared, actions executed by earlier filters
        can affect subsequent filters via changes to server or session state
        (for example, moving messages to other folders).

        Criteria with is_efficient=True (e.g. FromIs, ToIs, SubjectContains)
        use IMAP SEARCH only and do not fetch message bodies. Criteria that
        need parsed headers or bodies (e.g. SubjectIs with SUBJECT prefilter,
        SubjectMatches with regex, BodyMatches) use a best-effort per-run fetch
        cache: messages are fetched once when first
        needed and kept in memory until evicted. Messages that were acted on
        are evicted after each action and may be fetched again later in the
        same run if selected by another filter.

        A dry run selects the folder read-only and applies no action. Its filters
        all see the folder as it is, so a message an earlier filter would have
        moved can also be reported by a later one.

        Returns a report (see :mod:`imap_thingy.report`): per filter its spec,
        the matched messages (UID, From, Subject, Date, fetched as ENVELOPE)
        and whether its action was executed. An exception from the server or an
        action propagates; the report of the filters before it is then lost
        (``run_into`` keeps it).
        """
        report = self.new_report(dry_run)
        self.run_into(report, filter_or_filters, dry_run=dry_run)
        return report

    def new_report(self, dry_run: bool) -> FolderReport:
        """Return an empty report for a run of this folder."""
        return {
            "account": self.account.name,
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
    ) -> None:
        """Like run(), appending each filter's report to report as soon as it finishes.

        For callers that need the partial report when the run raises.
        """
        filters = (
            [filter_or_filters]
            if isinstance(filter_or_filters, Filter)
            else list(filter_or_filters)
        )
        if not filters:
            return
        conn = self.connect(readonly=dry_run)
        fetched: dict[int, Message] = {}
        self.log.info("running %s filter(s)", len(filters))
        try:
            for f in filters:
                report["filters"].append(self._run_one(f, dry_run, conn, fetched))
        finally:
            try:
                conn.logout()
                self.log.debug("Disconnected from %s:%s", self.account.host, self.account.port)
            except Exception as exc:
                self.log.debug("Logout failed: %s", exc, exc_info=True)

    def _run_one(
        self, f: Filter, dry_run: bool, conn: IMAPClient, fetched: dict[int, Message]
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
        selected_msg_ids = search_mail(conn, f.criterion.imap_query)
        if f.criterion.is_efficient and selected_msg_ids:
            self.log.debug(
                "IMAP-only (efficient), no fetch for %s message(s)", len(selected_msg_ids)
            )
        if not f.criterion.is_efficient:
            to_fetch = [i for i in selected_msg_ids if i not in fetched]
            self.log.debug(
                "cache: %s candidates, %s in cache, fetching %s",
                len(selected_msg_ids),
                len(selected_msg_ids) - len(to_fetch),
                len(to_fetch),
            )
            if to_fetch:
                added = fetch_mail(conn, to_fetch)
                fetched.update(added)
                self.log.debug("fetched %s message(s), cache size %s", len(added), len(fetched))
            messages_this = {i: fetched[i] for i in selected_msg_ids if i in fetched}
            if len(messages_this) < len(selected_msg_ids):
                missing_ids = [i for i in selected_msg_ids if i not in messages_this]
                sample_size = 10
                sample = missing_ids[:sample_size]
                truncated = " (first 10 shown)" if len(missing_ids) > sample_size else ""
                self.log.warning(
                    "Skipping %s message(s) that could not be fetched or parsed%s. Sample: %s",
                    len(missing_ids),
                    truncated,
                    sample,
                )
            selected_msg_ids = list(f.criterion.select(messages_this).keys())
        if selected_msg_ids:
            result["matched"] = summarize_mail(conn, selected_msg_ids)
            _sample_size = 10
            _sample = selected_msg_ids[:_sample_size]
            _truncated = (
                f" (first {len(_sample)} shown)" if len(selected_msg_ids) > _sample_size else ""
            )
            if dry_run:
                self.log.info(
                    "[Dry-Run] would select %s message(s)%s and execute %s: %s",
                    len(selected_msg_ids),
                    _truncated,
                    f.action,
                    _sample,
                )
            else:
                self.log.info(
                    "selected %s message(s)%s: %s",
                    len(selected_msg_ids),
                    _truncated,
                    _sample,
                )
                f.action.execute(
                    conn,
                    selected_msg_ids,
                    delimiter=self.account.delimiter,
                )
                self.log.info("executed %s", f.action)
                result["executed"] = True
                evicted_count = 0
                for i in selected_msg_ids:
                    if fetched.pop(i, None) is not None:
                        evicted_count += 1
                if evicted_count:
                    self.log.debug(
                        "evicted %s message(s) from cache (acted on), cache size %s",
                        evicted_count,
                        len(fetched),
                    )
        else:
            self.log.debug("no messages matched for action %s", f.action)
        return result

    def __truediv__(self, path: str) -> Folder:
        return Folder(self.account, (self.path / path))


class Account:
    """Represents an email account with IMAP connection management.

    Handles connection creation, reuse, and cleanup for IMAP operations.
    """

    def __init__(
        self,
        name: str,
        host: str,
        port: int,
        username: str,
        password: str,
        address: str | None = None,
        delimiter: str = ".",
        ssl_context: ssl.SSLContext | None = None,
        timeout: float | None = None,
    ) -> None:
        """Initialize an email account.

        Args:
            name: Display name for the account.
            host: IMAP server hostname.
            port: IMAP server port.
            username: IMAP username.
            password: IMAP password.
            address: Email address (defaults to username if not provided).
            delimiter: Character used to delimit folder subdirectories (default: ".").
            ssl_context: TLS settings for the connection, e.g. to trust a private CA
                (default: imapclient's, which verifies against the system CAs).
            timeout: Seconds to wait for the connection and for each socket read or
                write before failing with a socket timeout (default: wait forever).

        """
        self.name = name
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.address = address if address is not None else username
        self.delimiter = delimiter
        self.ssl_context = ssl_context
        self.timeout = timeout
        self.log: logging.Logger = logging.getLogger(__name__).getChild(self.name)
        self.inbox = Folder(self, Path("INBOX"))

    def connect(self) -> IMAPClient:
        """Create a new IMAP connection. Caller must call conn.logout() when done."""
        conn = IMAPClient(
            self.host, self.port, ssl=True, ssl_context=self.ssl_context, timeout=self.timeout
        )
        conn.login(self.username, self.password)
        self.log.debug("Connected to %s:%s", self.host, self.port)
        return conn

    def list_folders(self) -> list[Path]:
        r"""Return the paths of the folders that can hold mail, in the server's order.

        Names are split on the account's ``delimiter``, so ``account / path`` selects the listed
        folder again; a server reporting another delimiter is logged as a misconfiguration. A
        folder with no reported delimiter (a flat namespace) is a single segment. Folders flagged
        ``\Noselect`` or ``\NonExistent`` (e.g. bare parents of other folders) are left out.
        """
        conn = self.connect()
        try:
            listed = conn.list_folders()
        finally:
            try:
                conn.logout()
            except Exception as exc:
                self.log.debug("Logout failed: %s", exc, exc_info=True)
        paths: list[Path] = []
        server_delimiters: set[str] = set()
        for flags, delimiter, name in listed:
            if {f.lower() for f in flags} & _UNSELECTABLE:
                continue
            if delimiter is None:
                paths.append(Path([name]))
                continue
            server_delimiters.add(delimiter.decode())
            paths.append(Path(name.split(self.delimiter)))
        if server_delimiters - {self.delimiter}:
            self.log.warning(
                "server folder delimiter %s differs from the account's %r",
                ", ".join(map(repr, sorted(server_delimiters))),
                self.delimiter,
            )
        return paths

    def __truediv__(self, path: str | Path) -> Folder:
        return Folder(self, path if isinstance(path, Path) else Path(path))

    def __str__(self) -> str:
        return self.name
