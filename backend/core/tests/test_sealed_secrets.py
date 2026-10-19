"""Port of `src/crypto/sealed-secrets.test.ts`.

Of its `stored secrets in the config` block, the case that reads the bootstrap is mailroom-mcp's
(`mcp/tests/test_sealed_secrets.py`); only `are written and cleared with the settings, in one
save` remains, to port with `store`.
"""

import os
import re
import tomllib
from typing import TYPE_CHECKING, NamedTuple

import pytest

from mailroom_core.config import (
    ConfigError,
    CoreSettings,
    TimeZoneName,
    core_settings_or_issues,
    issues_text,
    loaded_core,
    sealed_env_secrets,
    without_secret_envs,
)
from mailroom_core.crypto import (
    SealedSecretError,
    open_secret,
    seal_secret,
    sealed_secrets,
    secrets_subkey,
)
from mailroom_core.testing import config_env, local_config_text

if TYPE_CHECKING:
    from collections.abc import Mapping

key = secrets_subkey(os.urandom(32))

# Sealed by src/crypto/sealed-secrets.ts under MAILROOM_SECRETS_KEY = "ab" * 32.
TS_KEY = bytes.fromhex("ab" * 32)
TS_SUBKEY = "813ead718db448985742728fbda10a1f6918cc5cd6f4fda28a2d64ca46808bb5"
TS_BLOB = bytes.fromhex(
    "01904f5bc84f825f848b0bfb678c8be958e4fc1dc966e2f3113083372c9323266dd55c2af97e2dcae8a2e82dd968"
)


def test_open_with_the_same_key_account_and_purpose_only() -> None:
    blob = seal_secret(key, "acc", "password", "hunter2 ünïcode")
    assert b"hunter2" not in blob
    assert open_secret(key, "acc", "password", blob) == "hunter2 ünïcode"
    with pytest.raises(SealedSecretError):
        open_secret(key, "other", "password", blob)
    with pytest.raises(SealedSecretError):
        open_secret(secrets_subkey(os.urandom(32)), "acc", "password", blob)
    tampered = bytearray(blob)
    tampered[20] ^= 1
    with pytest.raises(SealedSecretError):
        open_secret(key, "acc", "password", bytes(tampered))
    with pytest.raises(SealedSecretError):
        open_secret(key, "acc", "password", bytes([2]) + blob[1:])


def test_differ_each_time_even_for_the_same_value() -> None:
    assert seal_secret(key, "acc", "password", "x") != seal_secret(key, "acc", "password", "x")


def test_open_a_blob_the_typescript_code_sealed() -> None:
    subkey = secrets_subkey(TS_KEY)
    assert subkey.hex() == TS_SUBKEY
    assert open_secret(subkey, "acc", "password", TS_BLOB) == "hunter2 ünïcode"
    with pytest.raises(SealedSecretError):
        open_secret(subkey, "other", "password", TS_BLOB)


def test_seal_the_same_bytes_as_the_typescript_code(monkeypatch: pytest.MonkeyPatch) -> None:
    nonce = TS_BLOB[1:13]
    monkeypatch.setattr(sealed_secrets, "token_bytes", lambda _: nonce)
    assert seal_secret(secrets_subkey(TS_KEY), "acc", "password", "hunter2 ünïcode") == TS_BLOB


# home's password already stored rather than named by variable.
STORED_TEXT = local_config_text().replace(
    'auth       = { type = "password", secret_env = "HOME_TOKEN" }',
    'auth       = { type = "password" }',
    1,
)
SUBKEY = secrets_subkey(bytes.fromhex("ab" * 32))
ENV = config_env()
UTC = TimeZoneName("UTC")


class Seeded(NamedTuple):
    settings: CoreSettings
    secret_envs: Mapping[str, str]


def seeded(text: str = STORED_TEXT) -> Seeded:
    raw = tomllib.loads(text)
    seed, secret_envs = without_secret_envs(
        {key: raw[key] for key in ("limits", "account") if key in raw}
    )
    settings = core_settings_or_issues(seed)
    if not isinstance(settings, CoreSettings):
        raise ConfigError(f"invalid settings (test):\n{issues_text(settings)}")
    return Seeded(settings, secret_envs)


def test_take_secret_env_out_of_the_settings_and_seal_what_it_names() -> None:
    assert 'auth       = { type = "password" }' in STORED_TEXT
    parts = seeded()
    assert dict(parts.secret_envs) == {"work": "WORK_APP_PASSWORD"}
    assert [acc.auth.model_dump() for acc in parts.settings.account] == [
        {"type": "password"},
        {"type": "password"},
    ]
    sealed = sealed_env_secrets(SUBKEY, parts.secret_envs, ENV)
    assert open_secret(SUBKEY, "work", "password", sealed["work"]) == "google-secret"
    sealed["home"] = seal_secret(SUBKEY, "home", "password", "stored-token")
    config = loaded_core(UTC, parts.settings, SUBKEY, sealed).config
    assert config.secrets["home"] == "stored-token"
    assert config.secrets["work"] == "google-secret"


def test_refuse_to_seal_without_the_variable_or_a_secrets_key_naming_neither_value() -> None:
    parts = seeded()
    with pytest.raises(
        ConfigError, match=r"^missing secrets in the environment: WORK_APP_PASSWORD$"
    ):
        sealed_env_secrets(SUBKEY, parts.secret_envs, {**ENV, "WORK_APP_PASSWORD": ""})
    with pytest.raises(
        ConfigError, match=re.escape("storing the passwords of work needs [auth] secrets_key_env")
    ):
        sealed_env_secrets(None, parts.secret_envs, ENV)
    assert sealed_env_secrets(None, {}, ENV) == {}


def test_leave_a_malformed_secret_env_for_the_schema_to_refuse() -> None:
    malformed = STORED_TEXT.replace(
        'secret_env = "WORK_APP_PASSWORD"', 'secret_env = "x y"', 1
    )
    with pytest.raises(ConfigError, match="secret_env"):
        seeded(malformed)


def test_leave_out_an_account_whose_stored_secret_is_missing_or_does_not_open_saying_why() -> None:
    settings = seeded().settings
    missing = loaded_core(UTC, settings, SUBKEY)
    assert missing.inactive == ("home", "work")
    assert missing.config.accounts == ()
    assert any(
        'account "home" is not running: it has no stored password' in warning
        for warning in missing.warnings
    )

    wrong = {"home": seal_secret(secrets_subkey(os.urandom(32)), "home", "password", "x")}
    assert any(
        "does not open with this secrets key" in warning
        for warning in loaded_core(UTC, settings, SUBKEY, wrong).warnings
    )
    unkeyed = loaded_core(UTC, settings, None, wrong)
    assert any("needs [auth] secrets_key_env" in warning for warning in unkeyed.warnings)
