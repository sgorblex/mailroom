"""The account and limit settings: core's half of `src/config/schema.ts` (`settingsSchema`).

The same rules hold for every edit on the settings page and for plain config
(`mail.connections_from`).
"""

import re
from functools import cache
from typing import TYPE_CHECKING, Annotated, Final, Literal, NewType, get_args
from zoneinfo import available_timezones

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    Strict,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
)

from mailroom_core.config.errors import ConfigError, ConfigIssue, config_problem, validation_issues

if TYPE_CHECKING:
    from collections.abc import Callable

type SendMode = Literal["off", "drafts", "approval", "direct"]
SEND_MODES: Final[tuple[SendMode, ...]] = get_args(SendMode.__value__)

MAX_SAFE_INTEGER: Final = 2**53 - 1
"""The largest integer a JavaScript number holds exactly: the TypeScript server's bound on
integers and durations, kept so that settings it accepted still load."""

TimeZoneName = NewType("TimeZoneName", str)
"""An IANA time zone known here: made by `time_zone_name` or by a `TimeZone` field only."""
_TIME_ZONE_PROBLEM = 'must be an IANA time zone, e.g. "Europe/Rome"'


@cache
def _time_zones() -> dict[str, str]:
    return {zone.lower(): zone for zone in available_timezones()}


def _canonical_time_zone(zone: str) -> str | None:
    """The IANA name `zone` spells in any letter case, as Intl accepts it; None if unknown."""
    return _time_zones().get(zone.lower()) if zone.isascii() else None


def time_zone_name(zone: str) -> TimeZoneName:
    """`zone` checked as an IANA time zone and given its canonical case; raises `ConfigError`
    otherwise."""
    canonical = _canonical_time_zone(zone)
    if canonical is None:
        raise ConfigError(f"time zone {zone!r} {_TIME_ZONE_PROBLEM}")
    return TimeZoneName(canonical)


def _check_time_zone(zone: TimeZoneName) -> TimeZoneName:
    canonical = _canonical_time_zone(zone)
    if canonical is None:
        raise config_problem(_TIME_ZONE_PROBLEM)
    return TimeZoneName(canonical)


TimeZone = Annotated[TimeZoneName, Strict(), AfterValidator(_check_time_zone)]
"""A config field holding an IANA time zone, e.g. `Europe/Rome`."""

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
# zod's email check, so that both servers accept the same addresses.
_EMAIL = re.compile(
    r"(?:[A-Za-z0-9_'+\-]+\.)*[A-Za-z0-9_'+\-]*[A-Za-z0-9_+-]@(?:[A-Za-z0-9][A-Za-z0-9\-]*\.)+[A-Za-z]{2,}"
)
_RECIPIENT_RULE = re.compile(r"(?:[^\s@]+@|@)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")


def _matching(pattern: re.Pattern[str], message: str) -> Callable[[str], str]:
    def check(text: str) -> str:
        if pattern.fullmatch(text) is None:
            raise config_problem(message)
        return text

    return check


Slug = Annotated[
    StrictStr,
    AfterValidator(_matching(_SLUG, "must be lowercase letters, digits and dashes")),
]
"""Ids that appear in URLs and tool arguments: `account = "<id>"`."""
NonEmpty = Annotated[StrictStr, Field(min_length=1)]
_Email = Annotated[StrictStr, AfterValidator(_matching(_EMAIL, "Invalid email address"))]
_RecipientRule = Annotated[
    StrictStr,
    AfterValidator(
        _matching(
            _RECIPIENT_RULE,
            "must be an address (ana@example.org) or @ plus a domain (@example.org)",
        )
    ),
]
"""An exact recipient or a whole domain: `ana@example.org`, `@example.org`."""
_Count = Annotated[StrictInt, Field(ge=1, le=MAX_SAFE_INTEGER)]


class StrictModel(BaseModel):
    """Base of the config models: unknown keys are an error, what is parsed is read-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Endpoint(StrictModel):
    """Host, port and TLS mode of an account's IMAP or SMTP server."""

    host: NonEmpty
    port: Annotated[StrictInt, Field(ge=1, le=65_535)]
    tls: Literal["implicit", "starttls"]


class Identity(StrictModel):
    """An address an account may send from, with an optional display name."""

    address: _Email
    name: NonEmpty | None = None
    default: StrictBool = False


class PasswordAuth(StrictModel):
    """A password or token, stored sealed in the database."""

    type: Literal["password"]


# A union discriminated on `type` once there is a second kind (0.4: xoauth2).
type AccountAuth = PasswordAuth


class AccountConfig(StrictModel):
    """One mail account: login, send mode, servers, identities and auto-approved recipients."""

    id: Slug
    login: NonEmpty
    send_mode: SendMode
    auth: AccountAuth
    imap: Endpoint
    smtp: Endpoint
    identities: Annotated[tuple[Identity, ...], Field(min_length=1)]
    auto_approve: tuple[_RecipientRule, ...] = ()
    """Recipients (`addr@x` or `@domain`) sent directly in `approval` mode."""


class Limits(StrictModel):
    """Sends per hour and recipients per send, body characters a read shows, attachment size."""

    send_per_hour: _Count = 20
    max_recipients: _Count = 10
    body_chars: Annotated[StrictInt, Field(ge=500, le=MAX_SAFE_INTEGER)] = 6000
    attachment_max_mb: Annotated[StrictFloat, Field(gt=0, allow_inf_nan=False)] = 10.0


class CoreSettings(StrictModel):
    """Accounts and limits, as stored; only `core_settings_or_issues` makes one with its rules."""

    limits: Limits = Limits()
    account: tuple[AccountConfig, ...] = ()


def core_settings_or_issues(raw: object) -> CoreSettings | tuple[ConfigIssue, ...]:
    """The settings, valid and with a lone identity made the default; else what is wrong."""
    try:
        settings = CoreSettings.model_validate({} if raw is None else raw)
    except ValidationError as error:
        return tuple(validation_issues(error))
    issues = _cross_check(settings)
    if issues:
        return issues
    # A single identity is the default one whether or not it says so.
    return settings.model_copy(
        update={
            "account": tuple(
                acc.model_copy(
                    update={"identities": (acc.identities[0].model_copy(update={"default": True}),)}
                )
                if len(acc.identities) == 1
                else acc
                for acc in settings.account
            )
        }
    )


def _cross_check(settings: CoreSettings) -> tuple[ConfigIssue, ...]:
    issues: list[ConfigIssue] = []
    ids: set[str] = set()
    logins: set[str] = set()
    addresses: dict[str, str] = {}
    for i, acc in enumerate(settings.account):
        if acc.id in ids:
            issues.append(ConfigIssue(("account", i, "id"), f'duplicate account id "{acc.id}"'))
        ids.add(acc.id)
        # Tools accept a login in any case, so two logins differing only in case would be
        # ambiguous.
        login = acc.login.lower()
        if login in logins:
            issues.append(ConfigIssue(("account", i, "login"), f'duplicate login "{acc.login}"'))
        logins.add(login)

        defaults = sum(identity.default for identity in acc.identities)
        if len(acc.identities) > 1 and defaults != 1:
            issues.append(
                ConfigIssue(
                    ("account", i, "identities"),
                    f"exactly one identity must be marked default (found {defaults})",
                )
            )
        for j, identity in enumerate(acc.identities):
            address = identity.address.lower()
            owner = addresses.get(address)
            if owner is not None:
                issues.append(
                    ConfigIssue(
                        ("account", i, "identities", j, "address"),
                        f'identity {address} is already used by account "{owner}"',
                    )
                )
            addresses[address] = acc.id
    return tuple(issues)
