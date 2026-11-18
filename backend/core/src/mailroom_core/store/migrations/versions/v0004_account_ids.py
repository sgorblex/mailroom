"""Number the accounts: each slug id becomes an id from the sequence `account_ids`, in every
revision of the settings, the sealed secrets and the send log.

An account without a name is named by its slug, unless another account of that revision already
has that name (compared trimmed and case-insensitively, as the host refuses a taken name): that
one stays unnamed. `former_slugs` keeps each slug, so that the
secrets sealed to it still open (`seal_secret`'s `former_slug`) and a host's own migration can map
its rows. Ids are handed out per user, in user order: the current revision's accounts first, then
any slug only an older revision, a secret or a send still names.

The downgrade gives each id back a slug: its former slug, else its name when that is a free slug,
else `account-<id>`. A secret sealed to an id, not a slug, does not open after it.

Revision ID: core_0004
Revises: core_0003
"""

import re
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

revision: str = "core_0004"
down_revision: str | None = "core_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SLUG: Final = re.compile(r"[a-z0-9][a-z0-9-]*")
_NUMBERED: Final = re.compile(r"account-[0-9]+")
_MAX_NAME: Final = 64

_settings = sa.table(
    "settings",
    sa.column("user_id", sa.Text),
    sa.column("revision", sa.Integer),
    sa.column("doc", JSONB),
    schema="core",
)


def upgrade() -> None:
    connection = op.get_bind()
    op.execute("create sequence core.account_ids as bigint")
    op.create_table(
        "former_slugs",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "account_id", name="pk_former_slugs"),
        schema="core",
    )
    op.create_index(
        "uq_former_slugs_user_id_slug",
        "former_slugs",
        ["user_id", "slug"],
        unique=True,
        schema="core",
    )

    ids: dict[str, dict[str, int]] = {}
    for user_id, slug in _stored_slugs(connection):
        slugs = ids.setdefault(user_id, {})
        if slug not in slugs:
            next_id = connection.execute(sa.text("select nextval('core.account_ids')"))
            slugs[slug] = next_id.scalar_one()
    rows = [
        {"user_id": user_id, "account_id": account_id, "slug": slug}
        for user_id, slugs in ids.items()
        for slug, account_id in slugs.items()
    ]
    if rows:
        connection.execute(
            sa.text(
                "insert into core.former_slugs (user_id, account_id, slug) "
                "values (:user_id, :account_id, :slug)"
            ),
            rows,
        )

    def numbered(user_id: str, accounts: list[object]) -> list[object]:
        taken = {
            _name_key(name)
            for a in accounts
            if isinstance(a, dict) and isinstance(name := a.get("name"), str)
        }
        changed: list[object] = []
        for account in accounts:
            if not isinstance(account, dict) or not isinstance(slug := account.get("id"), str):
                changed.append(account)
                continue
            renumbered = {**account, "id": ids[user_id][slug]}
            name = slug[:_MAX_NAME]
            if account.get("name") is None and _name_key(name) not in taken:
                renumbered["name"] = name
                taken.add(_name_key(name))
            changed.append(renumbered)
        return changed

    _rewrite_docs(connection, numbered)

    # Mapped in one statement each, the keys dropped meanwhile: an id may spell another's slug.
    op.drop_constraint("pk_account_secrets", "account_secrets", type_="primary", schema="core")
    op.execute(
        "update core.account_secrets s set account_id = f.account_id::text "
        "from core.former_slugs f where f.user_id = s.user_id and f.slug = s.account_id"
    )
    op.alter_column(
        "account_secrets",
        "account_id",
        type_=sa.BigInteger(),
        postgresql_using="account_id::bigint",
        schema="core",
    )
    op.create_primary_key(
        "pk_account_secrets",
        "account_secrets",
        ["user_id", "account_id", "purpose"],
        schema="core",
    )
    op.execute(
        "update core.sends s set account = f.account_id::text "
        "from core.former_slugs f where f.user_id = s.user_id and f.slug = s.account"
    )
    op.alter_column(
        "sends", "account", type_=sa.BigInteger(), postgresql_using="account::bigint", schema="core"
    )


