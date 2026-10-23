"""Run the worker: `python -m mailroom.worker`. It never migrates; it waits for the API to.

One worker runs at a time (it holds a database lock while it runs): a second one exits. At
start, jobs a stopped worker left running are marked failed. On SIGTERM or SIGINT procrastinate
gives the jobs in progress `SHUTDOWN_SECONDS` to finish, then aborts them.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Final

from mailroom.database import get_sync_database_url
from mailroom.database_connectivity import wait_for_sync_database
from mailroom.db_migrations import wait_for_head
from mailroom.observability.logging_config import configure_logging
from mailroom.worker.app import app, database_connector
from mailroom.worker.startup import fail_interrupted_jobs, worker_lock

# Not __name__: run with -m, that is "__main__", outside the "mailroom" loggers.
logger = logging.getLogger("mailroom.worker")

SHUTDOWN_SECONDS: Final = 60.0
"""How long a stopping worker waits for its jobs (the Quadlet unit's StopTimeout is longer)."""


async def _settle() -> None:
    async with app.open_async():
        await fail_interrupted_jobs()


def main() -> None:
    configure_logging()
    database_url = get_sync_database_url()
    engine = wait_for_sync_database(database_url)
    try:
        wait_for_head(engine)
    finally:
        engine.dispose()
    lock = worker_lock(database_url)
    if lock is None:
        logger.error("Another worker is running on this database; exiting.")
        sys.exit(1)
    with lock, app.replace_connector(database_connector(database_url)):
        asyncio.run(_settle())
        logger.info("Worker starting.")
        app.run_worker(shutdown_graceful_timeout=SHUTDOWN_SECONDS)


if __name__ == "__main__":
    main()
