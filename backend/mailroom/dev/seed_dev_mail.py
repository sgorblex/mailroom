"""Give a user the dev Dovecot's mailboxes: ``seed_dev_mail <username>``.

Adds (or resets) the accounts ``dev`` and ``team`` on the dev mail backends (``mise run dev-mail``:
IMAPS 127.0.0.1:33993, SMTPS 127.0.0.1:33025, any login with ``DEV_MAIL_PASSWORD``), then puts
sample mail in each INBOX that is still empty: a few senders, old and new, one starred, for trying
filters on. The database must be migrated (``mise run migrate``) and ``MAILROOM_SECRETS_KEY`` set.
"""

from __future__ import annotations

import argparse
import imaplib
import os
import sys
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from typing import TYPE_CHECKING, Final

from sqlalchemy.exc import SQLAlchemyError

from mailroom import database
from mailroom.accounts.mail import MailFailure, imap_account, mail_ssl_context
from mailroom.accounts.secrets_key import secrets_subkey_from_env
from mailroom.accounts.store import AccountError, load_accounts, save_account, user_store
from mailroom.auth.users import user_by_username
from mailroom.db_migrations import applied_revisions, expected_heads
from mailroom_core.store import RevisionConflict, SettingsBusy

if TYPE_CHECKING:
    from mailroom_core.config import AccountConfig
    from mailroom_core.store import Json

DEV_HOST: Final = "127.0.0.1"
DEV_IMAP_PORT: Final = 33993
DEV_SMTP_PORT: Final = 33025
DEV_DOMAIN: Final = "mailroom.test"
ACCOUNTS: Final = ("dev", "team")

# (sender, subject, age in days, starred)
_SAMPLES: Final = (
    ("Shop Newsletter <news@shop.example>", "Autumn sale: 30% off everything", 2, False),
    ("Shop Newsletter <news@shop.example>", "Your weekly picks", 45, False),
    ("Bank Alerts <alerts@bank.example>", "Your statement is ready", 1, False),
    ("Bank Alerts <alerts@bank.example>", "Card payment of 42.00 EUR", 90, False),
    ("CI <ci@builds.example>", "Build #1234 failed on main", 0, False),
    ("CI <ci@builds.example>", "Build #1201 passed", 30, False),
    ("Ana Rossi <ana@friends.example>", "Dinner on Saturday?", 3, True),
    ("Ana Rossi <ana@friends.example>", "Photos from the trip", 120, False),
)


def _account(account_id: str) -> dict[str, Json]:
    address = f"{account_id}@{DEV_DOMAIN}"
    return {
        "id": account_id,
        "login": account_id,
        "send_mode": "drafts",
        "auth": {"type": "password"},
        "imap": {"host": DEV_HOST, "port": DEV_IMAP_PORT, "tls": "implicit"},
        "smtp": {"host": DEV_HOST, "port": DEV_SMTP_PORT, "tls": "implicit"},
        "identities": [{"address": address, "name": f"Dev {account_id}"}],
    }


def _message(sender: str, subject: str, recipient: str, date: datetime) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = format_datetime(date)
    message["Message-ID"] = make_msgid(domain=DEV_DOMAIN)
    message.set_content(f"{subject}.\n\nSample mail from mise run seed-dev-mail.\n")
    return message.as_bytes()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seed-dev-mail", description=__doc__)
    parser.add_argument("username", help="the Mailroom user who gets the accounts")
    args = parser.parse_args(argv)

    subkey = secrets_subkey_from_env()
    if subkey is None:
        return 1
    password = os.environ.get("DEV_MAIL_PASSWORD", "pass")
    database.load_local_environment()
    try:
        engine = database.create_database_engine(database.get_sync_database_url())
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        if applied_revisions(engine) != expected_heads():
            print("The database is not migrated: run `mise run migrate` first.", file=sys.stderr)
            return 1
        with database.create_session_factory(engine)() as db:
            user = user_by_username(db, args.username)
        if user is None:
            print(f"No user {args.username!r}: `mise run create-user` first.", file=sys.stderr)
            return 1
        store = user_store(engine, user.id)
        configs: list[AccountConfig] = []
        for account_id in ACCOUNTS:
            existing = {account.id for account in load_accounts(store).settings.account}
            configs.append(
                save_account(
                    store, subkey, _account(account_id), password, new=account_id not in existing
                )
            )
    except (AccountError, RevisionConflict, SettingsBusy) as exc:
        print(exc, file=sys.stderr)
        return 1
    except SQLAlchemyError as exc:
        print(f"Could not reach the database: {type(exc).__name__}.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print(f"{user.username} has the accounts {', '.join(ACCOUNTS)}.")

    ssl_context = mail_ssl_context()
    now = datetime.now(UTC)
    for config in configs:
        account_id = config.id
        try:
            conn = imap_account(config, password, ssl_context).connect()
        except (MailFailure, OSError, imaplib.IMAP4.error) as exc:
            print(
                f"{account_id}: cannot reach the dev Dovecot ({exc}); `mise run dev-mail`, and "
                "MAILROOM_MAIL_CA_FILE naming its CA.",
                file=sys.stderr,
            )
            return 1
        try:
            if conn.select_folder("INBOX")[b"EXISTS"]:
                print(f"{account_id}: INBOX already has mail; left as it is.")
                continue
            recipient = f"{account_id}@{DEV_DOMAIN}"
            for sender, subject, age, starred in _SAMPLES:
                date = now - timedelta(days=age)
                conn.append(
                    "INBOX",
                    _message(sender, subject, recipient, date),
                    flags=(b"\\Flagged",) if starred else (),
                    msg_time=date,
                )
            print(f"{account_id}: {len(_SAMPLES)} sample messages in INBOX.")
        finally:
            conn.logout()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
