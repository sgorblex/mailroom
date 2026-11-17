"""Account ids are numbers: the app's rows naming an account by its slug take the id mailroom-core's
``core_0004`` gave it (``core.former_slugs``), in one upgrade.

A slug core does not know (an account removed before) is given a new id, recorded in
``core.former_slugs`` like the others. The downgrade gives each id back the slug core's downgrade
gives it (``_slugs_of``, as there).

Revision ID: 202611171200
Revises: 202611161200
Create Date: 2026-11-17 12:00:00

"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

revision: str = "202611171200"
down_revision: str | Sequence[str] | None = "202611161200"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLUG: Final = re.compile(r"[a-z0-9][a-z0-9-]*")
_NUMBERED: Final = re.compile(r"account-[0-9]+")
_LOCK: Final = re.compile(r"mailbox:([0-9]+):(.+)")
_LIVE_JOBS: Final = "status in ('todo', 'doing')"

# Each (user, account) an app row names, as text: the column cast, or a JSON field.
_STORED: Final = (
    "select user_id, account_id::text from app.account_filter_settings",
    "select user_id, account_id::text from app.rule_set",
    "select user_id, account_id::text from app.audit_record",
    (
        "select user_id, detail->>'destination_account' from app.audit_record "
        "where detail ? 'destination_account'"
    ),
    (
        "select user_id, detail->>'source_account' from app.audit_record "
        "where detail ? 'source_account'"
    ),
    (
        "select s.user_id, r.result->>'account' from app.filter_run r "
        "join app.rule_set s on s.id = r.rule_set_id where r.result ? 'account'"
    ),
)


def upgrade() -> None:
    connection = op.get_bind()
    current, former = _core_accounts(connection)
    known: dict[tuple[int, str], int] = {}
    for core_user, slugs in former.items():
        if not core_user.isdecimal():
            continue
        for account_id, slug in slugs.items():
            known[int(core_user), slug] = account_id
    # Accounts added since core_0004, should this run again after a downgrade of the app's alone.
    for core_user, accounts in current.items():
        if not core_user.isdecimal():
            continue
        back = _slugs_of(accounts, former.get(core_user, {}), ())
        for account_id, slug in back.items():
            known.setdefault((int(core_user), slug), account_id)

    for user_id, slug in _stored(connection):
        if (user_id, slug) not in known:
            account_id = connection.execute(
                sa.text("select nextval('core.account_ids')")
            ).scalar_one()
            connection.execute(
                sa.text("insert into core.former_slugs values (:user_id, :account_id, :slug)"),
                {"user_id": str(user_id), "account_id": account_id, "slug": slug},
            )
            known[user_id, slug] = account_id
    _rewrite(connection, {key: str(account_id) for key, account_id in known.items()}, "bigint")


def downgrade() -> None:
    connection = op.get_bind()
    current, former = _core_accounts(connection)
    stored: dict[int, list[int]] = {
        int(core_user): [] for core_user in (*current, *former) if core_user.isdecimal()
    }
    for user_id, account_id in _stored(connection):
        if account_id.isdecimal():
            stored.setdefault(user_id, []).append(int(account_id))
    names: dict[tuple[int, str], str] = {}
    for user_id, ids in stored.items():
        core_user = str(user_id)
        slugs = _slugs_of(current.get(core_user, []), former.get(core_user, {}), ids)
        for account_id, slug in slugs.items():
            names[user_id, str(account_id)] = slug
    _rewrite(connection, names, "text")


def _core_accounts(
    connection: sa.Connection,
) -> tuple[dict[str, list[tuple[int, object]]], dict[str, dict[int, str]]]:
    """Each core user's current accounts as (id, name), and the former slugs by id."""
    current: dict[str, list[tuple[int, object]]] = {}
    newest = connection.execute(
        sa.text(
            "select distinct on (user_id) user_id, doc from core.settings "
            "order by user_id, revision desc"
        )
    )
    for user_id, doc in newest:
        accounts = doc.get("account") if isinstance(doc, dict) else None
        current[user_id] = [
            (account_id, account.get("name"))
            for account in (accounts if isinstance(accounts, list) else [])
            if isinstance(account, dict) and type(account_id := account.get("id")) is int
        ]
    former: dict[str, dict[int, str]] = {}
    for user_id, account_id, slug in connection.execute(
        sa.text("select user_id, account_id, slug from core.former_slugs")
    ):
        former.setdefault(user_id, {})[account_id] = slug
    return current, former


