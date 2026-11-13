"""Port of `src/config/load.test.ts`: its `parseConfig` cases on accounts and limits alone, checked
as core checks stored settings (`core_settings_or_issues`). Each replaces only the first match, as
JavaScript's `String.replace` does.
"""

import re
import tomllib

import pytest
from pydantic import ValidationError

from mailroom_core.config import (
    ConfigError,
    CoreSettings,
    core_settings_or_issues,
    issues_text,
    time_zone_name,
)
from mailroom_core.testing.config import example_config_text

EXAMPLE = example_config_text()


def parse_core(text: str) -> CoreSettings:
    """The accounts and limits of a whole config file as settings: the example names its secrets
    by `secret_env`, which stored settings never hold."""
    raw = tomllib.loads(text)
    for account in raw.get("account", []):
        account.get("auth", {}).pop("secret_env", None)
    settings = core_settings_or_issues(
        {key: raw[key] for key in ("limits", "account") if key in raw}
    )
    if not isinstance(settings, CoreSettings):
        raise ConfigError(f"invalid settings:\n{issues_text(settings)}")
    return settings


def config_error(text: str) -> str:
    with pytest.raises(ConfigError) as error:
        parse_core(text)
    return str(error.value)


def test_makes_a_lone_identity_the_default_one() -> None:
    assert parse_core(EXAMPLE).account[1].identities[0].default is True


def test_freezes_the_result() -> None:
    settings = parse_core(EXAMPLE)
    with pytest.raises(ValidationError):
        settings.account[0].id = "other"  # type: ignore[misc]
    assert isinstance(settings.account, tuple)
    assert isinstance(settings.account[0].identities, tuple)


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


def test_refuses_malformed_auto_approve_rules() -> None:
    assert "auto_approve" in config_error(
        EXAMPLE.replace('auto_approve = ["@team.example"]', 'auto_approve = ["team.example"]', 1)
    )
