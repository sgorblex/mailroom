"""A stand-in for `MailConnections` for the engine's unit tests: real runs are in
`test_engine_mail.py`, against Dovecot."""

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

from mailroom_core.filters.account import Account
from mailroom_core.mail import Folder, MailboxContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from mailroom_core.mail import MailConnections


class FakeConnections:
    """Runs a session's function at once on a MagicMock client, recording each session as
    (account, path, read_only); `folders` and `delimiter` answer what the test sets."""

    def __init__(self, delimiter: str | None = ".") -> None:
        self.client = MagicMock()
        self.hierarchy = delimiter
        self.listed: list[Folder] = []
        self.exists = 0
        self.sessions: list[tuple[str, str, bool]] = []

    def delimiter(self, account_id: str) -> str | None:
        return self.hierarchy

    def folders(self, account_id: str, *, with_status: bool = False) -> list[Folder]:
        return self.listed

    def with_mailbox[T](
        self, account_id: str, path: str, read_only: bool, fn: Callable[[MailboxContext], T]
    ) -> T:
        self.sessions.append((account_id, path, read_only))
        return fn(context(self.client, path, read_only, account_id, self.exists))


def context(
    client: object,
    path: str = "INBOX",
    read_only: bool = False,
    account: str = "a",
    exists: int = 0,
) -> MailboxContext:
    return MailboxContext(
        client=cast("MagicMock", client),
        path=path,
        read_only=read_only,
        uid_validity=1,
        exists=exists,
        uid_next=None,
        account=account,
    )


@pytest.fixture
def connections() -> FakeConnections:
    return FakeConnections()


@pytest.fixture
def account(connections: FakeConnections) -> Account:
    return Account(cast("MailConnections", connections), "test_account")
