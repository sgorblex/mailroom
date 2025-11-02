import re
import mailparser
from imapclient import imapclient, IMAPClient
from typing import Any, Callable

from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.interfaces import OneAccountOneFolderFilter

import logging

logger = logging.getLogger("imap-thingy")

# Type alias for parsed mail message
ParsedMail = Any  # mailparser doesn't have type stubs


def get_mail(client: IMAPClient, imap_query: list[str]) -> list[tuple[int, ParsedMail]]:
    logger.info(f"Fetching mail with IMAP query {imap_query}")

    msg_ids = client.search(imap_query)
    fetched = client.fetch(msg_ids, ["BODY.PEEK[]"])

    messages: list[tuple[int, ParsedMail]] = []
    for msgid, data in fetched.items():
        msg = mailparser.parse_from_bytes(data[b"BODY[]"])
        messages.append((msgid, msg))

    return messages


def matches(pattern: str, string: str) -> bool:
    return bool(re.fullmatch(pattern, string))


class FilterCriterion:
    """
    imap_query is a preliminary filter applied when the first IMAP query is performed. This allows to limit client-side filtering, if an appropriate query is given by the user.
    """

    def __init__(self, func: Callable[[ParsedMail], bool], imap_query: list[str | list[Any]] | None = None) -> None:
        self.func = func
        self.imap_query = imap_query

    def filter(self, connection: IMAPClient) -> list[int]:
        imap_query = self.imap_query if self.imap_query else ["ALL"]
        messages = get_mail(connection, imap_query)
        return [msgid for msgid, msg in messages if self.func(msg)]

    def __and__(self, other: "FilterCriterion") -> "FilterCriterion":
        func = lambda msg: self.func(msg) & other.func(msg)
        imap_query = (self.imap_query + other.imap_query) if other.imap_query else self.imap_query
        return FilterCriterion(func, imap_query)

    def __or__(self, other: "FilterCriterion") -> "FilterCriterion":
        func = lambda msg: self.func(msg) | other.func(msg)
        imap_query = ["OR", self.imap_query, other.imap_query] if other.imap_query else None
        return FilterCriterion(func, imap_query)

    def __invert__(self) -> "FilterCriterion":
        func = lambda msg: ~self.func(msg)
        imap_query = ["NOT", self.imap_query] if self.imap_query else None
        return FilterCriterion(func, imap_query)


class EfficientCriterion(FilterCriterion):
    """
    If the criterion only needs information obtainable via an IMAP query, there is no need to fetch the messages at all, so it can be performed more efficiently.
    """

    def __init__(self, func: Callable[[ParsedMail], bool], imap_query: list[str | list[Any]]) -> None:
        super().__init__(func, imap_query)

    def filter(self, connection: IMAPClient) -> list[int]:
        logger.info(f"Fetching mail efficiently with IMAP query {self.imap_query}")
        return connection.search(self.imap_query)

    def __and__(self, other: FilterCriterion) -> FilterCriterion:
        criterion = super().__and__(other)
        if isinstance(other, EfficientCriterion):
            return make_efficient(criterion)
        else:
            return criterion

    def __or__(self, other: FilterCriterion) -> FilterCriterion:
        criterion = super().__or__(other)
        if isinstance(other, EfficientCriterion):
            return make_efficient(criterion)
        else:
            return criterion

    def __invert__(self) -> "EfficientCriterion":
        return make_efficient(super().__invert__())


def make_efficient(criterion: FilterCriterion) -> EfficientCriterion:
    return EfficientCriterion(criterion.func, criterion.imap_query)


# efficient
def select_all() -> EfficientCriterion:
    return EfficientCriterion(lambda _: True, ["ALL"])


def from_contains(addr: str) -> EfficientCriterion:
    return EfficientCriterion(lambda msg: any(addr in email for name, email in msg.from_), ["FROM", addr])


def to_contains_contains(addr: str) -> EfficientCriterion:
    return EfficientCriterion(lambda msg: any(addr in email for name, email in msg.to), ["TO", addr])


