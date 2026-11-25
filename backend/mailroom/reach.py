"""How far a caller reaches within its user's Mailroom: which of the user's accounts, and how far
it may send. The user on the web has the whole reach; a connected app what its grant gives it.

An ``AppContext`` carries it (``AppContext.within``), and what the services read of the accounts
comes through ``AppContext.accounts``, cut to it: an account outside it is as one that is not the
user's. Rows naming an account (rule sets, muted threads, held sends…) are cut by ``covers``."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, get_args

from sqlalchemy import true

from mailroom_core.config import SendMode

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import InstrumentedAttribute

    from mailroom.accounts.store import UserAccounts
    from mailroom_core.config import AccountConfig

SEND_CEILINGS: Final[tuple[SendMode, ...]] = get_args(SendMode.__value__)
"""What a send ceiling may be, strictest first."""


def stricter(one: SendMode, other: SendMode) -> SendMode:
    return min(one, other, key=SEND_CEILINGS.index)


@dataclass(frozen=True, slots=True)
class Reach:
    accounts: frozenset[int] | None = None
    """The account ids it is narrowed to; None for every account, those added later too."""
    ceiling: SendMode | None = None
    """The most it may do when it sends, whatever an account's send mode; None for no ceiling."""

    @property
    def narrowed(self) -> bool:
        """Whether it leaves accounts out: the rule sets for all accounts are then beyond it."""
        return self.accounts is not None

    def includes(self, account_id: int | None) -> bool:
        return self.accounts is None or account_id in self.accounts

    def covers(
        self, account_id: InstrumentedAttribute[int] | InstrumentedAttribute[int | None]
    ) -> ColumnElement[bool]:
        """The rows about an account within it (a null account id, all accounts, is not once it
        is narrowed)."""
        return true() if self.accounts is None else account_id.in_(self.accounts)

    def capped(self, account: AccountConfig) -> AccountConfig:
        """``account`` with the stricter of its send mode and the ceiling. Under ``approval`` the
        account's own auto-approved recipients are still the ones sent to."""
        if self.ceiling is None:
            return account
        mode = stricter(account.send_mode, self.ceiling)
        return account.model_copy(update={"send_mode": mode})

    def within(self, loaded: UserAccounts) -> UserAccounts:
        """``loaded`` for reading: its settings cut to the accounts within, each ``capped``."""
        if self == WHOLE:
            return loaded
        kept = [self.capped(a) for a in loaded.settings.account if self.includes(a.id)]
        return replace(loaded, settings=loaded.settings.model_copy(update={"account": kept}))


WHOLE: Final = Reach()


def reach_of(account_ids: object, send_ceiling: object) -> Reach:
    """The reach a grant's limits give, as kept or as a token's claims carry them: None is no
    limit, and what cannot be read reaches nothing, or sends nothing."""
    accounts: frozenset[int] | None = None
    if account_ids is not None:
        listed = account_ids if isinstance(account_ids, list) else []
        accounts = frozenset(each for each in listed if type(each) is int)
    ceiling: SendMode | None = None
    if send_ceiling is not None:
        ceiling = next((mode for mode in SEND_CEILINGS if mode == send_ceiling), "off")
    return Reach(accounts, ceiling)
