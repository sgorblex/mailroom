"""Refusals the web app words in the reader's language: a ``detail`` carrying a ``code``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi import HTTPException

if TYPE_CHECKING:
    from mailroom.errors import Refusal, RefusalKind


def refused(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def mail_status(code: str) -> int:
    """A mailbox failure's status: 409 when the account needs fixing, 502 for the server. (Without
    the secrets key it is no mailbox failure: ``mailroom.errors.mail_refusal``.)"""
    return 409 if code in {"password_missing", "starttls_unsupported"} else 502


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
