"""A user's accounts, kept as mailroom-core keeps them: one settings document per user in schema
``core`` (``AccountStore``), each account's password sealed beside it.

Every change replaces the whole document at the next revision, checked by mailroom-core's own
config models first.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from mailroom_core.config import AccountConfig, CoreSettings, core_settings_or_issues
from mailroom_core.crypto import SealedSecretError, open_secret, seal_secret
from mailroom_core.store import AccountStore, SecretChanges

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.engine import Engine

    from mailroom_core.config import ConfigIssue, Limits
    from mailroom_core.crypto import SecretsSubkey
    from mailroom_core.store import Json

_ACCOUNTS: Final = "account"
ACCOUNT_NAME_TAKEN: Final = "account_name_taken"
"""Another of the user's accounts has the name (compared by ``name_key``)."""
_LIMITS: Final = "limits"


def core_user_id(user_id: int) -> str:
    """The key of a Mailroom user's rows in schema ``core``."""
    return str(user_id)


def user_store(engine: Engine, user_id: int) -> AccountStore:
    """The store of one Mailroom user."""
    return AccountStore(engine, core_user_id(user_id))


@dataclass(frozen=True, slots=True)
class Issue:
    """What is wrong with one field of an account, at a path inside it (``("imap", "port")``);
    an empty path is about the account as a whole."""

    path: tuple[str | int, ...]
    message: str


