from imap_thingy.accounts import EMailAccount
from imap_thingy.filters.criterion_filter import CriterionFilter, cc_contains_is, from_is, mark_as_read, move_to, to_contains_is, bcc_contains_is


class MoveIfFromFilter(CriterionFilter):
    def __init__(self, account: EMailAccount, sender: str, folder: str, mark_read: bool = True, base_folder: str = "INBOX") -> None:
        action = mark_as_read() & move_to(folder) if mark_read else move_to(folder)
        super().__init__(account, from_is(sender), action, base_folder=base_folder)


class MoveIfToFilter(CriterionFilter):
    def __init__(self, account: EMailAccount, correspondant: str, folder: str, include_CC: bool = True, include_BCC: bool = True, mark_read: bool = True) -> None:
        criterion = to_contains_is(correspondant)
        if include_CC:
            criterion |= cc_contains_is(correspondant)
        if include_BCC:
            criterion |= bcc_contains_is(correspondant)
        action = mark_as_read() & move_to(folder) if mark_read else move_to(folder)
        super().__init__(account, criterion, action)
