"""Recorded mailbox failures take mailroom-core's error codes and fixed messages.

A run's ``error`` (and its ``result``'s ``error``, the same text) is ``"<code>: <message>"``.
Upgrading renames the filter engine bridge's old codes and replaces their message, which could
quote the server, with mailroom-core's fixed one: ``login_failed`` is ``auth_failed``,
``tls_failed`` is ``unreachable`` (saying the certificate did not verify when the old text did),
``imap_error`` is ``auth_failed``, ``mailbox_missing`` or ``server_error`` by the response code it
quoted, as mailroom-core decides, or ``unreachable`` for a lost connection (imaplib's abort,
``socket error``, ``EOF``); ``timeout`` and ``unreachable`` keep their code. Downgrading
renames the codes back and keeps the fixed messages: the server's wording is gone for good.

Revision ID: 202611121200
Revises: 202611101200
Create Date: 2026-11-12 12:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

revision: str = "202611121200"
down_revision: str | Sequence[str] | None = "202611101200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# mailroom-core's messages as of this revision, frozen here.
AUTH_FAILED = "auth_failed: The mail server rejected the login for this account."
UNREACHABLE = "unreachable: The mail server for this account could not be reached."
UNVERIFIED = f"{UNREACHABLE} Its TLS certificate did not verify."
TIMEOUT = "timeout: The mail server did not answer in time."
MAILBOX_MISSING = "mailbox_missing: That folder does not exist on this account."
SERVER_ERROR = "server_error: The mail server reported an error."


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _starts(text: str, code: str) -> str:
    return f"starts_with({text}, {_literal(f'{code}: ')})"


def _quotes(text: str, fragment: str) -> str:
    return f"strpos({text}, {_literal(fragment)}) > 0"


def upgraded(text: str) -> str:
    """A SQL expression: the error ``text`` (a SQL expression) with the new code and message."""
    tls, imap = _starts(text, "tls_failed"), _starts(text, "imap_error")
    # As mailroom-core's mail_error decides by the response code the old text quoted.
    missing = f"({_quotes(text, '[NONEXISTENT]')} OR {_quotes(text, '[TRYCREATE]')})"
    # imaplib's abort, a connection lost mid-command, which core calls unreachable.
    lost = f"({_quotes(text, 'socket error')} OR {_quotes(text, 'EOF')})"
    cases = [
        (_starts(text, "login_failed"), AUTH_FAILED),
        (f"{tls} AND {_quotes(text, 'CERTIFICATE_VERIFY_FAILED')}", UNVERIFIED),
        (tls, UNREACHABLE),
        (_starts(text, "timeout"), TIMEOUT),
        (_starts(text, "unreachable"), UNREACHABLE),
        (f"{imap} AND {_quotes(text, '[AUTHENTICATIONFAILED]')}", AUTH_FAILED),
        (f"{imap} AND {missing}", MAILBOX_MISSING),
        (f"{imap} AND {lost}", UNREACHABLE),
        (imap, SERVER_ERROR),
    ]
    whens = " ".join(f"WHEN {test} THEN {_literal(value)}" for test, value in cases)
    return f"CASE {whens} ELSE {text} END"


def downgraded(text: str) -> str:
    """A SQL expression: the error ``text`` with the old code, its message kept."""
    renames = [
        ("auth_failed", "login_failed", None),
        ("unreachable", "tls_failed", "Its TLS certificate did not verify."),
        ("mailbox_missing", "imap_error", None),
        ("server_error", "imap_error", None),
        ("unsupported", "imap_error", None),
    ]
    whens = []
    for new, old, ending in renames:
        test = _starts(text, new)
        if ending is not None:
            test += f" AND right({text}, {len(ending)}) = {_literal(ending)}"
        rest = f"substr({text}, {len(new) + 1})"
        whens.append(f"WHEN {test} THEN {_literal(old)} || {rest}")
    return f"CASE {' '.join(whens)} ELSE {text} END"


def _rewrite(rewritten: Callable[[str], str]) -> None:
    op.execute(f"UPDATE app.filter_run SET error = {rewritten('error')} WHERE error IS NOT NULL")
    in_result = "result->>'error'"
    op.execute(
        "UPDATE app.filter_run "
        f"SET result = jsonb_set(result, '{{error}}', to_jsonb({rewritten(in_result)})) "
        "WHERE jsonb_typeof(result->'error') = 'string'"
    )


def upgrade() -> None:
    _rewrite(upgraded)


def downgrade() -> None:
    _rewrite(downgraded)
