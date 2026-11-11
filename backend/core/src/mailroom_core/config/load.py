"""Accounts and limits, checked, their secrets opened: core's half of `src/config/load.ts`.

mailroom-mcp adds the bootstrap file and the profiles; `CoreConfig` is the config without them,
which the send gate takes.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from mailroom_core.config.errors import ConfigError
from mailroom_core.config.schema import (
    AccountConfig,
    CoreSettings,
    Limits,
    TimeZoneName,
    is_env_name,
)
from mailroom_core.crypto import SealedSecretError, SecretsSubkey, open_secret, seal_secret


@dataclass(frozen=True, slots=True)
class CoreConfig:
    """The running accounts and limits: what the mail layer and the send gate need."""

    time_zone: TimeZoneName
    """IANA time zone of the dates shown to the model."""
    limits: Limits
    accounts: tuple[AccountConfig, ...]
    secrets: Mapping[str, str] = field(repr=False)
    """Mailbox password or token per running account id."""


@dataclass(frozen=True, slots=True)
class LoadedCore:
    """The core config as loaded, with what the load found worth saying."""

    config: CoreConfig
    warnings: tuple[str, ...]
    """Problems worth logging that do not stop the server."""
    inactive: tuple[str, ...]
    """Accounts left out of the config: their stored secret is missing or does not open."""


def without_secret_envs(raw: object) -> tuple[object, Mapping[str, str]]:
    """Take each account's `auth.secret_env` out of raw settings, by account id.

    Settings may name an account's password by environment variable: config.toml's, being
    imported, or a database's from before 0.2.1. The names come out, for `sealed_env_secrets` to
    store their values; a malformed one stays where it is, for the schema to refuse.
    """
    secret_envs: dict[str, str] = {}
    if not isinstance(raw, Mapping) or not isinstance(raw.get("account"), list):
        return raw, MappingProxyType(secret_envs)
    accounts: list[object] = []
    for acc in raw["account"]:
        auth = acc.get("auth") if isinstance(acc, Mapping) else None
        if not isinstance(auth, Mapping):
            accounts.append(acc)
            continue
        name = auth.get("secret_env")
        account_id = acc.get("id")
        if not isinstance(account_id, str) or not is_env_name(name):
            accounts.append(acc)
            continue
        secret_envs[account_id] = name
        accounts.append({**acc, "auth": {k: v for k, v in auth.items() if k != "secret_env"}})
    return {**raw, "account": accounts}, MappingProxyType(secret_envs)


def sealed_env_secrets(
    subkey: SecretsSubkey | None, secret_envs: Mapping[str, str], env: Mapping[str, str]
) -> dict[str, bytes]:
    """The passwords `secret_envs` names, read from `env` and sealed for the database.

    A missing variable or secrets key is a deployment error: nothing is stored until it is fixed.
    """
    sealed: dict[str, bytes] = {}
    missing: dict[str, None] = {}
    for account_id, name in secret_envs.items():
        value = env.get(name, "")
        if value == "":
            missing[name] = None
        elif subkey is not None:
            sealed[account_id] = seal_secret(subkey, account_id, "password", value)
    if missing:
        raise ConfigError(f"missing secrets in the environment: {', '.join(missing)}")
    if len(sealed) < len(secret_envs):
        raise ConfigError(
            f"storing the passwords of {', '.join(secret_envs)} needs [auth] secrets_key_env"
            " (MAILROOM_SECRETS_KEY)"
        )
    return sealed


def loaded_core(
    time_zone: TimeZoneName,
    settings: CoreSettings,
    subkey: SecretsSubkey | None,
    stored: Mapping[str, bytes] = MappingProxyType({}),
) -> LoadedCore:
    """The running accounts, each with its secret opened from `stored` (sealed blobs by id).

    An account whose stored secret is missing or does not open (no key, another key) is left out
    with a warning, so the settings page still comes up to fix it.
    """
    secrets: dict[str, str] = {}
    inactive: list[tuple[str, str]] = []
    for acc in settings.account:
        secret = _stored_secret(subkey, acc.id, stored)
        if isinstance(secret, _Problem):
            inactive.append((acc.id, secret.reason))
        else:
            secrets[acc.id] = secret
    running = tuple(acc for acc in settings.account if acc.id in secrets)
    return LoadedCore(
        config=CoreConfig(
            time_zone=time_zone,
            limits=settings.limits,
            accounts=running,
            secrets=MappingProxyType(secrets),
        ),
        warnings=(
            *(
                f'account "{account_id}" is not running: {reason}.'
                " Enter its password on the settings page."
                for account_id, reason in inactive
            ),
            *_identity_domain_warnings(running),
            *_auto_approve_warnings(running),
        ),
        inactive=tuple(account_id for account_id, _ in inactive),
    )


@dataclass(frozen=True, slots=True)
class _Problem:
    reason: str


def _stored_secret(
    subkey: SecretsSubkey | None, account_id: str, stored: Mapping[str, bytes]
) -> str | _Problem:
    """An account's stored secret, or why it cannot be used (never the value)."""
    blob = stored.get(account_id)
    if blob is None:
        return _Problem("it has no stored password")
    if subkey is None:
        return _Problem("its stored password needs [auth] secrets_key_env (MAILROOM_SECRETS_KEY)")
    try:
        return open_secret(subkey, account_id, "password", blob)
    except SealedSecretError:
        return _Problem(
            "its stored password does not open with this secrets key (another key, or altered)"
        )


def _domain(address: str) -> str:
    return address[address.rfind("@") + 1 :].lower()


def _identity_domain_warnings(accounts: tuple[AccountConfig, ...]) -> list[str]:
    return [
        f'account "{acc.id}": identity {identity.address} is outside the login\'s domain;'
        " the SMTP server may refuse it"
        for acc in accounts
        for identity in acc.identities
        if "@" in acc.login and _domain(identity.address) != _domain(acc.login)
    ]


def _auto_approve_warnings(accounts: tuple[AccountConfig, ...]) -> list[str]:
    """`auto_approve` only means something in `approval` mode; elsewhere it is likely a mistake."""
    return [
        f'account "{acc.id}": auto_approve has no effect with send_mode "{acc.send_mode}"'
        for acc in accounts
        if acc.auto_approve and acc.send_mode != "approval"
    ]
