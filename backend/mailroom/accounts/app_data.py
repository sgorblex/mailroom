"""The app's own rows about a user's accounts (schema ``app``), beside mailroom-core's: the Run
filters switch, and forgetting all of it when an account or its user goes (the user's remembered
remote-content senders and senders read as text, muted threads, ledger of copies moved between
accounts, mail index, notification folders, signatures, held sends, snoozes and vacation
replies, and its folders in the saved views too)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from mailroom.accounts.store import core_user_id, load_accounts, remove_account
from mailroom.filters.transfers import forget_target
from mailroom.mail.vacation import forget_vacation
from mailroom.models import (
    AccountFilterSettings,
    ApiToken,
    FilterRun,
    IdentitySignature,
    MailIndexFolder,
    MailNotificationFolders,
    MailViewInterest,
    MutedThread,
    RemoteContentSender,
    RuleSet,
    SavedView,
    SenderViewPreference,
    TransferLedger,
)
from mailroom.oauth.models import OAuthGrant
from mailroom_core.store import forget_user

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from mailroom_core.store import AccountStore


def run_filters_by_account(db: Session, user_id: int) -> dict[int, bool]:
    """Run filters per account id; an account without a row has it off."""
    rows = db.execute(
        select(AccountFilterSettings.account_id, AccountFilterSettings.run_filters).where(
            AccountFilterSettings.user_id == user_id
        )
    )
    return {row.account_id: row.run_filters for row in rows}


def set_run_filters(db: Session, user_id: int, account_id: int, *, run_filters: bool) -> None:
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


def forget_account(store: AccountStore, db: Session, user_id: int, account_id: int) -> None:
    """Remove the account from mailroom-core and the app's rows about it, the runs on it of the
    user's rule sets for all accounts included. The app's rows go first, so none is left behind
    if removing the account then fails; the user's other rule sets moving to it are turned off
    (``forget_target``). Raises ``AccountNotFound``, or ``RevisionConflict`` / ``SettingsBusy``."""
    load_accounts(store).account(account_id)
    db.execute(
        delete(AccountFilterSettings).where(
            AccountFilterSettings.user_id == user_id,
            AccountFilterSettings.account_id == account_id,
        )
    )
    forget_target(db, user_id, account_id)
    # Its rule sets go too, with their filters and runs, the runs on it of the rule sets for all
    # accounts (their reports name its mail), its muted threads and its index, whose messages go
    # with their folders, with the folders its browsers view.
    db.execute(delete(RuleSet).where(RuleSet.user_id == user_id, RuleSet.account_id == account_id))
    db.execute(
        delete(FilterRun).where(
            FilterRun.account_id == account_id,
            FilterRun.rule_set_id.in_(
                select(RuleSet.id).where(RuleSet.user_id == user_id, RuleSet.account_id.is_(None))
            ),
        )
    )
    db.execute(
        delete(MutedThread).where(
            MutedThread.user_id == user_id, MutedThread.account_id == account_id
        )
    )
    db.execute(
        delete(MailIndexFolder).where(
            MailIndexFolder.user_id == user_id, MailIndexFolder.account_id == account_id
        )
    )
    db.execute(
        delete(MailViewInterest).where(
            MailViewInterest.user_id == user_id, MailViewInterest.account_id == account_id
        )
    )
    db.execute(
        delete(MailNotificationFolders).where(
            MailNotificationFolders.user_id == user_id,
            MailNotificationFolders.account_id == account_id,
        )
    )
    db.execute(
        delete(IdentitySignature).where(
            IdentitySignature.user_id == user_id, IdentitySignature.account_id == account_id
        )
    )
    _drop_from_grants(db, user_id, account_id)
    _drop_from_views(db, user_id, account_id)
    _forget_scheduled(db, user_id, account_id)
    _forget_snoozes(db, user_id, account_id)
    forget_vacation(db, user_id, account_id)
    db.commit()
    remove_account(store, account_id)


def _drop_from_grants(db: Session, user_id: int, account_id: int) -> None:
    """Take the account out of the connected apps and API tokens narrowed to it; one left without
    any reaches none (never all of them)."""
    for holder in (OAuthGrant, ApiToken):
        db.execute(
            update(holder)
            .where(holder.user_id == user_id, holder.account_ids.contains([account_id]))
            .values(account_ids=func.array_remove(holder.account_ids, account_id))
        )


def _drop_from_views(db: Session, user_id: int, account_id: int) -> None:
    """Take the account's folders out of the user's saved views; a view left without one lists
    nothing."""
    for view in db.scalars(
        select(SavedView).where(SavedView.user_id == user_id, SavedView.sources.is_not(None))
    ):
        kept = [source for source in view.sources or () if source[0] != account_id]
        if len(kept) != len(view.sources or ()):
            view.sources = kept


def _forget_scheduled(db: Session, user_id: int, account_id: int | None = None) -> None:
    # Here, not at the top: the mail service imports this module's.
    from mailroom.mail.service.scheduled import forget_scheduled

    forget_scheduled(db, user_id, account_id)


def _forget_snoozes(db: Session, user_id: int, account_id: int | None = None) -> None:
    # Here, not at the top: the mail service imports this module's.
    from mailroom.mail.service.snooze import forget_snoozes

    forget_snoozes(db, user_id, account_id)


def forget_user_accounts(db: Session, user_id: int) -> None:
    """Remove everything about the user's accounts inside ``db``'s transaction, for the user's
    own removal in the same one: the app's rows (remembered senders included), then
    mailroom-core's (settings with their history, sealed passwords, send log). Raises
    ``SettingsBusy`` while a save of their accounts holds them."""
    db.execute(delete(AccountFilterSettings).where(AccountFilterSettings.user_id == user_id))
    db.execute(delete(RuleSet).where(RuleSet.user_id == user_id))
    db.execute(delete(MutedThread).where(MutedThread.user_id == user_id))
    db.execute(delete(TransferLedger).where(TransferLedger.user_id == user_id))
    db.execute(delete(MailIndexFolder).where(MailIndexFolder.user_id == user_id))
    db.execute(delete(MailNotificationFolders).where(MailNotificationFolders.user_id == user_id))
    db.execute(delete(RemoteContentSender).where(RemoteContentSender.user_id == user_id))
    db.execute(delete(SenderViewPreference).where(SenderViewPreference.user_id == user_id))
    db.execute(delete(SavedView).where(SavedView.user_id == user_id))
    db.execute(delete(IdentitySignature).where(IdentitySignature.user_id == user_id))
    _forget_scheduled(db, user_id)
    _forget_snoozes(db, user_id)
    forget_vacation(db, user_id)
    forget_user(db.connection(), core_user_id(user_id))
