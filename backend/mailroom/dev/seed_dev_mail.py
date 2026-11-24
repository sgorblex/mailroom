"""Give a user the dev Dovecot's mailboxes: ``seed_dev_mail <username>``.

Adds (or resets) the accounts with the logins ``dev`` and ``team`` on the dev mail backends
(``mise run dev-mail``: IMAPS 127.0.0.1:33993, SMTPS 127.0.0.1:33025, any login with
``DEV_MAIL_PASSWORD``), then puts sample mail in each INBOX that is still empty: a few senders,
old and new, one starred, for trying filters on. Each INBOX also gets the HTML samples it does not
have yet (found again by their Message-ID), for trying the HTML view, dark HTML and Read as text:
a newsletter with no colours of its own, one with its own colours and an inline image, and two
from one sender. The database must be migrated (``mise run migrate``) and
``MAILROOM_SECRETS_KEY`` set.
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from functools import partial
from typing import TYPE_CHECKING, Final

from sqlalchemy.exc import SQLAlchemyError

from mailroom import database
from mailroom.accounts.secrets_key import secrets_subkey_from_env
from mailroom.accounts.store import AccountError, load_accounts, save_account, user_store
from mailroom.auth.users import user_by_username
from mailroom.db_migrations import applied_revisions, expected_heads
from mailroom.mail.connections import mail_ssl_context
from mailroom_core.mail import MailConnections, MailError
from mailroom_core.store import RevisionConflict, SettingsBusy

if TYPE_CHECKING:
    from mailroom_core.config import AccountConfig
    from mailroom_core.mail import MailboxContext
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


@dataclass(frozen=True, slots=True)
class _HtmlSample:
    key: str
    """Names the Message-ID, by which a later run finds the sample again."""
    sender: str
    subject: str
    age: int
    """In days."""
    text: str
    html: str
    image: str | None = None
    """The Content-ID of a small PNG the HTML shows inline (`cid:`), if any."""


_LIBRARY: Final = "City Library <updates@library.example>"

_HTML_SAMPLES: Final = (
    _HtmlSample(
        key="garden-newsletter",
        sender="Garden Club <club@garden.example>",
        subject="November in the garden",
        age=1,
        text=(
            "November in the garden\n\nThe days are shorter, but there is still plenty to do.\n\n"
            "- Plant tulip bulbs before the first frost.\n- Rake leaves into a corner.\n"
            "- Bring tender pots under cover.\n\nThe Garden Club\n"
        ),
        html=(
            "<html><body>"
            "<h1>November in the garden</h1>"
            "<p>The days are shorter, but there is still plenty to do outside.</p>"
            "<h2>This month</h2>"
            "<ul><li>Plant tulip and daffodil bulbs before the first frost.</li>"
            "<li>Rake leaves into a corner: by spring they make good mulch.</li>"
            "<li>Bring tender pots indoors or under cover.</li></ul>"
            "<blockquote>The best time to plant a tree was twenty years ago. "
            "The second best time is now.</blockquote>"
            '<p>The whole calendar is on <a href="https://garden.example/calendar">our site</a>.'
            "</p>"
            "<p>Happy gardening,<br>The Garden Club</p>"
            "</body></html>"
        ),
    ),
    _HtmlSample(
        key="travel-colours",
        sender="Travel Deals <deals@travel.example>",
        subject="Weekend trips by train",
        age=2,
        text=(
            "Weekend trips by train\n\nThree short trips for the next long weekend.\n\n"
            "Prices are examples: nothing here is for sale.\n"
        ),
        html=(
            '<html><body style="margin:0;background-color:#eef2f7;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'bgcolor="#eef2f7"><tr><td align="center" style="padding:24px;">'
            '<table role="presentation" width="560" cellpadding="0" cellspacing="0" '
            'style="background-color:#ffffff;border-radius:8px;">'
            '<tr><td style="background-color:#1f4e79;color:#ffffff;padding:20px;'
            'font-family:Arial,sans-serif;">'
            '<img src="cid:logo@travel.example" width="48" height="48" alt="Travel Deals" '
            'style="vertical-align:middle;"> '
            '<span style="font-size:22px;font-weight:bold;">Weekend trips</span></td></tr>'
            '<tr><td style="padding:20px;color:#333333;font-family:Arial,sans-serif;'
            'font-size:15px;line-height:1.5;">'
            "<p>Three short trips for the next long weekend, all by train.</p>"
            '<p style="background-color:#fff4d6;color:#7a5200;padding:12px;border-radius:4px;">'
            "Prices are examples: nothing here is for sale.</p>"
            '<p><a href="https://travel.example/trips" style="display:inline-block;'
            "background-color:#e8743b;color:#ffffff;padding:10px 18px;border-radius:4px;"
            'text-decoration:none;">See the trips</a></p></td></tr>'
            '<tr><td style="background-color:#f7f7f7;color:#777777;padding:12px 20px;'
            'font-size:12px;font-family:Arial,sans-serif;">'
            "Sample mail from mise run seed-dev-mail.</td></tr>"
            "</table></td></tr></table></body></html>"
        ),
        image="logo@travel.example",
    ),
    _HtmlSample(
        key="library-loan",
        sender=_LIBRARY,
        subject="Your loan is due on Friday",
        age=0,
        text="Your loan is due on Friday\n\nThe Little Prince, due back on Friday.\n",
        html=(
            "<html><body><p>Hello,</p>"
            "<p>This book is due back on <strong>Friday</strong>:</p>"
            "<table><tr><th>Title</th><th>Due</th></tr>"
            "<tr><td>The Little Prince</td><td>Friday</td></tr></table>"
            '<p><a href="https://library.example/loans">Renew it online</a>.</p>'
            "</body></html>"
        ),
    ),
    _HtmlSample(
        key="library-arrivals",
        sender=_LIBRARY,
        subject="New arrivals this week",
        age=6,
        text="New arrivals this week\n\n- A field guide to mushrooms\n- Bread at home\n",
        html=(
            "<html><body><h2>New arrivals this week</h2>"
            "<ul><li><em>A field guide to mushrooms</em></li><li><em>Bread at home</em></li></ul>"
            "<p>Reserve them at the desk or online.</p></body></html>"
        ),
    ),
)


def _account(login: str, account_id: int | None) -> dict[str, Json]:
    address = f"{login}@{DEV_DOMAIN}"
    account: dict[str, Json] = {
        "name": login,
        "login": login,
        "send_mode": "drafts",
        "auth": {"type": "password"},
        "imap": {"host": DEV_HOST, "port": DEV_IMAP_PORT, "tls": "implicit"},
        "smtp": {"host": DEV_HOST, "port": DEV_SMTP_PORT, "tls": "implicit"},
        "identities": [{"address": address, "name": f"Dev {login}"}],
    }
    return account if account_id is None else {"id": account_id, **account}


def _message(sender: str, subject: str, recipient: str, date: datetime) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Date"] = format_datetime(date)
    message["Message-ID"] = make_msgid(domain=DEV_DOMAIN)
    message.set_content(f"{subject}.\n\nSample mail from mise run seed-dev-mail.\n")
    return message.as_bytes()


def _png(size: int, rgb: tuple[int, int, int]) -> bytes:
    """A square PNG of one colour."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + bytes(rgb) * size
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