def _stored(connection: sa.Connection) -> list[tuple[int, str]]:
    """Every (user, account) the app's rows name, as text, the live jobs' locks included."""
    found: dict[tuple[int, str], None] = {}
    for query in _STORED:
        for user_id, account in connection.execute(sa.text(query)):
            if account is not None:
                found[user_id, account] = None
    for (lock,) in connection.execute(
        sa.text(f"select lock from procrastinate.procrastinate_jobs where {_LIVE_JOBS}")
    ):
        match = None if lock is None else _LOCK.fullmatch(lock)
        if match is not None:
            found[int(match[1]), match[2]] = None
    return list(found)


def _rewrite(connection: sa.Connection, accounts: dict[tuple[int, str], str], kind: str) -> None:
    """Each account column, JSON field, draft send key and live lock through ``accounts``
    (user, old text → new text), each column then of type ``kind``. One statement a column, its
    keys dropped meanwhile: an id may spell another account's slug."""
    op.execute(
        "create temporary table account_map (user_id bigint, old text, new text) on commit drop"
    )
    if accounts:
        connection.execute(
            sa.text("insert into account_map values (:user_id, :old, :new)"),
            [
                {"user_id": user_id, "old": old, "new": new}
                for (user_id, old), new in accounts.items()
            ],
        )
    json = "to_jsonb(m.new::bigint)" if kind == "bigint" else "to_jsonb(m.new)"

    op.drop_constraint(
        "pk_account_filter_settings", "account_filter_settings", type_="primary", schema="app"
    )
    for table in ("account_filter_settings", "rule_set", "audit_record"):
        op.alter_column(
            table, "account_id", type_=sa.Text(), postgresql_using="account_id::text", schema="app"
        )
        op.execute(
            f"update app.{table} t set account_id = m.new from account_map m "
            "where m.user_id = t.user_id and m.old = t.account_id"
        )
        op.alter_column(
            table,
            "account_id",
            type_=sa.BigInteger() if kind == "bigint" else sa.Text(),
            postgresql_using=f"account_id::{kind}",
            schema="app",
        )
    op.create_primary_key(
        "pk_account_filter_settings",
        "account_filter_settings",
        ["user_id", "account_id"],
        schema="app",
    )
    for field in ("destination_account", "source_account"):
        op.execute(
            f"update app.audit_record t set detail = jsonb_set(t.detail, '{{{field}}}', {json}) "
            f"from account_map m where m.user_id = t.user_id and m.old = t.detail->>'{field}'"
        )
    op.execute(
        f"update app.filter_run r set result = jsonb_set(r.result, '{{account}}', {json}) "
        "from app.rule_set s, account_map m where s.id = r.rule_set_id "
        "and m.user_id = s.user_id and m.old = r.result->>'account'"
    )
    # A draft's send key, ``<account>/<compose_id>`` (``mail.service.compose._send_key``), so that
    # a send claimed before still stops a second delivery.
    op.execute(
        "update core.sends s set key = m.new || substr(s.key, length(m.old) + 1) "
        "from account_map m where s.user_id = m.user_id::text and s.key like m.old || '/%'"
    )
    op.execute(
        "update procrastinate.procrastinate_jobs j "
        "set lock = 'mailbox:' || m.user_id || ':' || m.new "
        f"from account_map m where j.{_LIVE_JOBS} "
        "and j.lock = 'mailbox:' || m.user_id || ':' || m.old"
    )


def _slugs_of(
    current: Sequence[tuple[int, object]], former: dict[int, str], ids: Iterable[int]
) -> dict[int, str]:
    """mailroom-core's ``core_0004._slugs_of``, as it was written: one user's slugs, an id's
    former slug, else its name in ``current`` when that is a slug no other account has, else
    ``account-<id>`` (with a suffix in the unlikely case that is taken)."""
    slugs = dict(former)
    taken = set(former.values())
    for account_id, name in current:
        if account_id in slugs or not isinstance(name, str):
            continue
        if _SLUG.fullmatch(name) and not _NUMBERED.fullmatch(name) and name not in taken:
            slugs[account_id] = name
            taken.add(name)
    for account_id in [*(i for i, _ in current), *ids]:
        if account_id in slugs:
            continue
        slug = f"account-{account_id}"
        suffix = 1
        while slug in taken:
            suffix += 1
            slug = f"account-{account_id}-{suffix}"
        slugs[account_id] = slug
        taken.add(slug)
    return slugs
