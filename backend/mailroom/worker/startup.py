"""What the worker settles before it takes jobs: that it is the only one, and that no job is left
running by a worker that died."""

from __future__ import annotations

import logging
from typing import Final

import psycopg
from procrastinate.jobs import Status

from mailroom.database import libpq_database_url
from mailroom.worker.app import app

logger = logging.getLogger(__name__)

WORKER_LOCK: Final = 0x6D61696C726F6F6D
"""The session advisory lock the running worker holds (the bytes of "mailroom")."""


def worker_lock(database_url: str) -> psycopg.Connection | None:
    """A connection holding the worker lock, or None if another worker holds it. The lock lasts
    as long as the connection: keep it open for the life of the worker."""
    connection = psycopg.connect(libpq_database_url(database_url), autocommit=True)
    held = connection.execute("SELECT pg_try_advisory_lock(%s)", (WORKER_LOCK,)).fetchone()
    if held is not None and held[0]:
        return connection
    connection.close()
    return None


async def fail_interrupted_jobs() -> list[int]:
    """Mark failed the jobs still marked running: with the worker lock held, the worker that ran
    them is gone. Left as they are, their locks would hold back every later job on the same
    mailbox. Needs the app open."""
    interrupted = [
        job.id
        for job in await app.job_manager.list_jobs_async(status=Status.DOING.value)
        if job.id is not None
    ]
    for job_id in interrupted:
        await app.job_manager.finish_job_by_id_async(
            job_id=job_id, status=Status.FAILED, delete_job=False
        )
    if interrupted:
        logger.warning(
            "Marked failed %s job(s) a stopped worker left running: %s",
            len(interrupted),
            interrupted,
        )
    return interrupted
