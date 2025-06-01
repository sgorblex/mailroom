from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.criterion_filter import CriterionFilter, cc_contains_is, from_is, mark_as_read, move_to, to_contains_is


class MoveIfFromFilter(CriterionFilter):
    def __init__(self, account: EMailAccount, sender: str, folder: str, mark_read = True):
        action = mark_as_read() & move_to(folder) if mark_read else move_to(folder)
        super().__init__(account, from_is(sender), action)


class MoveIfToFilter(CriterionFilter):
    def __init__(self, account: EMailAccount, correspondant: str, folder: str, include_CC = True, mark_read = True):
        criterion = to_contains_is(correspondant) | cc_contains_is(correspondant) if include_CC else to_contains_is(correspondant)
        action = mark_as_read() & move_to(folder) if mark_read else move_to(folder)
        super().__init__(account, criterion, action)
