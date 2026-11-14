"""A user's limits on their mail, the same for the web and MCP (sends per hour, recipients,
attachments, the window a body is read in, a message moved to another account): kept in
mailroom-core's settings document beside the accounts, and changed only by the user from the
web, never through MCP. Raises ``mailroom.errors.Refusal``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel

from mailroom.accounts.service import (
    ATTACHMENT_MAX_MB,
    MOVE_MAX_MB,
    account_refusal,
    attachment_limit_mb,
    move_limit_mb,
)
from mailroom.accounts.store import AccountError, load_accounts, save_limits
from mailroom.errors import Refusal
from mailroom_core.store import RevisionConflict, SettingsBusy

if TYPE_CHECKING:
    from mailroom.context import AppContext
    from mailroom_core.config import Limits

logger = logging.getLogger(__name__)


class UserLimits(BaseModel):
    send_per_hour: int
    """Messages handed to SMTP in any hour, across the user's accounts."""
    max_recipients: int
    """To, Cc and Bcc together, per message."""
    body_chars: int
    """The window of a message's text read at a time, on the web and through MCP."""
    attachment_max_mb: float
    """A written message's attachments together, at most ``ATTACHMENT_MAX_MB`` whatever is
    stored."""
    move_max_mb: float | None = None
    """A message moved to another account, at most ``MOVE_MAX_MB`` whatever is stored. Always
    given back; a change without it keeps the current one."""


def _user_limits(limits: Limits) -> UserLimits:
    return UserLimits(
        send_per_hour=limits.send_per_hour,
        max_recipients=limits.max_recipients,
        body_chars=limits.body_chars,
        attachment_max_mb=attachment_limit_mb(limits),
        move_max_mb=move_limit_mb(limits),
    )


def user_limits(ctx: AppContext, user_id: int) -> UserLimits:
    """The user's limits: mailroom-core's defaults until they change one."""
    return _user_limits(load_accounts(ctx.store(user_id)).settings.limits)


def change_limits(ctx: AppContext, user_id: int, limits: UserLimits) -> UserLimits:
    """Replace the user's limits; each one out of bounds, an attachment limit above
    ``ATTACHMENT_MAX_MB`` or a move limit above ``MOVE_MAX_MB`` too, is an issue of one
    ``invalid_limits``. Without ``move_max_mb`` the current one stays."""
    raw = limits.model_dump()
    try:
        if raw["move_max_mb"] is None:
            raw["move_max_mb"] = move_limit_mb(load_accounts(ctx.store(user_id)).settings.limits)
        saved = save_limits(ctx.store(user_id), raw, ATTACHMENT_MAX_MB, MOVE_MAX_MB)
    except AccountError as exc:
        raise account_refusal(exc) from None
    except RevisionConflict:
        # The document is read again on each save: this is only a save at the same moment.
        msg = "Your settings were being saved at the same time: try again."
        raise Refusal("conflict", "settings_changed", msg) from None
    except SettingsBusy:
        msg = "Your settings are being saved elsewhere: try again."
        raise Refusal("unavailable", "settings_busy", msg) from None
    logger.info("limits_changed", extra={"event": "limits_changed", "user_id": user_id})
    return _user_limits(saved)
