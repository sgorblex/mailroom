from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

_REQUEST_ID_MAX_LEN = 128
_SAFE_REQUEST_ID = re.compile(r"^[!-~]+$")

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_or_create_request_id(incoming_header: str | None) -> str:
    if incoming_header is None:
        return str(uuid.uuid4())
    stripped = incoming_header.strip()
    if (
        stripped == ""
        or len(stripped) > _REQUEST_ID_MAX_LEN
        or _SAFE_REQUEST_ID.fullmatch(stripped) is None
    ):
        return str(uuid.uuid4())
    return stripped
