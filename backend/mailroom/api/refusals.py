"""Refusals the web app words in the reader's language: a ``detail`` carrying a ``code``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi import HTTPException

if TYPE_CHECKING:
    from mailroom.errors import Refusal, RefusalKind


def refused(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


_MAIL_STATUS: Final[dict[str, int]] = {
    "password_missing": 409,
    "starttls_unsupported": 409,
    "auth_failed": 409,
    "stale_reference": 409,
    "send_disabled": 409,
    "timeout": 504,
    "mailbox_missing": 404,
    "not_found": 404,
    "busy": 429,
    "rate_limited": 429,
    "invalid_argument": 400,
    "too_large": 413,
}
"""The rest (``unreachable``, ``unsupported``, ``recipient_refused``, ``server_error``, the filter
runner's own codes) are 502."""


def mail_status(code: str) -> int:
    """A mailbox failure's status: by mailroom-core's code (``docs/requirements/mail-client.md``,
    *Errors*), 409 when the account needs fixing, 502 for the server and any other code. (Without
    the secrets key it is no mailbox failure: ``mailroom.errors.mail_refusal``.)"""
    return _MAIL_STATUS.get(code, 502)


_STATUS: Final[dict[RefusalKind, int]] = {
    "not_found": 404,
    "invalid": 400,
    "conflict": 409,
    "busy": 429,
    "unavailable": 503,
    "timeout": 504,
    "failed": 500,
}


def http_refusal(refusal: Refusal) -> HTTPException:
    """``refusal`` as the API answers it: a ``not_found`` with its message alone, as a missing
    path id, unless it is ``coded``; anything else ``{"code", "message"}`` (and ``issues`` when it
    has them), its status by kind (a mailbox failure's by ``mail_status``)."""
    if refusal.kind == "not_found" and not refusal.coded:
        return HTTPException(status_code=404, detail=refusal.message)
    status = mail_status(refusal.code) if refusal.kind == "mail" else _STATUS[refusal.kind]
    detail: dict[str, object] = {"code": refusal.code, "message": refusal.message}
    if refusal.issues is not None:
        detail["issues"] = refusal.issues
    return HTTPException(status_code=status, detail=detail)
