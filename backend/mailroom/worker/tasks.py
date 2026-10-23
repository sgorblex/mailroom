"""The worker's tasks."""

from __future__ import annotations

import logging

from mailroom.worker.app import app

logger = logging.getLogger(__name__)


@app.task(name="ping")
def ping() -> None:
    """Does nothing: proves a job goes from the queue through a worker."""
    logger.info("pong")
