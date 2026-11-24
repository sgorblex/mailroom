from __future__ import annotations

from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    false,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mailroom.models.base import Base
from mailroom.models.user import User

INDEX_STATES: Final = ("pending", "syncing", "ready", "failed")
"""A folder's index: not built yet, being built or resynced, readable, or stopped by an error."""

_TRGM: Final = "gin_trgm_ops"
"""Unqualified here: the migration qualifies it with the schema pg_trgm lives in (``app`` unless
it was installed elsewhere), which the model cannot know."""


class MailIndexFolder(Base):
    """One folder of one of the user's accounts as the index knows it: whether it is indexed, the
    server's state it was last synced to, and its counts. The account lives in mailroom-core's
    schema ``core``, keyed by its id there."""

    __tablename__ = "mail_index_folder"
    __table_args__ = (
        UniqueConstraint("user_id", "account_id", "path"),
        # The target of the messages' foreign key, which keeps each row with its folder's user.
        UniqueConstraint("id", "user_id"),
        CheckConstraint(f"state IN ({', '.join(repr(s) for s in INDEX_STATES)})", name="state"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"))
    account_id: Mapped[int] = mapped_column(BigInteger)
    path: Mapped[str] = mapped_column(Text)
    """As the server names it (``INBOX`` in capitals)."""
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=true())
    """The user's switch; a folder off the index is read live."""
    inbox_subfolder: Mapped[bool] = mapped_column(Boolean, server_default=false())
    """Whether it is one of INBOX's subfolders that *All inboxes* may take in
    (``mailroom.mail.index.settings``), as the account's folders were last listed."""
    uid_validity: Mapped[int | None] = mapped_column(BigInteger)
    highest_modseq: Mapped[int | None] = mapped_column(BigInteger)
    """HIGHESTMODSEQ at the last sync; None without CONDSTORE."""
    uid_next: Mapped[int | None] = mapped_column(BigInteger)
    oldest_indexed: Mapped[date | None] = mapped_column(Date)
    """The age limit's day: messages that arrived since (SINCE, by the server's day) are indexed;
    None: all of them."""
    state: Mapped[str] = mapped_column(Text, server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    """The ``MailError`` code that failed the last sync, never its text."""
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, server_default="0")
    unread_count: Mapped[int] = mapped_column(Integer, server_default="0")


class MailIndexMessage(Base):
    """One message of an indexed folder, as ``mailroom_core.mail.IndexedMessage`` reads it: its
    metadata and snippet, never its body. Every field but the UID is mail-derived, untrusted text.
    On Gmail only All Mail is indexed, with each message's labels."""

    __tablename__ = "mail_index_message"
    __table_args__ = (
        ForeignKeyConstraint(
            ["folder_id", "user_id"],
            ["app.mail_index_folder.id", "app.mail_index_folder.user_id"],
            ondelete="CASCADE",
        ),
        # Newest first (keyset on arrival and UID) in a folder, and across the user's folders.
        Index("ix_mail_index_message_folder_newest", "folder_id", "received", "uid"),
        Index("ix_mail_index_message_user_newest", "user_id", "received", "folder_id", "uid"),
        Index(
            "ix_mail_index_message_unread",
            "folder_id",
            postgresql_where=text("NOT ('\\Seen' = ANY (flags))"),
        ),
        Index("ix_mail_index_message_thread_key", "user_id", "thread_key"),
        # A conversation's key: the message a new one answers, the replies stored before it.
        Index("ix_mail_index_message_message_id", "user_id", "message_id"),
        Index("ix_mail_index_message_references_root", "user_id", "references_root"),
        Index("ix_mail_index_message_in_reply_to", "user_id", "in_reply_to"),
        Index(
            "ix_mail_index_message_gm_msgid",
            "folder_id",
            "gm_msgid",
            postgresql_where=text("gm_msgid IS NOT NULL"),
        ),
        Index("ix_mail_index_message_labels", "labels", postgresql_using="gin"),
        # Substring search (ILIKE) on what a search's sender, to and subject compare.
        *(
            Index(
                f"ix_mail_index_message_{column}_trgm",
                column,
                postgresql_using="gin",
                postgresql_ops={column: _TRGM},
            )
            for column in ("subject", "sender_text", "to_text")
        ),
    )

    folder_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    uid: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    """The folder's user, kept on each row for queries across folders."""
    received: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    """INTERNALDATE, the arrival time."""
    date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """The Date header."""
    date_offset: Mapped[int | None] = mapped_column(SmallInteger)
    """The Date header's UTC offset, in minutes, which a read gives ``date`` in; None: UTC."""
    sender: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    """From, as ``[{"name", "address"}]``."""
    recipients: Mapped[dict[str, Any]] = mapped_column(JSONB)
    """To and Cc, as ``{"to": [...], "cc": [...]}``, each like ``sender``."""
    sender_text: Mapped[str] = mapped_column(Text)
    """From's names and addresses as one text, for a search's sender."""
    to_text: Mapped[str] = mapped_column(Text)
    """To's names and addresses as one text, for a search's to (To only, as IMAP's TO)."""
    subject: Mapped[str] = mapped_column(Text)
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text))
    """System flags (``\\Seen``, ``\\Flagged``...)."""
    keywords: Mapped[list[str]] = mapped_column(ARRAY(Text))
    size: Mapped[int] = mapped_column(BigInteger)
    has_attachments: Mapped[bool] = mapped_column(Boolean)
    snippet: Mapped[str | None] = mapped_column(Text)
    message_id: Mapped[str | None] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    references_root: Mapped[str | None] = mapped_column(Text)
    thread_key: Mapped[str | None] = mapped_column(Text)
    """The conversation's key (``mailroom.mail.index.threads``): Gmail's thread, else the key of
    the message it answers when the index holds it with the same subject, else the Message-ID."""
    list_id: Mapped[str | None] = mapped_column(Text)
    gm_msgid: Mapped[int | None] = mapped_column(BigInteger)
    gm_thrid: Mapped[int | None] = mapped_column(BigInteger)
    labels: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    """X-GM-LABELS (``\\Inbox``, ``\\Sent``, the user's labels); empty off Gmail."""


class MailViewInterest(Base):
    """A folder one of the user's browsers is viewing, until ``expires_at``: the worker watches
    it through IDLE while the row lasts (``mailroom.worker.idle``). The API writes the rows and
    announces each on ``INTEREST_CHANNEL``; the worker deletes the expired ones."""

    __tablename__ = "mail_view_interest"

    user_id: Mapped[int] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    folder: Mapped[str] = mapped_column(Text, primary_key=True)
    """As the server names it, like ``MailIndexFolder.path``."""
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
