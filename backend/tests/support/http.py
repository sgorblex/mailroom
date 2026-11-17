"""An ``httpx.MockTransport`` that answers as a network transport does, for code that streams."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Callable


def streamed(handler: Callable[[httpx.Request], Any]) -> httpx.MockTransport:
    """``handler``'s answers with their body still to read, as sent: an
    ``httpx.Response(content=...)`` arrives read, and decoded."""

    async def handle(request: httpx.Request) -> httpx.Response:
        reply = handler(request)
        if inspect.isawaitable(reply):
            reply = await reply
        return httpx.Response(reply.status_code, headers=reply.headers, stream=reply.stream)

    return httpx.MockTransport(handle)
