"""The Postgres database and its migrations, one Alembic history per package and schema.

`src/store/db.ts` migrated its SQLite file on open with a list of SQL scripts; here each package
keeps an Alembic history in its own schema, with the version table in that schema too, so
mailroom-core's, mailroom-mcp's and a host app's histories share a database without seeing each
other.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Final

from alembic import command, context
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine, MetaData

CORE_MIGRATIONS: Final = Path(__file__).parent / "migrations"
"""Schema `core`'s Alembic history."""


def database_engine(url: str) -> Engine:
    """An engine on `url`, a `postgresql://` URL being given the psycopg (3) driver."""
    if url.startswith("postgresql://"):
        url = f"postgresql+psycopg://{url.removeprefix('postgresql://')}"
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


def migrate(engine: Engine) -> None:
    """Bring schema `core` up to date."""
    upgrade(engine, CORE_MIGRATIONS)


def upgrade(engine: Engine, migrations: Path, revision: str = "head") -> None:
    """Run the Alembic history in `migrations` up to `revision`, in one transaction."""
    with engine.begin() as connection:
        command.upgrade(_config(migrations, connection), revision)


def downgrade(engine: Engine, migrations: Path, revision: str = "base") -> None:
    """Run the Alembic history in `migrations` down to `revision`, in one transaction."""
    with engine.begin() as connection:
        command.downgrade(_config(migrations, connection), revision)


def run_migrations(metadata: MetaData) -> None:
    """The body of each history's `env.py`: online only, on the connection `upgrade` passes.

    Only the schema of `metadata` is compared or touched; the version table lives in it, so the
    schema is created first. A transaction-scoped advisory lock on the schema's name makes
    processes migrating at once take turns.
    """
    schema = metadata.schema
    if schema is None:
        raise ValueError("a migrated MetaData needs a schema")
    connection: Connection = context.config.attributes["connection"]
    connection.execute(text("select pg_advisory_xact_lock(hashtext(:schema))"), {"schema": schema})
    connection.execute(text(f'create schema if not exists "{schema}"'))

    def include_name(name: str | None, type_: str, _parent: object) -> bool:
        return type_ != "schema" or name == schema

    context.configure(
        connection=connection,
        target_metadata=metadata,
        version_table_schema=schema,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def _config(migrations: Path, connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(migrations))
    config.attributes["connection"] = connection
    return config
