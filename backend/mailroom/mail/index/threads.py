"""The conversation a message of the mail index belongs to (``docs/requirements/mail-client.md``,
*Conversations*): its row's ``thread_key``, set as the index stores it.

On Gmail the key is the thread Gmail gives it (``X-GM-THRID``). Elsewhere a message joins the
conversation of the message it answers only when the user's index already holds that message in
the same account and both have the same subject once ``normalized_subject``: its References root,
else its first In-Reply-To id, the first of them that does; else its key is its own Message-ID.
A References header is the sender's to write, so a stranger naming a conversation's Message-ID
lands in it only by also copying its subject. Since a folder is indexed newest first, a reply is
often stored before what it answers: once that message is, ``rejoin`` brings its replies, and the
rows keyed under them, into its conversation.

Rows are mail-derived and untrusted: nothing here logs them.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from sqlalchemy import and_, or_, select, update

from mailroom.models import MailIndexFolder, MailIndexMessage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from mailroom_core.mail import IndexedMessage

_PREFIXES: Final = re.compile(
    r"^(?:\s*(?:re|fwd?|fw|aw|wg|sv|vs|tr|rif|res|enc|antw|r|i)\s*(?:\[\d+\]|\(\d+\))?\s*:)+",
    re.IGNORECASE,
)
"""Reply and forward prefixes, repeated (``Re: Fwd:``), numbered (``Re[2]:``), in the languages
mail clients write them (``AW``, ``WG``, ``SV``, ``R``, ``I``...)."""
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


def normalized_subject(subject: str) -> str:
    """``subject`` without its reply and forward prefixes, its runs of whitespace as one space,
    trimmed and case-folded: what a reply's subject shares with the message it answers."""
    return " ".join(_PREFIXES.sub("", subject).split()).casefold()


def _parents(message: IndexedMessage) -> list[str]:
    own = message.message_id
    found = [message.references_root, message.in_reply_to]
    return list(dict.fromkeys(p for p in found if p is not None and p != own))


def thread_keys(
    db: Session, user_id: int, account_id: int, messages: Sequence[IndexedMessage]
) -> dict[int, str | None]:
    """Each of ``messages`` (of one folder of ``account_id``) by UID with its conversation key:
    Gmail's thread; else the key of the first of its References root and In-Reply-To id that
    names a message the account's index holds, or one of ``messages`` older than it, with the
    same normalized subject; else its own Message-ID (None without one)."""
    parents = {p for m in messages if m.gmail_thread_id is None for p in _parents(m)}
    known: dict[str, list[tuple[str, str | None]]] = {}
    if parents:
        message, folder = MailIndexMessage, MailIndexFolder
        rows = db.execute(
            select(message.message_id, message.subject, message.thread_key)
            .join(folder, and_(folder.id == message.folder_id, folder.user_id == message.user_id))
            .where(
                message.user_id == user_id,
                folder.account_id == account_id,
                message.message_id.in_(sorted(parents)),
            )
        )
        for message_id, subject, key in rows:
            if message_id is not None:
                known.setdefault(message_id, []).append((normalized_subject(subject), key))
    keys: dict[int, str | None] = {}
    # Oldest first: what a message of the batch answers is keyed before it.
    for each in sorted(messages, key=_arrival):
        key = _key(each, known)
        keys[each.uid] = key
        if each.message_id is not None:
            known.setdefault(each.message_id, []).append((normalized_subject(each.subject), key))
    return keys


def _arrival(message: IndexedMessage) -> datetime:
    return message.received or message.date or _EPOCH


def _key(message: IndexedMessage, known: dict[str, list[tuple[str, str | None]]]) -> str | None:
    if message.gmail_thread_id is not None:
        return f"gmail:{message.gmail_thread_id}"
    subject = normalized_subject(message.subject)
    for parent in _parents(message):
        for theirs, key in known.get(parent, ()):
            if theirs == subject:
                return key or parent
    return message.message_id


def rejoin(
    db: Session,
    user_id: int,
    account_id: int,
    keyed: Sequence[tuple[IndexedMessage, str | None]],
) -> None:
    """Brings into the conversation of each message just stored (with its key) the rows of the
    account stored before it that answer it (their References root or In-Reply-To id is its
    Message-ID) with the same normalized subject, and every row keyed under theirs."""
    heads = {
        m.message_id: (normalized_subject(m.subject), key)
        for m, key in keyed
        if m.message_id is not None and m.gmail_thread_id is None and key is not None
    }
    if not heads:
        return
    message, folder = MailIndexMessage, MailIndexFolder
    folders = select(folder.id).where(folder.user_id == user_id, folder.account_id == account_id)
    rows = db.execute(
        select(
            message.subject, message.thread_key, message.references_root, message.in_reply_to
        ).where(
            message.user_id == user_id,
            message.folder_id.in_(folders),
            message.gm_thrid.is_(None),
            or_(
                message.references_root.in_(sorted(heads)),
                message.in_reply_to.in_(sorted(heads)),
            ),
        )
    )
    moved: dict[str, str] = {}
    for subject, key, root, replied in rows:
        for parent in (root, replied):
            head = None if parent is None else heads.get(parent)
            if head is None or head[0] != normalized_subject(subject):
                continue
            if key is not None and key != head[1]:
                moved.setdefault(key, head[1])
            break
    for old, new in moved.items():
        db.execute(
            update(message)
            .where(
                message.user_id == user_id,
                message.folder_id.in_(folders),
                message.gm_thrid.is_(None),
                message.thread_key == old,
            )
            .values(thread_key=new)
        )
