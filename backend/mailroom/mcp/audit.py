"""One ``mcp_tool_called`` log line per tool call: who (user, client, grant), which tool, how it
ended and how long it took. Of the arguments only their names: their values may be anything the
model wrote, mail it read included."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

Outcome = Literal["ok", "refused", "invalid_arguments", "unknown_tool", "failed"]
"""``refused``: the app would not (a ``Refusal``). ``failed``: something unexpected."""


def log_tool_call(
    *,
    tool: str,
    user_id: int | None,
    client_id: str | None,
    grant_id: str | None,
    argument_keys: Iterable[str],
    outcome: Outcome,
    code: str | None,
    duration_ms: float,
) -> None:
    event = "mcp_tool_called"
    extra: dict[str, object] = {
        "event": event,
        "tool": tool,
        "user_id": user_id,
        "client_id": client_id,
        "grant_id": grant_id,
        "argument_keys": sorted(argument_keys),
        "outcome": outcome,
        "duration_ms": round(duration_ms, 1),
        "via": "mcp",
    }
    if code is not None:
        extra["code"] = code
    logger.log(logging.ERROR if outcome == "failed" else logging.INFO, event, extra=extra)
