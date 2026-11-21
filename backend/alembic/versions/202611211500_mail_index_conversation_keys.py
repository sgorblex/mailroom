"""A conversation's key on the mail index (``app.mail_index_message.thread_key``) joins a
message to the one it answers only when the index holds that one in the same account with the
same subject (``mailroom.mail.index.threads``): the rows are found by Message-ID, References root
and In-Reply-To, and the keys stored before are set again by that rule, here by the References
root or In-Reply-To id alone (a sync sets them again as it stores each message).

Revision ID: 202611211500
Revises: 202611211400
Create Date: 2026-11-21 15:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611211500"
down_revision: str | Sequence[str] | None = "202611211400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("message_id", "references_root", "in_reply_to")

# `threads.normalized_subject` in SQL: prefixes, whitespace, case.
_SUBJECT = (
    "lower(btrim(regexp_replace(regexp_replace({0}.subject, "
    r"'^(\s*(re|fwd?|fw|aw|wg|sv|vs|tr|rif|res|enc|antw|r|i)\s*(\[[0-9]+\]|\([0-9]+\))?\s*:)+', "
    r"'', 'i'), '\s+', ' ', 'g')))"
)

_PARENT = """
EXISTS (
    SELECT 1
    FROM app.mail_index_message parent
    JOIN app.mail_index_folder parent_folder ON parent_folder.id = parent.folder_id
    WHERE parent.user_id = message.user_id
      AND parent_folder.account_id = folder.account_id
      AND parent.message_id = message.{column}
      AND {parent_subject} = {own_subject}
)
"""


def upgrade() -> None:
    for column in _COLUMNS:
        op.create_index(
            f"ix_mail_index_message_{column}",
            "mail_index_message",
            ["user_id", column],
            schema="app",
        )
    parent = {
        column: _PARENT.format(
            column=column,
            parent_subject=_SUBJECT.format("parent"),
            own_subject=_SUBJECT.format("message"),
        )
        for column in ("references_root", "in_reply_to")
    }
    op.execute(
        f"""
        UPDATE app.mail_index_message AS message
        SET thread_key = CASE
            WHEN message.references_root IS NOT NULL
                AND message.references_root IS DISTINCT FROM message.message_id
                AND {parent["references_root"]}
                THEN message.references_root
            WHEN message.in_reply_to IS NOT NULL
                AND message.in_reply_to IS DISTINCT FROM message.message_id
                AND {parent["in_reply_to"]}
                THEN message.in_reply_to
            ELSE message.message_id
        END
        FROM app.mail_index_folder AS folder
        WHERE folder.id = message.folder_id AND message.gm_thrid IS NULL
        """
    )


def downgrade() -> None:
    # The keys stay as set: the rule before this one read them the same way.
    for column in _COLUMNS:
        op.drop_index(f"ix_mail_index_message_{column}", "mail_index_message", schema="app")
