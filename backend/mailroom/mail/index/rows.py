"""What the index's writers share on a folder's message rows: storing them, their flags and
the folder's counts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import BigInteger, any_, bindparam, func, literal, not_, select, update
from sqlalchemy.dialects.postgresql import ARRAY, insert

from mailroom.models import MailIndexMessage

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session

    from mailroom.models import MailIndexFolder

SEEN: Final = "\\Seen"


def upsert_rows(db: Session, values: Sequence[Mapping[str, Any]]) -> None:
    """Store message rows (every column of each), replacing those with the same folder and UID."""
    if not values:
        return
    statement = insert(MailIndexMessage).values(list(values))
    kept = ("folder_id", "uid", "user_id")
    columns = [column.name for column in MailIndexMessage.__table__.columns]
    db.execute(
        statement.on_conflict_do_update(
            index_elements=["folder_id", "uid"],
            set_={name: statement.excluded[name] for name in columns if name not in kept},
        )
    )


def count_rows(db: Session, row: MailIndexFolder) -> None:
    """Set the folder's message and unread counts from its rows."""
    unread = not_(literal(SEEN) == any_(MailIndexMessage.flags))
    total, unseen = db.execute(
        select(func.count(), func.count().filter(unread)).where(
            MailIndexMessage.folder_id == row.id, MailIndexMessage.user_id == row.user_id
        )
    ).one()
    row.message_count, row.unread_count = total, unseen


def flag_kinds(flags: frozenset[str]) -> tuple[list[str], list[str]]:
    """System flags (``\\Seen``...) and keywords, each sorted."""
    return (
        sorted(flag for flag in flags if flag.startswith("\\")),
        sorted(flag for flag in flags if not flag.startswith("\\")),
    )


def apply_flags(
    db: Session,
    user_id: int,
    folder_id: int,
    flags: Mapping[int, frozenset[str]],
    labels: Mapping[int, tuple[str, ...]] | None = None,
) -> int:
    """Store ``flags`` (system flags and keywords together, by UID), and on Gmail ``labels``
    (those of the same UIDs), on the folder's rows that differ; how many changed. UIDs without a
    row are left out."""
    uids = bindparam("uids", sorted(flags), type_=ARRAY(BigInteger))
    held = db.execute(
        select(
            MailIndexMessage.uid,
            MailIndexMessage.flags,
            MailIndexMessage.keywords,
            MailIndexMessage.labels,
        ).where(
            MailIndexMessage.folder_id == folder_id,
            MailIndexMessage.user_id == user_id,
            MailIndexMessage.uid == any_(uids),
        )
    )
    current = {
        uid: (sorted(system), sorted(keywords), list(kept)) for uid, system, keywords, kept in held
    }
    changes: list[dict[str, Any]] = []
    for uid, server in flags.items():
        if uid not in current:
            continue
        system, keywords = flag_kinds(server)
        new_labels = current[uid][2] if labels is None else list(labels.get(uid, ()))
        if current[uid] != (system, keywords, new_labels):
            changes.append(
                {"at": uid, "new_flags": system, "new_keywords": keywords, "new_labels": new_labels}
            )
    if changes:
        statement = (
            update(MailIndexMessage)
            .where(
                MailIndexMessage.folder_id == folder_id,
                MailIndexMessage.user_id == user_id,
                MailIndexMessage.uid == bindparam("at"),
            )
            .values(
                flags=bindparam("new_flags"),
                keywords=bindparam("new_keywords"),
                labels=bindparam("new_labels"),
            )
        )
        db.connection().execute(statement, changes)
    return len(changes)
