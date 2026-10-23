"""The procrastinate app every task registers on, and its connector to the app's database.

The module-level `app` carries the tasks but no database: whoever runs or defers jobs swaps the
real connector in with `app.replace_connector(database_connector(url))` (`__main__` does for the
worker), and tests swap in `procrastinate.testing.InMemoryConnector`. procrastinate's tables live
in schema `procrastinate`, created by Mailroom's Alembic history, never by procrastinate's CLI.
"""

from __future__ import annotations

from typing import Any, NoReturn

import procrastinate

from mailroom.database import libpq_database_url

PROCRASTINATE_SCHEMA = "procrastinate"


def _unconfigured(**_pool_arguments: Any) -> NoReturn:  # noqa: ANN401
    message = "the worker app has no database: use app.replace_connector(database_connector(url))"
    raise RuntimeError(message)


def database_connector(database_url: str) -> procrastinate.PsycopgConnector:
    """A connector to `database_url` whose search path is procrastinate's schema: its SQL names
    its tables and functions unqualified."""
    return procrastinate.PsycopgConnector(
        conninfo=libpq_database_url(database_url),
        kwargs={"options": f"-c search_path={PROCRASTINATE_SCHEMA}"},
    )


app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(pool_factory=_unconfigured),
    import_paths=["mailroom.worker.tasks"],
)
