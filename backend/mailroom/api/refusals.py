"""Refusals the web app words in the reader's language: a ``detail`` carrying a ``code``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from mailroom.accounts.mail import MailFailure


def refused(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def mail_status(code: str) -> int:
    """A mailbox failure's status: 503 without the secrets key, 409 when the account needs fixing,
    502 for the server."""
    if code == "secrets_key_missing":
        return 503
    return 409 if code in {"password_missing", "starttls_unsupported"} else 502


def mail_refusal(failure: MailFailure) -> HTTPException:
    return refused(mail_status(failure.code), failure.code, failure.message)
