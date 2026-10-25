"""The app's own rows about a user's accounts (schema ``app``), beside mailroom-core's: the Run
filters switch, and forgetting all of it when an account or its user goes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mailroom_core.store import forget_user
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from mailroom.accounts.store import core_user_id, load_accounts, remove_account
from mailroom.models import AccountFilterSettings, RuleSet

if TYPE_CHECKING:
    from mailroom_core.store import AccountStore
    from sqlalchemy.orm import Session


def run_filters_by_account(db: Session, user_id: int) -> dict[str, bool]:
    """Run filters per account id; an account without a row has it off."""
    rows = db.execute(
        select(AccountFilterSettings.account_id, AccountFilterSettings.run_filters).where(
            AccountFilterSettings.user_id == user_id
        )
    )
    return {row.account_id: row.run_filters for row in rows}


def set_run_filters(db: Session, user_id: int, account_id: str, *, run_filters: bool) -> None:
    statement = insert(AccountFilterSettings).values(
        user_id=user_id, account_id=account_id, run_filters=run_filters
    )
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[AccountFilterSettings.user_id, AccountFilterSettings.account_id],
            set_={"run_filters": statement.excluded.run_filters},
        )
    )
    db.commit()


def forget_account(store: AccountStore, db: Session, user_id: int, account_id: str) -> None:
    """Remove the account from mailroom-core and the app's rows about it. The app's rows go first:
    if removing the account then fails, nothing is left for a new account with the same id to
    inherit. Raises ``AccountNotFound``, or ``RevisionConflict`` / ``SettingsBusy``."""
    load_accounts(store).account(account_id)
    db.execute(
        delete(AccountFilterSettings).where(
            AccountFilterSettings.user_id == user_id,
            AccountFilterSettings.account_id == account_id,
        )
    )
    # Its rule sets go too, with their filters and runs; a new account may reuse the id.
    db.execute(delete(RuleSet).where(RuleSet.user_id == user_id, RuleSet.account_id == account_id))
    db.commit()
    remove_account(store, account_id)


def forget_user_accounts(db: Session, user_id: int) -> None:
    """Remove everything about the user's accounts inside ``db``'s transaction, for the user's
    own removal in the same one: the app's rows, then mailroom-core's (settings with their
    history, sealed passwords, send log). Raises ``SettingsBusy`` while a save of their accounts
    holds them."""
    db.execute(delete(AccountFilterSettings).where(AccountFilterSettings.user_id == user_id))
    db.execute(delete(RuleSet).where(RuleSet.user_id == user_id))
    forget_user(db.connection(), core_user_id(user_id))
