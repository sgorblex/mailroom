"""Port of `src/config/load.test.ts`: its `parseConfig` cases on accounts and limits alone.

The cases that need the bootstrap or the profiles, and `durationMs`, are mailroom-mcp's
(`mcp/tests/test_config.py`). Each replaces only the first match, as JavaScript's
`String.replace` does.
"""

import dataclasses
import os
import re
import tomllib
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from mailroom_core.config import (
    ConfigError,
    CoreSettings,
    LoadedCore,
    core_settings_or_issues,
    issues_text,
    loaded_core,
    sealed_env_secrets,
    time_zone_name,
    without_secret_envs,
)
from mailroom_core.crypto import secrets_subkey
from mailroom_core.testing.config import example_config_text

if TYPE_CHECKING:
    from collections.abc import Mapping

EXAMPLE = example_config_text()

ENV = {
    "HOME_TOKEN": "home-secret-value",
    "WORK_APP_PASSWORD": "google-secret-value",
}


def parse_core(text: str, env: Mapping[str, str] = ENV) -> LoadedCore:
    """The accounts and limits of a whole config file, imported as on a first start."""
    raw = tomllib.loads(text)
    seed, secret_envs = without_secret_envs(
        {key: raw[key] for key in ("limits", "account") if key in raw}
    )
    settings = core_settings_or_issues(seed)
    if not isinstance(settings, CoreSettings):
        raise ConfigError(f"invalid settings (config file):\n{issues_text(settings)}")
    subkey = secrets_subkey(os.urandom(32))
    sealed = sealed_env_secrets(subkey, secret_envs, env)
    return loaded_core(time_zone_name(raw["timezone"]), settings, subkey, sealed)


def config_error(text: str, env: Mapping[str, str] = ENV) -> str:
    with pytest.raises(ConfigError) as error:
        parse_core(text, env)
    return str(error.value)


def test_makes_a_lone_identity_the_default_one() -> None:
    config = parse_core(EXAMPLE).config
    assert config.accounts[1].identities[0].default is True


def test_freezes_the_result() -> None:
    loaded = parse_core(EXAMPLE)
    config = loaded.config
    with pytest.raises(ValidationError):
        config.accounts[0].id = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.accounts = ()  # type: ignore[misc]
    with pytest.raises(TypeError):
        config.secrets["other"] = "x"  # type: ignore[index]
    assert isinstance(config.accounts, tuple)
    assert isinstance(config.accounts[0].identities, tuple)
    assert "secret-value" not in repr(loaded)


def test_rejects_plaintext_tls_and_missing_endpoint_fields() -> None:
    assert "smtp.tls" in config_error(
        EXAMPLE.replace('port = 465, tls = "implicit"', 'port = 465, tls = "none"', 1)
    )
    assert "imap.port" in config_error(
        EXAMPLE.replace('port = 993, tls = "implicit" }', 'tls = "implicit" }', 1)
    )


def test_takes_a_time_zone_in_any_letter_case_and_gives_its_iana_name() -> None:
    assert time_zone_name("europe/rome") == "Europe/Rome"
    assert time_zone_name("utc") == "UTC"
    with pytest.raises(ConfigError, match=r"""^time zone 'Europe/Nowhere' must be an IANA"""):
        time_zone_name("Europe/Nowhere")


def test_words_an_empty_string_as_zod_does() -> None:
    assert (
        "✖ Too small: expected string to have >=1 characters\n  → at account[0].login"
        in config_error(EXAMPLE.replace('login      = "me@example.net"', 'login      = ""', 1))
    )


def test_rejects_an_identity_used_by_two_accounts() -> None:
    text = EXAMPLE.replace(
        'identities   = [{ address = "it@team.example" }]',
        'identities   = [{ address = "CLUB@example.net" }]',
        1,
    )
    assert 'club@example.net is already used by account "home"' in config_error(text)


def test_requires_exactly_one_default_among_several_identities() -> None:
    assert "exactly one identity must be marked default" in config_error(
        EXAMPLE.replace(", default = true }", " }", 1)
    )


def test_warns_when_an_identity_is_outside_the_login_domain() -> None:
    text = EXAMPLE.replace(
        '{ address = "ada@example.net", name = "Ada" }',
        '{ address = "ale@example.org", name = "Ada" }',
        1,
    )
    assert parse_core(text).warnings == (
        'account "home": identity ale@example.org is outside the login\'s domain; the SMTP server may refuse it',
    )


def test_refuses_duplicate_account_ids_and_logins_that_differ_only_in_case() -> None:
    first = EXAMPLE.index("[[account]]")
    second = EXAMPLE[first : EXAMPLE.index("[[account]]", first + 1)]

    def with_copy(copy: str) -> str:
        return EXAMPLE.replace("# One connector URL", f"{copy}\n# One connector URL", 1)

    def one_identity(text: str) -> str:
        return re.sub(
            r"identities = \[[\s\S]*?\]\n",
            'identities = [{ address = "x@example.net" }]\n',
            text,
            count=1,
        )

    assert 'duplicate account id "home"' in config_error(with_copy(one_identity(second)))
    assert 'duplicate login "ME@example.net"' in config_error(
        with_copy(
            one_identity(
                second.replace('id         = "home"', 'id         = "upper"', 1).replace(
                    'login      = "me@example.net"', 'login      = "ME@example.net"', 1
                )
            )
        )
    )


def test_refuses_malformed_auto_approve_rules_and_warns_where_they_do_nothing() -> None:
    assert "auto_approve" in config_error(
        EXAMPLE.replace('auto_approve = ["@team.example"]', 'auto_approve = ["team.example"]', 1)
    )
    assert parse_core(
        EXAMPLE.replace('send_mode    = "approval"', 'send_mode    = "drafts"', 1)
    ).warnings == ('account "work": auto_approve has no effect with send_mode "drafts"',)
