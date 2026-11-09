"""Run the worker: `python -m mailroom.worker`. It never migrates; it waits for the API to.

Only filters need it: without them in ``MAILROOM_FEATURES`` it logs why and exits 0 before it
touches the database, and an unknown feature name makes it exit 1.

One worker runs at a time (it holds a database lock while it runs): a second one exits. At
start, jobs a stopped worker left running are marked failed, and their runs closed as
interrupted. Beside procrastinate's job loop, a thread runs the IDLE supervisor
(``mailroom.worker.idle``). On SIGTERM or SIGINT procrastinate gives the jobs in progress
`SHUTDOWN_SECONDS` to finish, then aborts them; the watches are then stopped too.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Final

from mailroom.config.features import FEATURES_ENV, features_from_env
from mailroom.database import get_sync_database_url, load_local_environment
from mailroom.database_connectivity import wait_for_sync_database
from mailroom.db_migrations import wait_for_head
from mailroom.observability.logging_config import configure_logging
from mailroom.worker.app import app, database_connector
from mailroom.worker.context import worker_context
from mailroom.worker.idle import IdleSupervisor
from mailroom.worker.runs import close_interrupted_runs
from mailroom.worker.startup import fail_interrupted_jobs, worker_lock

# Not __name__: run with -m, that is "__main__", outside the "mailroom" loggers.
logger = logging.getLogger("mailroom.worker")

SHUTDOWN_SECONDS: Final = 60.0
"""How long a stopping worker waits for its jobs (the Quadlet unit's StopTimeout is longer)."""
CONCURRENCY: Final = 4
"""Jobs run at once (each on a thread: the tasks are synchronous); at most one per mailbox."""
POLL_SECONDS: Final = 1.0
"""How often the worker looks for jobs besides being notified of new ones: a job held back by its
mailbox's lock waits at most this long once the lock frees (procrastinate's default is 5 s)."""


async def _settle() -> None:
    async with app.open_async():
        await fail_interrupted_jobs()


def main() -> None:
    configure_logging()
    load_local_environment()
    try:
        features = features_from_env()
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    logger.info("%s: %s", FEATURES_ENV, features.description)
    if not features.filters:
        logger.info("No enabled feature needs the worker (filters is off); exiting.")
        return
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
        context = worker_context()
        with context.sessions() as db:
            close_interrupted_runs(db)
        logger.info("Worker starting.")
        stop = threading.Event()
        supervisor = IdleSupervisor(context.sessions, context.mailboxes)
        watching = threading.Thread(target=supervisor.run, args=(stop,), name="idle-supervisor")
        watching.start()
        try:
            app.run_worker(
                concurrency=CONCURRENCY,
                fetch_job_polling_interval=POLL_SECONDS,
                shutdown_graceful_timeout=SHUTDOWN_SECONDS,
            )
        finally:
            stop.set()
            watching.join()
    logger.info("Worker stopped.")


if __name__ == "__main__":
    main()
