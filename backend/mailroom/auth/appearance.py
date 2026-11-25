"""How the web looks to a user, the same on every device. Choices of one device alone (pane
widths, a collapsed folder pane) stay in that browser.

The user's row keeps only the choices they made, so a later change of a default reaches whoever
never chose; reading fills the rest, and a stored choice that is no longer an option (one since
removed) reads as that choice's default rather than failing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

Theme = Literal["system", "light", "dark"]
Accent = Literal["default", "blue", "violet", "green", "terracotta", "rose"]
Density = Literal["comfortable", "compact"]
TextSize = Literal["small", "default", "large"]
ReadingPane = Literal["right", "below", "off"]
Home = Literal["dashboard", "mail", "filters", "accounts", "activity"]
SwipeAction = Literal["none", "archive", "trash", "read", "star", "handled"]


class Appearance(BaseModel):
    """Every choice, each with its default."""

    model_config = ConfigDict(extra="forbid")

    theme: Theme = "system"
    accent: Accent = "default"
    density: Density = "comfortable"
    text_size: TextSize = "default"
    reading_pane: ReadingPane = "right"
    home: Home = "dashboard"
    """The page the web opens on."""
    swipe_left: SwipeAction = "trash"
    """What a message list row does when swiped left on a touch screen."""
    swipe_right: SwipeAction = "archive"


class AppearanceChange(BaseModel):
    """Some of the choices, at least one; the others stay as they are."""

    model_config = ConfigDict(extra="forbid")

    theme: Theme | None = None
    accent: Accent | None = None
    density: Density | None = None
    text_size: TextSize | None = None
    reading_pane: ReadingPane | None = None
    home: Home | None = None
    swipe_left: SwipeAction | None = None
    swipe_right: SwipeAction | None = None

    @model_validator(mode="after")
    def _some_choice(self) -> AppearanceChange:
        if not self.chosen():
            msg = "Change at least one choice."
            raise ValueError(msg)
        return self

    def chosen(self) -> dict[str, str]:
        """The choices this change makes."""
        return self.model_dump(exclude_none=True)


def valid_choices(stored: dict[str, Any]) -> dict[str, Any]:
    """The choices of ``stored`` that are still options; a key no choice has is dropped too."""
    kept: dict[str, Any] = {}
    for name, value in stored.items():
        if name not in Appearance.model_fields:
            continue
        try:
            Appearance.model_validate({name: value})
        except ValidationError:
            continue
        kept[name] = value
    return kept


def appearance_of(stored: dict[str, Any]) -> Appearance:
    """``stored``'s choices over the defaults, each that is no longer an option left to its
    default."""
    return Appearance.model_validate(valid_choices(stored))
