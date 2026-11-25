"""Handled replaces To sort: what each account keeps the mark as (``account_handled``, no row for
the star), rule sets' markers ``handled`` and ``starred`` for ``to_sort`` and ``star``, and the
swipe choice ``handled`` for ``to_sort``. No rule set acts on other mail after it:

- an account that used To sort (a rule set with the ``to_sort`` marker, or with a filter that
  looks at or sets the keyword, runs or could run on it, or its index holds a message with it)
  starts as the keyword it was kept as, every other one as the star; so a ``to_sort`` marker
  becomes ``handled``, on a server without keywords too, where it goes on matching nothing;
- a ``star`` marker becomes ``handled`` only where the star is what Handled is on every account
  it can run on, else ``starred``.

``rule_set.marker`` is renamed ``mark``: a process of the version before, which would take a
marker it does not know for none and run the rule set on every message, then fails instead. The
filters' own To sort criteria and actions keep their meaning, the keyword.

The downgrade gives each ``handled`` back the marker that means the same on every account it can
run on (the star where there is no account: a later one would have kept it as that); one that has
none (another keyword, a label, or accounts that differ) waits for To sort, disabled.

Revision ID: 202611251000
Revises: 202611241400
Create Date: 2026-11-25 10:00:00

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "202611251000"
down_revision: str | Sequence[str] | None = "202611241400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KEYWORD = "keyword:$MailroomSort"
# A filter's To sort criterion or action, anywhere in its spec.
_TO_SORT_NODE = """$.** ? (
    @.type == "is_to_sort" || @.type == "is_not_to_sort"
    || @.type == "mark_to_sort" || @.type == "unmark_to_sort"
    || ((@.type == "has_flag" || @.type == "lacks_flag") && @.args.flag == "TO_SORT")
)"""
# Every account known of each user: mailroom-core's, and those the app's own rows name. An
# account whose Run filters is off counts too: it may be turned on.
_ACCOUNTS = """
    SELECT u.id AS user_id, (account.value ->> 'id')::bigint AS account_id
    FROM app.user u
    JOIN LATERAL (
        SELECT doc FROM core.settings s WHERE s.user_id = u.id::text
        ORDER BY s.revision DESC LIMIT 1
    ) newest ON true
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(newest.doc -> 'account') = 'array'
        THEN newest.doc -> 'account' ELSE '[]'::jsonb END
    ) AS account(value)
    WHERE jsonb_typeof(account.value -> 'id') = 'number'
    UNION SELECT user_id, account_id FROM app.account_filter_settings
    UNION SELECT user_id, account_id FROM app.rule_set WHERE account_id IS NOT NULL
"""


def upgrade() -> None:
    op.create_table(
        "account_handled",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("mark", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app.user.id"],
            name=op.f("fk_account_handled_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "account_id", name=op.f("pk_account_handled")),
        schema="app",
    )
    op.execute(
        f"""
        INSERT INTO app.account_handled (user_id, account_id, mark)
        SELECT user_id, account_id, '{_KEYWORD}' FROM (
            SELECT known.user_id, known.account_id
            FROM ({_ACCOUNTS}) known
            JOIN app.rule_set r ON r.user_id = known.user_id
                AND (r.account_id IS NULL OR r.account_id = known.account_id)
            WHERE r.marker = 'to_sort' OR EXISTS (
                SELECT FROM app.filter spec WHERE spec.rule_set_id = r.id
                    AND jsonb_path_exists(spec.spec, '{_TO_SORT_NODE}')
            )
            UNION
            SELECT f.user_id, f.account_id FROM app.mail_index_folder f
            WHERE EXISTS (
                SELECT FROM app.mail_index_message m
                WHERE m.folder_id = f.id AND m.user_id = f.user_id
                    AND '$mailroomsort' = ANY (
                        string_to_array(lower(array_to_string(m.keywords, ' ')), ' ')
                    )
            )
        ) used
        """
    )
    op.drop_constraint(op.f("ck_rule_set_marker"), "rule_set", schema="app", type_="check")
    op.alter_column("rule_set", "marker", new_column_name="mark", schema="app")
    op.execute(
        """
        UPDATE app.rule_set r SET mark = CASE
            WHEN r.mark = 'to_sort' THEN 'handled'
            WHEN EXISTS (
                SELECT FROM app.account_handled h WHERE h.user_id = r.user_id
                    AND (r.account_id IS NULL OR r.account_id = h.account_id)
            ) THEN 'starred'
            ELSE 'handled'
        END
        WHERE r.mark <> 'none'
        """
    )
    op.create_check_constraint(
        op.f("ck_rule_set_mark"),
        "rule_set",
        "mark IN ('none', 'starred', 'handled')",
        schema="app",
    )
    _rename_swipes("to_sort", "handled")


def downgrade() -> None:
    _rename_swipes("handled", "to_sort")
    op.drop_constraint(op.f("ck_rule_set_mark"), "rule_set", schema="app", type_="check")
    op.alter_column("rule_set", "mark", new_column_name="marker", schema="app")
    op.execute(
        f"""
        WITH kept AS (
            SELECT r.id,
                bool_and(h.mark IS NULL) AS star,  -- no account: no mark
                bool_and(h.mark IS NOT NULL AND lower(h.mark) = lower('{_KEYWORD}')) AS to_sort
            FROM app.rule_set r
            LEFT JOIN ({_ACCOUNTS}) known ON known.user_id = r.user_id
                AND (r.account_id IS NULL OR r.account_id = known.account_id)
            LEFT JOIN app.account_handled h
                ON h.user_id = known.user_id AND h.account_id = known.account_id
            WHERE r.marker = 'handled'
            GROUP BY r.id
        )
        UPDATE app.rule_set r SET
            marker = CASE WHEN kept.star THEN 'star' ELSE 'to_sort' END,
            enabled = r.enabled AND (kept.star OR kept.to_sort)
        FROM kept WHERE kept.id = r.id
        """
    )
    op.execute(
        "UPDATE app.rule_set SET marker = CASE marker WHEN 'starred' THEN 'star' "
        "ELSE 'to_sort' END WHERE marker IN ('starred', 'handled')"
    )
    op.create_check_constraint(
        op.f("ck_rule_set_marker"),
        "rule_set",
        "marker IN ('none', 'star', 'to_sort')",
        schema="app",
    )
    op.drop_table("account_handled", schema="app")


def _rename_swipes(old: str, new: str) -> None:
    for side in ("swipe_left", "swipe_right"):
        op.execute(
            f"""
            UPDATE app.user SET appearance = jsonb_set(appearance, '{{{side}}}', '"{new}"')
            WHERE appearance ->> '{side}' = '{old}'
            """
        )
