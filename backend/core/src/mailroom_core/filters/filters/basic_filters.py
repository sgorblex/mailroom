from imap_thingy.account import EMailAccount
from imap_thingy.filters.criterion_filter import CriterionFilter, from_is, mark_as_read, move_to, to_contains_is


class MoveIfFromFilter(CriterionFilter):
    def __init__(self, account: EMailAccount, sender: str, folder: str, mark_read = True):
        action = mark_as_read() & move_to(folder) if mark_read else move_to(folder)
        super().__init__(account, from_is(sender), action)


class MoveIfToFilter(CriterionFilter):
    def __init__(self, account: EMailAccount, sender: str, folder: str, mark_read = True):
        action = mark_as_read() & move_to(folder) if mark_read else move_to(folder)
        super().__init__(account, to_contains_is(sender), action)