def downgrade() -> None:
    connection = op.get_bind()
    slugs = _slugs_back(connection)
    op.execute(
        "create temporary table account_slugs (user_id text, account_id bigint, slug text) "
        "on commit drop"
    )
    rows = [
        {"user_id": user_id, "account_id": account_id, "slug": slug}
        for user_id, of_user in slugs.items()
        for account_id, slug in of_user.items()
    ]
    if rows:
        connection.execute(
            sa.text("insert into account_slugs values (:user_id, :account_id, :slug)"), rows
        )

    def slugged(user_id: str, accounts: list[object]) -> list[object]:
        return [
            {**a, "id": slugs[user_id][account_id]}
            if isinstance(a, dict) and type(account_id := a.get("id")) is int
            else a
            for a in accounts
        ]

    _rewrite_docs(connection, slugged)

    op.drop_constraint("pk_account_secrets", "account_secrets", type_="primary", schema="core")
    op.alter_column(
        "account_secrets",
        "account_id",
        type_=sa.Text(),
        postgresql_using="account_id::text",
        schema="core",
    )
    op.execute(
        "update core.account_secrets s set account_id = m.slug from account_slugs m "
        "where m.user_id = s.user_id and m.account_id::text = s.account_id"
    )
    op.create_primary_key(
        "pk_account_secrets",
        "account_secrets",
        ["user_id", "account_id", "purpose"],
        schema="core",
    )
    op.alter_column(
        "sends", "account", type_=sa.Text(), postgresql_using="account::text", schema="core"
    )
    op.execute(
        "update core.sends s set account = m.slug from account_slugs m "
        "where m.user_id = s.user_id and m.account_id::text = s.account"
    )
    op.drop_index("uq_former_slugs_user_id_slug", "former_slugs", schema="core")
    op.drop_table("former_slugs", schema="core")
    op.execute("drop sequence core.account_ids")


def _accounts(doc: object) -> list[dict[str, object]]:
    accounts = doc.get("account") if isinstance(doc, dict) else None
    return [a for a in accounts if isinstance(a, dict)] if isinstance(accounts, list) else []


def _documents(connection: sa.Connection) -> Iterable[sa.Row[tuple[str, int, object]]]:
    """Every revision, each user's newest first."""
    return connection.execute(
        sa.select(_settings.c.user_id, _settings.c.revision, _settings.c.doc).order_by(
            _settings.c.user_id, _settings.c.revision.desc()
        )
    )


def _stored_slugs(connection: sa.Connection) -> list[tuple[str, str]]:
    """Every (user, slug) stored, in the order ids are handed out."""
    found: list[tuple[str, str]] = [
        (row.user_id, slug)
        for row in _documents(connection)
        for account in _accounts(row.doc)
        if isinstance(slug := account.get("id"), str)
    ]
    for query in (
        "select user_id, account_id from core.account_secrets order by user_id, account_id",
        "select user_id, account from core.sends where account is not null order by user_id, id",
    ):
        found += [(row[0], row[1]) for row in connection.execute(sa.text(query))]
    return sorted(found, key=lambda pair: pair[0])


def _name_key(name: str) -> str:
    """What two account names are compared by, as the host compares them."""
    return name.strip().casefold()


def _rewrite_docs(
    connection: sa.Connection, change: Callable[[str, list[object]], list[object]]
) -> None:
    """Every revision with its accounts passed through `change`."""
    for row in list(_documents(connection)):
        doc = row.doc
        if not isinstance(doc, dict) or not isinstance(doc.get("account"), list):
            continue
        accounts = change(row.user_id, doc["account"])
        connection.execute(
            sa.update(_settings)
            .where(_settings.c.user_id == row.user_id, _settings.c.revision == row.revision)
            .values(doc={**doc, "account": accounts})
        )


def _slugs_back(connection: sa.Connection) -> dict[str, dict[int, str]]:
    """Each user's slug for every id stored: as the app's own downgrade gives them."""
    former: dict[str, dict[int, str]] = {}
    for user_id, account_id, slug in connection.execute(
        sa.text("select user_id, account_id, slug from core.former_slugs")
    ):
        former.setdefault(user_id, {})[account_id] = slug
    current: dict[str, list[tuple[int, object]]] = {}
    stored: dict[str, list[int]] = {}
    for row in _documents(connection):
        accounts = [
            (account_id, a.get("name"))
            for a in _accounts(row.doc)
            if type(account_id := a.get("id")) is int
        ]
        current.setdefault(row.user_id, accounts)
        stored.setdefault(row.user_id, []).extend(account_id for account_id, _ in accounts)
    for table, column in (("account_secrets", "account_id"), ("sends", "account")):
        for user_id, account_id in connection.execute(
            sa.text(f"select user_id, {column} from core.{table} where {column} is not null")
        ):
            stored.setdefault(user_id, []).append(account_id)
    return {
        user_id: _slugs_of(current.get(user_id, []), former.get(user_id, {}), ids)
        for user_id, ids in stored.items()
    }


def _slugs_of(
    current: Sequence[tuple[int, object]], former: dict[int, str], ids: Iterable[int]
) -> dict[int, str]:
    """One user's slugs: an id's former slug, else its name in `current` when that is a slug no
    other account has, else `account-<id>` (with a suffix in the unlikely case that is taken).
    The app's downgrade (202611171200) repeats this."""
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
