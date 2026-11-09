"""Throwaway Postgres databases for the store tests, on the server TEST_DATABASE_URL names.

mise.toml points it at the dev Postgres (`mise run dev-db`); Woodpecker runs one as a service. The
database it names is only connected to, for creating and dropping others: each test gets a fresh
copy of a template migrated once per session, and every database made here is dropped at the end.
"""

import os
import secrets
from contextlib import contextmanager
from typing import TYPE_CHECKING, ClassVar, Self

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from mailroom_core.store import database_engine

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy import Engine, MetaData

type Migrate = Callable[[Engine], None]
"""Brings a database's schemas up to date, as `mailroom_core.store.migrate` does."""


class ThrowawayDatabases:
    """Creates databases named `mailroom_test_<random>` on one server, and drops them again."""

    __test__: ClassVar = False

    def __init__(self, url: str) -> None:
        self._admin = database_engine(url).execution_options(isolation_level="AUTOCOMMIT")
        self._templates: dict[Migrate, str] = {}
        self._created: list[str] = []

    @classmethod
    def from_env(cls) -> Self | None:
        url = os.environ.get("TEST_DATABASE_URL")
        return None if not url else cls(url)

    def check(self) -> None:
        """Raise unless the server accepts a connection."""
        with self._admin.connect() as connection:
            connection.execute(text("select 1"))

    @contextmanager
    def migrated(self, migrate: Migrate) -> Iterator[Engine]:
        """A fresh database brought up to date by `migrate`, dropped when the block ends."""
        template = self._templates.get(migrate)
        if template is None:
            template = self._create()
            engine = create_engine(
                self._database_url(template), poolclass=NullPool, hide_parameters=True
            )
            try:
                migrate(engine)
            finally:
                engine.dispose()
            self._templates[migrate] = template
        name = self._create(template)
        engine = database_engine(self._database_url(name))
        try:
            yield engine
        finally:
            engine.dispose()
            self._drop(name)

    def close(self) -> None:
        """Drop every database still left, the templates included."""
        for name in reversed([*self._created]):
            self._drop(name)
        self._templates.clear()
        self._admin.dispose()

    def _database_url(self, name: str) -> str:
        return self._admin.url.set(database=name).render_as_string(hide_password=False)

    def _create(self, template: str | None = None) -> str:
        name = f"mailroom_test_{secrets.token_hex(6)}"
        copy = "" if template is None else f' template "{template}"'
        with self._admin.connect() as connection:
            connection.execute(text(f'create database "{name}"{copy}'))
        self._created.append(name)
        return name

    def _drop(self, name: str) -> None:
        with self._admin.connect() as connection:
            connection.execute(text(f'drop database if exists "{name}" with (force)'))
        self._created.remove(name)


def schema_differences(engine: Engine, metadata: MetaData) -> list[object]:
    """What Alembic's autogenerate finds between `metadata` and its schema in the database:
    empty once the migrations create exactly the tables the code declares."""
    schema = metadata.schema

    def include_name(name: str | None, type_: str, _parent: object) -> bool:
        return type_ != "schema" or name == schema

    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "include_name": include_name,
                "version_table_schema": schema,
            },
        )
        return list(compare_metadata(context, metadata))
