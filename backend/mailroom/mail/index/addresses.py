"""The addresses a recipient field suggests (``docs/requirements/mail-client.md``, *Compose*),
read from the index's rows alone, nothing kept for them: the addresses the user sent to (To and Cc
of their Sent folders' rows, on Gmail of All Mail's rows labelled ``\\Sent``) first, then the
people who wrote to them directly (the From of a row that names one of their identities in To or
Cc). List mail (a List-Id), no-reply addresses and the user's own identities are never
suggested. Each group is ranked by how many rows name the address, then by the latest; an address
in both is the first group's. Every row is mail-derived and untrusted: nothing here logs one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from sqlalchemy import BigInteger, Integer, Text, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

if TYPE_CHECKING:
    from collections.abc import Collection

    from sqlalchemy.orm import Session

MAX_SUGGESTIONS: Final = 10
MAX_NAME: Final = 1024
"""A longer name, which mail can carry, is suggested as none: a draft's address takes no more."""
NO_REPLY: Final = r"(^|[^a-z0-9])(no[-_]?reply|do[-_]?not[-_]?reply)([^a-z0-9]|$)"
"""An address no one reads: ``noreply@``, ``no-reply@``, ``do-not-reply@``, ``@noreply.``..."""
_SENT_LABEL: Final = "\\Sent"


@dataclass(frozen=True, slots=True)
class Suggestion:
    """An address, lowercase, with the latest name it went by (empty when none, or longer than
    ``MAX_NAME``); ``sent``: the user wrote to it."""

    address: str
    name: str
    sent: bool


_PEOPLE: Final = (
    "jsonb_array_elements(coalesce(m.recipients->'to', '[]'::jsonb)"
    " || coalesce(m.recipients->'cc', '[]'::jsonb))"
)

_SUGGEST: Final = text(f"""
WITH sent AS (
    SELECT lower(btrim(p->>'address')) AS address, btrim(coalesce(p->>'name', '')) AS name,
           m.received, 0 AS tier
    FROM app.mail_index_message m CROSS JOIN LATERAL {_PEOPLE} AS p
    WHERE m.user_id = :user_id AND m.folder_id = ANY(:folders)
      AND (m.folder_id = ANY(:sent) OR :sent_label = ANY(m.labels))
),
received AS (
    SELECT lower(btrim(m.sender->0->>'address')) AS address,
           btrim(coalesce(m.sender->0->>'name', '')) AS name, m.received, 1 AS tier
    FROM app.mail_index_message m
    WHERE m.user_id = :user_id AND m.folder_id = ANY(:inbound)
      AND NOT m.folder_id = ANY(:sent) AND NOT :sent_label = ANY(m.labels)
      AND m.list_id IS NULL AND jsonb_array_length(m.sender) = 1
      AND m.sender_text ILIKE :anywhere ESCAPE '\\'
      AND EXISTS (
          SELECT FROM {_PEOPLE} AS p WHERE lower(btrim(p->>'address')) = ANY(:identities)
      )
),
kept AS (
    SELECT * FROM (SELECT * FROM sent UNION ALL SELECT * FROM received) AS entries
    WHERE address LIKE '_%@_%' AND NOT address = ANY(:identities) AND address !~ :no_reply
),
matched AS (
    SELECT DISTINCT address FROM kept
    WHERE address LIKE :prefix ESCAPE '\\' OR lower(name) LIKE :prefix ESCAPE '\\'
       OR lower(name) LIKE :word ESCAPE '\\'
),
tiers AS (
    SELECT DISTINCT ON (address) address, tier, count(*) AS n, max(received) AS last,
           coalesce((array_agg(name ORDER BY received DESC) FILTER (WHERE name <> ''))[1], '')
               AS name
    FROM kept JOIN matched USING (address)
    GROUP BY address, tier
    ORDER BY address, tier
)
SELECT address, CASE WHEN length(name) > :max_name THEN '' ELSE name END, tier FROM tiers
ORDER BY tier, n DESC, last DESC, address
LIMIT :limit
""").bindparams(
    bindparam("folders", type_=ARRAY(BigInteger)),
    bindparam("sent", type_=ARRAY(BigInteger)),
    bindparam("inbound", type_=ARRAY(BigInteger)),
    bindparam("identities", type_=ARRAY(Text)),
    bindparam("limit", type_=Integer),
    bindparam("max_name", type_=Integer),
)


def _like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def suggest(
    db: Session,
    user_id: int,
    prefix: str,
    *,
    folders: Collection[int],
    sent: Collection[int],
    inbound: Collection[int],
    identities: Collection[str],
    limit: int = MAX_SUGGESTIONS,
) -> list[Suggestion]:
    """Up to ``limit`` addresses whose address, display name or a word of it starts with
    ``prefix`` (case-insensitive), among the user's rows of ``folders`` (indexed and ready): the
    recipients of those that are ``sent`` folders' (or carry Gmail's ``\\Sent`` label), then the
    senders of ``inbound``'s that name one of ``identities`` (lowercase) in To or Cc."""
    needle = _like(prefix.strip().lower())
    if not needle or not folders:
        return []
    rows = db.execute(
        _SUGGEST,
        {
            "user_id": user_id,
            "folders": sorted(folders),
            "sent": sorted(sent),
            "inbound": sorted(inbound),
            "identities": sorted(identities),
            "sent_label": _SENT_LABEL,
            "no_reply": NO_REPLY,
            "anywhere": f"%{needle}%",
            "prefix": f"{needle}%",
            "word": f"% {needle}%",
            "limit": min(max(limit, 1), MAX_SUGGESTIONS),
            "max_name": MAX_NAME,
        },
    )
    return [Suggestion(address=address, name=name, sent=tier == 0) for address, name, tier in rows]
