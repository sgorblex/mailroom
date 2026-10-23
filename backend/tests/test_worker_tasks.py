from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from procrastinate.testing import InMemoryConnector
from sqlalchemy import text

from mailroom.worker.app import PROCRASTINATE_SCHEMA, app, database_connector
from mailroom.worker.startup import fail_interrupted_jobs, worker_lock
from mailroom.worker.tasks import ping

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


def _clear_queue(engine: Engine) -> None:
    # The jobs are committed: other tests look at the queue.
    with engine.begin() as connection:
        connection.execute(
            text(
                f"TRUNCATE {PROCRASTINATE_SCHEMA}.procrastinate_jobs, "
                f"{PROCRASTINATE_SCHEMA}.procrastinate_periodic_defers CASCADE"
            )
        )


def _job_status(engine: Engine, job_id: int) -> str:
    with engine.connect() as connection:
        return connection.execute(
            text(f"SELECT status FROM {PROCRASTINATE_SCHEMA}.procrastinate_jobs WHERE id = :id"),
            {"id": job_id},
        ).scalar_one()


def test_ping_runs_through_a_worker() -> None:
    connector = InMemoryConnector()
    with app.replace_connector(connector):
        job_id = ping.defer()
        app.run_worker(wait=False, install_signal_handlers=False)
    assert connector.jobs[job_id]["status"] == "succeeded"


def test_ping_runs_against_the_migrated_database(migrated_engine: Engine) -> None:
    async def defer_and_work() -> int:
        async with app.open_async():
            job_id = await ping.defer_async()
            await app.run_worker_async(wait=False, install_signal_handlers=False)
            return job_id

    try:
        with app.replace_connector(database_connector(os.environ["DATABASE_URL"])):
            job_id = asyncio.run(defer_and_work())
        assert _job_status(migrated_engine, job_id) == "succeeded"
    finally:
        _clear_queue(migrated_engine)


def test_a_second_worker_is_refused(migrated_engine: Engine) -> None:
    _ = migrated_engine
    database_url = os.environ["DATABASE_URL"]
    first = worker_lock(database_url)
    assert first is not None
    with first:
        assert worker_lock(database_url) is None
    again = worker_lock(database_url)
    assert again is not None
    again.close()


def test_jobs_a_stopped_worker_left_running_fail(migrated_engine: Engine) -> None:
    async def defer() -> int:
        async with app.open_async():
            return await ping.defer_async()

    async def settle() -> list[int]:
        async with app.open_async():
            return await fail_interrupted_jobs()

    try:
        with app.replace_connector(database_connector(os.environ["DATABASE_URL"])):
            job_id = asyncio.run(defer())
            with migrated_engine.begin() as connection:
                # Its triggers name procrastinate's types unqualified.
                connection.execute(text(f"SET LOCAL search_path TO {PROCRASTINATE_SCHEMA}"))
                connection.execute(
                    text("UPDATE procrastinate_jobs SET status = 'doing' WHERE id = :id"),
                    {"id": job_id},
                )
            assert asyncio.run(settle()) == [job_id]
        assert _job_status(migrated_engine, job_id) == "failed"
    finally:
        _clear_queue(migrated_engine)
