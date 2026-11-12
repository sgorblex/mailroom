"""The filter engine on real mail, against the test Dovecot, with nothing but mailroom-core: the
connections come from plain config (`connections_from` and a settings dict), with no app and no
database. This is the test that keeps "core filters on its own" (docs/core.md, *Public API*)."""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import pytest

from mailroom_core.filters import (
    Account,
    Delete,
    Filter,
    FromIs,
    IsUnread,
    MarkAsRead,
    MatchTimeoutError,
    MoveTo,
    Path,
    Star,
    SubjectContains,
    SubjectMatches,
    Trash,
    get_match_timeout,
    monitor,
    run_rules,
    set_match_timeout,
    starred_only,
)
from mailroom_core.mail import MailError, connections_from
from mailroom_core.testing import (
    create_folders,
    folder_contents,
    mime,
    seed,
    unverified_context,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from mailroom_core.mail import MailConnections
    from mailroom_core.testing import ImapTestServer

pytestmark = pytest.mark.integration

SHOP = "Shop <news@shop.example>"
SPAM = "Prize <win@spam.example>"
OLD = "Old <old@list.example>"
FRIEND = "Ada <ada@friend.example>"
WAIT_SECONDS = 20.0


def settings(user: str, server: ImapTestServer) -> dict[str, object]:
    """The settings the app stores, as `tomllib` would read them from a plain config file."""
    return {
        "account": [
            {
                "id": "personal",
                "login": user,
                "send_mode": "drafts",
                "auth": {"type": "password"},
                "imap": {"host": server.host, "port": server.port, "tls": "implicit"},
                "smtp": {"host": "127.0.0.1", "port": 9, "tls": "implicit"},
                "identities": [{"address": f"{user}@example.test"}],
            }
        ]
    }


@pytest.fixture
def mail(imap_server: ImapTestServer) -> Iterator[Callable[..., Account]]:
    made: list[MailConnections] = []

    def make(user: str, *, pool_size: int = 1) -> Account:
        connections = connections_from(
            settings(user, imap_server),
            {"personal": imap_server.password},
            ssl_context=unverified_context(),
            pool_size=pool_size,
        )
        made.append(connections)
        return Account(connections, "personal")

    yield make
    for connections in made:
        connections.close_all()


def subjects(server: ImapTestServer, user: str, folder: str) -> list[str]:
    return [m.subject for m in folder_contents(server, user, folder)]


def flags(server: ImapTestServer, user: str, folder: str) -> dict[str, set[str]]:
    return {m.subject: set(m.flags) for m in folder_contents(server, user, folder)}


def test_a_run_moves_trashes_deletes_and_flags(imap_server: ImapTestServer, mail) -> None:
    user = imap_server.unique_user("engine")
    account = mail(user)
    nested = Path("Lists") / "Nerds"
    create_folders(imap_server, user, "Lists", nested.as_string(account.delimiter() or "."))
    seed(imap_server, user, "INBOX", mime("Sale", sender=SHOP))
    seed(imap_server, user, "INBOX", mime("You won", sender=SPAM))
    seed(imap_server, user, "INBOX", mime("Old news", sender=OLD))
    seed(imap_server, user, "INBOX", mime("Lunch?", sender=FRIEND))
    seed(imap_server, user, "Trash", mime("Already gone", sender=OLD), flags=["\\Deleted"])

    filters = [
        Filter(FromIs("news@shop.example"), MoveTo(nested)),
        Filter(FromIs("win@spam.example"), Trash()),
        Filter(FromIs("old@list.example"), Delete()),
        Filter(FromIs("ada@friend.example") & IsUnread(), MarkAsRead() + Star()),
    ]
    report = account.inbox.run(filters)

    assert [[m["subject"] for m in f["matched"]] for f in report["filters"]] == [
        ["Sale"],
        ["You won"],
        ["Old news"],
        ["Lunch?"],
    ]
    assert all(f["executed"] for f in report["filters"])
    assert report["folder"] == ["INBOX"]
    assert report["filters"][0]["matched"][0]["from"] == SHOP
    assert subjects(imap_server, user, "INBOX") == ["Lunch?"]
    assert flags(imap_server, user, "INBOX")["Lunch?"] >= {"\\Seen", "\\Flagged"}
    assert subjects(imap_server, user, nested.as_string(account.delimiter() or ".")) == ["Sale"]
    # The delete expunged exactly its message: the one already marked in Trash stays.
    assert subjects(imap_server, user, "Trash") == ["Already gone", "You won"]


def test_starred_only_moves_starred_mail_and_unstars_it(imap_server: ImapTestServer, mail) -> None:
    user = imap_server.unique_user("starred")
    create_folders(imap_server, user, "Shop")
    seed(imap_server, user, "INBOX", mime("Sale", sender=SHOP))
    seed(imap_server, user, "INBOX", mime("Picks", sender=SHOP), flags=["\\Flagged"])
    account = mail(user)

    rules = starred_only(
        [(account.inbox, [Filter(FromIs("news@shop.example"), MoveTo(Path("Shop")))])]
    )
    [report] = run_rules(rules, dry_run=False)

    assert [m["subject"] for m in report["filters"][0]["matched"]] == ["Picks"]
    assert subjects(imap_server, user, "INBOX") == ["Sale"]
    assert "\\Flagged" not in flags(imap_server, user, "Shop")["Picks"]


def test_a_dry_run_is_read_only_and_bounded_to_the_newest(
    imap_server: ImapTestServer, mail
) -> None:
    user = imap_server.unique_user("dry")
    for n in range(5):
        seed(imap_server, user, "INBOX", mime(f"Saldi perché {n}", sender=SHOP))
    account = mail(user)

    filters = [
        Filter(SubjectContains("perché"), Delete()),
        Filter(SubjectMatches(r"Saldi .* [0-9]"), MoveTo(Path("Trash"))),
    ]
    report = account.inbox.run(filters, dry_run=True, newest=3)

    newest = ["Saldi perché 2", "Saldi perché 3", "Saldi perché 4"]
    assert report["limited_to_newest"] == 3
    assert [[m["subject"] for m in f["matched"]] for f in report["filters"]] == [newest, newest]
    assert not any(f["executed"] for f in report["filters"])
    assert len(subjects(imap_server, user, "INBOX")) == 5
    assert all("\\Seen" not in f for f in flags(imap_server, user, "INBOX").values())

    whole = account.inbox.run(filters, dry_run=True, newest=5)
    assert "limited_to_newest" not in whole
    assert len(whole["filters"][0]["matched"]) == 5


def test_a_real_run_bounded_to_the_newest_keeps_its_window_after_a_move(
    imap_server: ImapTestServer, mail
) -> None:
    """The first filter's move expunges inside the window: the second still sees exactly the
    newest messages that are left, by UID, not the sequence numbers that shifted."""
    user = imap_server.unique_user("window")
    for subject in ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"):
        seed(imap_server, user, "INBOX", mime(subject, sender=SHOP))
    account = mail(user)

    filters = [
        Filter(SubjectContains("Echo"), MoveTo(Path("Trash"))),
        Filter(FromIs("news@shop.example"), Star()),
    ]
    report = account.inbox.run(filters, newest=3)

    assert report["limited_to_newest"] == 3
    assert [[m["subject"] for m in f["matched"]] for f in report["filters"]] == [
        ["Echo"],
        ["Delta", "Foxtrot"],
    ]
    starred = {s for s, f in flags(imap_server, user, "INBOX").items() if "\\Flagged" in f}
    assert starred == {"Delta", "Foxtrot"}
    assert subjects(imap_server, user, "Trash") == ["Echo"]


def test_run_into_keeps_the_report_of_a_run_that_stops(imap_server: ImapTestServer, mail) -> None:
    """A move into Drafts is refused, as the mail client refuses it, after the first filter."""
    user = imap_server.unique_user("partial")
    seed(imap_server, user, "INBOX", mime("Sale", sender=SHOP))
    account = mail(user)
    first = Filter(FromIs("news@shop.example"), MarkAsRead())
    report = account.inbox.new_report(dry_run=False)

    with pytest.raises(MailError) as raised:
        account.inbox.run_into(
            report,
            [first, Filter(FromIs("news@shop.example"), MoveTo(Path("Drafts")))],
            dry_run=False,
        )

    assert raised.value.code == "invalid_argument"
    assert [f["spec"] for f in report["filters"]] == [first.to_spec()]
    assert subjects(imap_server, user, "INBOX") == ["Sale"]
    assert subjects(imap_server, user, "Drafts") == []


def test_a_move_to_a_missing_folder_is_mailbox_missing(imap_server: ImapTestServer, mail) -> None:
    user = imap_server.unique_user("missing")
    seed(imap_server, user, "INBOX", mime("Sale", sender=SHOP))
    with pytest.raises(MailError) as raised:
        mail(user).inbox.run(Filter(FromIs("news@shop.example"), MoveTo(Path("Nowhere"))))
    assert raised.value.code == "mailbox_missing"
    assert subjects(imap_server, user, "INBOX") == ["Sale"]


@pytest.fixture
def quick_match_timeout() -> Iterator[None]:
    before = get_match_timeout()
    set_match_timeout(0.05)
    yield
    set_match_timeout(before)


@pytest.mark.usefixtures("quick_match_timeout")
def test_a_pattern_out_of_time_is_raised_as_itself(imap_server: ImapTestServer, mail) -> None:
    """Not as the server's error: `with_mailbox` would make any exception a `server_error`."""
    user = imap_server.unique_user("timeout")
    seed(imap_server, user, "INBOX", mime("a" * 60 + "b", sender=SHOP))
    account = mail(user)
    with pytest.raises(MatchTimeoutError):
        account.inbox.run(Filter(SubjectMatches(r"(a|aa)+$"), MarkAsRead()), dry_run=True)
    # The connection it dropped is opened again for the next run.
    assert account.inbox.run(Filter(FromIs("news@shop.example"), MarkAsRead()))["filters"]


def test_run_rules_runs_each_folder(imap_server: ImapTestServer, mail) -> None:
    user = imap_server.unique_user("rules")
    create_folders(imap_server, user, "Shop")
    seed(imap_server, user, "INBOX", mime("Sale", sender=SHOP))
    seed(imap_server, user, "Junk", mime("You won", sender=SPAM))
    account = mail(user, pool_size=2)
    rules = [
        (account.inbox, [Filter(FromIs("news@shop.example"), MoveTo(Path("Shop")))]),
        (account / "Junk", [Filter(FromIs("win@spam.example"), Trash())]),
    ]

    reports = run_rules(rules, dry_run=True, parallel=True)
    assert [r["folder"] for r in reports] == [["INBOX"], ["Junk"]]
    assert subjects(imap_server, user, "Shop") == []

    run_rules(rules, dry_run=False)
    assert subjects(imap_server, user, "Shop") == ["Sale"]
    assert subjects(imap_server, user, "Trash") == ["You won"]


def test_monitor_runs_on_connect_and_on_new_mail_until_stopped(
    imap_server: ImapTestServer, mail
) -> None:
    user = imap_server.unique_user("monitor")
    create_folders(imap_server, user, "Shop")
    seed(imap_server, user, "INBOX", mime("Before", sender=SHOP))
    account = mail(user)
    stop = threading.Event()
    rules = [(account.inbox, [Filter(FromIs("news@shop.example"), MoveTo(Path("Shop")))])]
    thread = threading.Thread(
        target=monitor,
        args=(rules,),
        kwargs={"dry_run": False, "stop": stop, "install_signal_handlers": False},
    )
    thread.start()
    try:
        assert eventually(lambda: subjects(imap_server, user, "Shop") == ["Before"])
        seed(imap_server, user, "INBOX", mime("After", sender=SHOP))
        assert eventually(lambda: subjects(imap_server, user, "Shop") == ["Before", "After"])
    finally:
        stop.set()
        thread.join(WAIT_SECONDS)
    assert not thread.is_alive()
    assert subjects(imap_server, user, "INBOX") == []


def eventually(check: Callable[[], bool]) -> bool:
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.2)
    return False
