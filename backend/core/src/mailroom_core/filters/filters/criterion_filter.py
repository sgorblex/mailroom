import re
import pyzmail

from imap_thingy.account import EMailAccount
from imap_thingy.filters.filter import Filter

import logging
logger = logging.getLogger("imap-thingy")


def get_all_mail(client):
    msg_ids = client.search(['ALL'])
    fetched = client.fetch(msg_ids, ['BODY.PEEK[]'])

    pyz_messages = []
    for msgid, data in fetched.items():
        msg = pyzmail.PyzMessage.factory(data[b'BODY[]'])
        pyz_messages.append((msgid, msg))

    return pyz_messages



class FilterCriterion():
    def __init__(self, func):
        self.func = func

    def matches(self, message):
        return self.func(message)

    def filter(self, messages):
        return [msgid for msgid, msg in messages if self.func(msg)]

    def __and__(self, other):
        return FilterCriterion(lambda msg: self.func(msg) & other.func(msg))

    def __or__(self, other):
        return FilterCriterion(lambda msg: self.func(msg) | other.func(msg))

    def __invert__(self):
        return FilterCriterion(lambda msg: ~self.func(msg))

def matches(pattern, string):
    return bool(re.fullmatch(pattern, string))

def select_all():
    return FilterCriterion(lambda _: True)

def subject_matches(pattern: str):
    return FilterCriterion(lambda msg: matches(pattern, msg.get_subject()))

def from_matches(pattern: str):
    return FilterCriterion(lambda msg: matches(pattern, msg.get_address('from')[1]))

def from_is(addr: str):
    return FilterCriterion(lambda msg: addr == msg.get_address('from')[1])

def to_contains(pattern: str):
    return FilterCriterion(lambda msg: any(matches(pattern, to) for to in msg.get_addresses('to')))


class MailAction():
    def __init__(self, func, name = "<no name>"):
        self.func = func
        self.name = name

    def execute(self, connection, msgids):
        for msg in msgids:
            self.func(connection, msg)

    def __and__(self, other):
        def newfunc(client, msg):
            self.func(client, msg)
            other.func(client, msg)
        return MailAction(newfunc, self.name + "; " + other.name)

    def __str__(self):
        return self.name

def move_to(folder: str):
    def func(client, msgids):
        client.move(msgids, folder)
    return MailAction(func, name=f"move to {folder}")

def mark_as_read():
    def func(client, msgids):
        client.add_flags(msgids, [b'\\Seen'])
    return MailAction(func, name="mark as read")

def mark_as_unread():
    def func(client, msgids):
        client.remove_flags(msgids, [b'\\Seen'])
    return MailAction(func, name="mark as unread")


class CriterionFilter(Filter):
    def __init__(self, accounts: EMailAccount, criterion: FilterCriterion, action: MailAction):
        super().__init__(accounts)
        assert isinstance(accounts, EMailAccount)
        self.account = accounts
        self.criterion = criterion
        self.action = action

    def apply(self, dry_run=False):
        msgs = self.criterion.filter(get_all_mail(self.account.connection))
        if msgs:
            if dry_run:
                logger.info(f"[Dry-Run] Would select: {msgs}")
                logger.info(f"[Dry-Run] Would execute: {self.action}")
            else:
                logger.info(f"Selected: {msgs}")
                self.action.execute(self.account.connection,msgs)
                logger.info(f"Executed: {self.action}")