class AccountError(ValueError):
    """A change to the accounts that cannot be made. The message is safe to show; ``code`` names
    the case for a client that words it itself."""

    def __init__(self, message: str, code: str, issues: Sequence[Issue] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.issues = tuple(issues)


class AccountNotFound(LookupError):  # noqa: N818 -- reads as the case it is
    """No account of this user has that id."""


@dataclass(frozen=True, slots=True)
class UserAccounts:
    revision: int
    """0 before the first save."""
    doc: dict[str, Json]
    """The settings document as stored."""
    settings: CoreSettings

    def account(self, account_id: int) -> AccountConfig:
        for account in self.settings.account:
            if account.id == account_id:
                return account
        raise AccountNotFound(account_id)

    def raw_accounts(self) -> list[Json]:
        """A copy of the stored account entries, in order."""
        accounts = self.doc.get(_ACCOUNTS, [])
        return list(accounts) if isinstance(accounts, list) else []


def load_accounts(store: AccountStore) -> UserAccounts:
    stored = store.settings()
    doc: dict[str, Json] = {}
    if stored is not None and isinstance(stored.doc, dict):
        doc = stored.doc
    checked = core_settings_or_issues(doc)
    if not isinstance(checked, CoreSettings):
        # Only this module writes these documents, always checked first.
        message = f"user {store.user_id}'s stored settings are invalid"
        raise RuntimeError(message)
    return UserAccounts(
        revision=0 if stored is None else stored.revision, doc=doc, settings=checked
    )


def accounts_with_password(store: AccountStore) -> frozenset[int]:
    """The ids of the accounts with a stored password."""
    return frozenset(store.secrets())


def password_account(account_id: int | None, fields: Mapping[str, Json]) -> dict[str, Json]:
    """The account as mailroom-core stores it from its ``fields``: password auth, the secret kept
    apart; a new account (``account_id`` None) is given its id when saved."""
    account: dict[str, Json] = {**fields, "auth": {"type": "password"}}
    return account if account_id is None else {"id": account_id, **account}


def save_account(
    store: AccountStore,
    subkey: SecretsSubkey | None,
    raw: Mapping[str, Json],
    password: str | None,
    *,
    new: bool,
) -> AccountConfig:
    """Add the account ``raw`` describes with a new id (``new``) or replace the one with its id,
    storing ``password`` sealed when given (a new account needs one), refusing a new or changed
    name another of the user's accounts has (``account_name_taken``) in the same revision.
    Raises ``AccountError``, ``AccountNotFound``, or mailroom-core's ``RevisionConflict`` /
    ``SettingsBusy``."""
    if new and not password:
        msg = "A new account needs its password."
        raise AccountError(msg, "invalid_account", [Issue(("password",), "Required")])
    current = load_accounts(store)
    accounts = current.raw_accounts()
    stored_name: str | None = None
    if new:
        account_id = store.new_account_id()
        index = len(accounts)
        accounts.append({"id": account_id, **raw})
    else:
        stored_id = raw.get("id")
        found = next(
            (
                i
                for i, acc in enumerate(accounts)
                if isinstance(acc, dict) and acc.get("id") == stored_id
            ),
            None,
        )
        if not isinstance(stored_id, int) or found is None:
            raise AccountNotFound(stored_id)
        account_id, index = stored_id, found
        stored_name = current.account(account_id).name
        accounts[index] = dict(raw)
    doc: dict[str, Json] = {**current.doc, _ACCOUNTS: accounts}
    checked = core_settings_or_issues(doc)
    if not isinstance(checked, CoreSettings):
        issues = [_issue_of(issue, index) for issue in checked]
        raise AccountError(_issues_text(issues), "invalid_account", issues)
    _refuse_a_taken_name(checked.account, index, stored_name)
    secrets = SecretChanges()
    if password:
        if subkey is None:
            msg = "MAILROOM_SECRETS_KEY is not set, so no password can be stored."
            raise AccountError(msg, "secrets_key_missing")
        sealed = seal_secret(
            subkey,
            account_id,
            "password",
            password,
            former_slug=store.former_slugs().get(account_id),
        )
        secrets = SecretChanges(set={account_id: sealed})
    store.save_settings(doc, current.revision, secrets)
    return checked.account[index]


def name_key(name: str) -> str:
    """What two account names are compared by: the same once NFKC-normalised and trimmed, whatever
    their case."""
    return unicodedata.normalize("NFKC", name).strip().casefold()


def _refuse_a_taken_name(
    accounts: Sequence[AccountConfig], index: int, stored_name: str | None
) -> None:
    """Refuse ``accounts[index]``'s name when another of the user's accounts has it
    (``account_name_taken``), unless it is the account's ``stored_name`` kept; an unnamed account
    takes none."""
    name = accounts[index].name
    if name is None:
        return
    key = name_key(name)
    if stored_name is not None and name_key(stored_name) == key:
        return
    for i, other in enumerate(accounts):
        if i != index and other.name is not None and name_key(other.name) == key:
            msg = f"Another account is already named {other.name!r}."
            raise AccountError(msg, ACCOUNT_NAME_TAKEN, [Issue(("name",), msg)])


def save_limits(
    store: AccountStore, raw: Mapping[str, Json], attachment_cap: float, move_cap: float
) -> Limits:
    """Replace the user's limits with ``raw``, the accounts kept, with ``attachment_max_mb`` at
    most ``attachment_cap`` and ``move_max_mb`` at most ``move_cap``. Raises ``AccountError``
    (``invalid_limits``), or mailroom-core's ``RevisionConflict`` / ``SettingsBusy``."""
    current = load_accounts(store)
    doc: dict[str, Json] = {**current.doc, _LIMITS: dict(raw)}
    checked = core_settings_or_issues(doc)
    issues = (
        []
        if isinstance(checked, CoreSettings)
        else [
            Issue(tuple(issue.path[1:]) if issue.path[:1] == (_LIMITS,) else (), issue.message)
            for issue in checked
        ]
    )
    for name, cap in (("attachment_max_mb", attachment_cap), ("move_max_mb", move_cap)):
        value = raw.get(name)
        if isinstance(value, (int, float)) and value > cap:
            issues.append(Issue((name,), f"At most {cap:g}"))
    if issues or not isinstance(checked, CoreSettings):
        raise AccountError(_issues_text(issues), "invalid_limits", issues)
    store.save_settings(doc, current.revision)
    return checked.limits


def remove_account(store: AccountStore, account_id: int) -> None:
    """Remove the account and its stored password. Raises ``AccountNotFound``, or
    ``RevisionConflict`` / ``SettingsBusy``."""
    current = load_accounts(store)
    current.account(account_id)
    accounts = [
        acc
        for acc in current.raw_accounts()
        if not (isinstance(acc, dict) and acc.get("id") == account_id)
    ]
    store.save_settings(
        {**current.doc, _ACCOUNTS: accounts}, current.revision, SecretChanges(remove=[account_id])
    )


def account_password(
    store: AccountStore, subkey: SecretsSubkey | None, account_id: int
) -> str | None:
    """The account's password, or None when none is stored or it does not open (no key, or
    another one)."""
    blob = store.secrets().get(account_id)
    if blob is None or subkey is None:
        return None
    try:
        return open_secret(
            subkey,
            account_id,
            "password",
            blob,
            former_slug=store.former_slugs().get(account_id),
        )
    except SealedSecretError:
        return None


def _issue_of(issue: ConfigIssue, index: int) -> Issue:
    """mailroom-core's issue about the whole document, as one about this account's fields."""
    path = issue.path
    if len(path) >= 2 and path[0] == _ACCOUNTS and path[1] == index:
        return Issue(tuple(path[2:]), issue.message)
    return Issue((), issue.message)


def _issues_text(issues: Sequence[Issue]) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in issue.path)}: {issue.message}"
        if issue.path
        else issue.message
        for issue in issues
    )