def _sample_id(sample: _HtmlSample, login: str) -> str:
    return f"<{sample.key}.{login}@{DEV_DOMAIN}>"


def _html_message(sample: _HtmlSample, login: str, date: datetime) -> bytes:
    """Text and HTML alternatives, the HTML with its inline image (multipart/related) if any."""
    message = EmailMessage()
    message["From"] = sample.sender
    message["To"] = f"{login}@{DEV_DOMAIN}"
    message["Subject"] = sample.subject
    message["Date"] = format_datetime(date)
    message["Message-ID"] = _sample_id(sample, login)
    message.set_content(sample.text)
    message.add_alternative(sample.html, subtype="html")
    if sample.image is not None:
        html = next(p for p in message.iter_parts() if p.get_content_type() == "text/html")
        html.add_related(
            _png(48, (232, 116, 59)), "image", "png", cid=f"<{sample.image}>", filename="logo.png"
        )
    return message.as_bytes()


def _seeded(login: str, context: MailboxContext) -> int:
    """Put the plain samples in the selected INBOX of ``login`` if it is empty, and the HTML
    samples it does not have yet; how many went in."""
    now = datetime.now(UTC)
    recipient = f"{login}@{DEV_DOMAIN}"
    messages: list[tuple[bytes, bool, datetime]] = []
    if not context.exists:
        for sender, subject, age, starred in _SAMPLES:
            date = now - timedelta(days=age)
            messages.append((_message(sender, subject, recipient, date), starred, date))
    for sample in _HTML_SAMPLES:
        if context.exists and context.client.search(
            ["HEADER", "Message-ID", _sample_id(sample, login)]
        ):
            continue
        date = now - timedelta(days=sample.age)
        messages.append((_html_message(sample, login, date), False, date))
    for raw, starred, date in messages:
        context.client.append("INBOX", raw, flags=(b"\\Flagged",) if starred else (), msg_time=date)
    return len(messages)


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
        for login in ACCOUNTS:
            stored = load_accounts(store).settings.account
            account_id = next((a.id for a in stored if a.login == login), None)
            configs.append(
                save_account(
                    store, subkey, _account(login, account_id), password, new=account_id is None
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

    connections = MailConnections.from_accounts(
        configs,
        {config.id: password for config in configs},
        ssl_context=mail_ssl_context(),
    )
    try:
        for config in configs:
            try:
                added = connections.with_mailbox(
                    config.id, "INBOX", False, partial(_seeded, config.login)
                )
            except MailError as exc:
                print(
                    f"{config.login}: cannot seed the dev Dovecot ({exc}); `mise run dev-mail`, "
                    "and MAILROOM_MAIL_CA_FILE naming its CA.",
                    file=sys.stderr,
                )
                return 1
            if added:
                print(f"{config.login}: {added} sample messages in INBOX.")
            else:
                print(f"{config.login}: INBOX already has the samples; left as it is.")
    finally:
        connections.close_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