def subject_contains(substring: str) -> EfficientCriterion:
    return EfficientCriterion(lambda msg: substring in (msg.subject or ""), imap_query=["SUBJECT", substring])


# semi-efficient
def from_is(addr: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: any(addr == email for name, email in msg.from_), imap_query=["FROM", addr])


def to_contains_is(addr: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: any(addr == email for name, email in msg.to), imap_query=["TO", addr])


def cc_contains_is(addr: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: any(addr == email for name, email in msg.cc), imap_query=["CC", addr])


def bcc_contains_is(addr: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: any(addr == email for name, email in msg.bcc), imap_query=["BCC", addr])


def subject_is(subj: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: (msg.subject or "") == subj, imap_query=["SUBJECT", subj])


# non-efficient
def from_matches(pattern: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: any(matches(pattern, email) for name, email in msg.from_))


def from_matches_name(pattern: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: any(matches(pattern, name) for name, email in msg.from_))


def to_contains_matches(pattern: str, incl_cc: bool = True, incl_bcc: bool = True) -> FilterCriterion:
    criterion = FilterCriterion(lambda msg: any(matches(pattern, email) for name, email in msg.to))
    if incl_cc:
        criterion |= cc_contains_matches(pattern)
    if incl_bcc:
        criterion |= bcc_contains_matches(pattern)
    return criterion


def cc_contains_matches(pattern: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: any(matches(pattern, email) for name, email in msg.cc))


def bcc_contains_matches(pattern: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: any(matches(pattern, email) for name, email in msg.bcc))


def subject_matches(pattern: str) -> FilterCriterion:
    return FilterCriterion(lambda msg: matches(pattern, msg.subject or ""))


class MailAction:
    def __init__(self, func: Callable[[EMailAccount, list[int]], None], name: str = "<no name>") -> None:
        self.func = func
        self.name = name

    def execute(self, account: EMailAccount, msgids: list[int]) -> None:
        for msg in msgids:
            self.func(account, msg)

    def __and__(self, other: "MailAction") -> "MailAction":
        def newfunc(account: EMailAccount, msg: list[int]) -> None:
            self.func(account, msg)
            other.func(account, msg)

        return MailAction(newfunc, self.name + "; " + other.name)

    def __str__(self) -> str:
        return self.name


def move_to(folder: str) -> MailAction:
    def func(account: EMailAccount, msgids: list[int]) -> None:
        account.connection.move(msgids, folder)

    return MailAction(func, name=f"move to {folder}")


def trash() -> MailAction:
    def func(account: EMailAccount, msgids: list[int]) -> None:
        account.connection.move(msgids, account.connection.find_special_folder(imapclient.TRASH))

    return MailAction(func, name=f"trash")


def mark_as_read() -> MailAction:
    def func(account: EMailAccount, msgids: list[int]) -> None:
        account.connection.add_flags(msgids, [b"\\Seen"])

    return MailAction(func, name="mark as read")


def mark_as_unread() -> MailAction:
    def func(account: EMailAccount, msgids: list[int]) -> None:
        account.connection.remove_flags(msgids, [b"\\Seen"])

    return MailAction(func, name="mark as unread")


class CriterionFilter(OneAccountOneFolderFilter):
    def __init__(self, account: EMailAccount, criterion: FilterCriterion, action: MailAction, base_folder: str = "INBOX") -> None:
        super().__init__(account, base_folder)
        self.criterion = criterion
        self.action = action

    def apply(self, dry_run: bool = False) -> None:
        super().apply(dry_run)
        msgs = self.criterion.filter(self.account.connection)
        if msgs:
            if dry_run:
                logger.info(f"[Dry-Run] Would select: {msgs}")
                logger.info(f"[Dry-Run] Would execute: {self.action}")
            else:
                logger.info(f"Selected: {msgs}")
                self.action.execute(self.account, msgs)
                logger.info(f"Executed: {self.action}")
