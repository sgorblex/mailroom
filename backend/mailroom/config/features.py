"""``MAILROOM_FEATURES``: which optional features an instance runs on top of the base (users,
sign-in, accounts and their secrets, the settings screens). A feature that is off runs nothing:
no routes, no worker, no screens.

Specification: ``docs/REQUIREMENTS.md``, *Editions*.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Literal, get_args

FEATURES_ENV: Final = "MAILROOM_FEATURES"

Feature = Literal["mail", "mcp", "filters"]
FEATURE_NAMES: Final[tuple[Feature, ...]] = get_args(Feature)


@dataclass(frozen=True, slots=True)
class Features:
    mail: bool
    """The mail client: its REST routes and screens."""
    mcp: bool
    """The MCP server, its built-in authorization server, consent screen and connected apps."""
    filters: bool
    """Rule sets, their runs and the worker that runs them; the MCP ``filters_*`` tool groups."""

    @property
    def names(self) -> list[Feature]:
        """The features that are on, in the order mail, mcp, filters."""
        return [name for name in FEATURE_NAMES if getattr(self, name)]

    @property
    def description(self) -> str:
        """The features that are on, for a log line."""
        return ", ".join(self.names) or "none (the base only)"

    @property
    def mail_layer(self) -> bool:
        """Whether the shared mail connections and send runner exist: the mail client and the
        MCP mail tools both use them."""
        return self.mail or self.mcp


ALL_FEATURES: Final = Features(mail=True, mcp=True, filters=True)


def parsed_features(raw: str | None) -> Features:
    """``raw`` (``MAILROOM_FEATURES``, comma separated, case-insensitive): None means every
    feature, blank means none (the base only). Raises ``ValueError`` for a name that is no
    feature: the API then refuses to start rather than guess."""
    if raw is None:
        return ALL_FEATURES
    names = {name.strip().lower() for name in raw.split(",") if name.strip()}
    unknown = sorted(names.difference(FEATURE_NAMES))
    if unknown:
        msg = (
            f"{FEATURES_ENV} names an unknown feature ({', '.join(unknown)}); "
            f"the features are {', '.join(FEATURE_NAMES)}"
        )
        raise ValueError(msg)
    return Features(mail="mail" in names, mcp="mcp" in names, filters="filters" in names)


def features_from_env() -> Features:
    """``MAILROOM_FEATURES`` as ``parsed_features`` reads it."""
    return parsed_features(os.environ.get(FEATURES_ENV))
