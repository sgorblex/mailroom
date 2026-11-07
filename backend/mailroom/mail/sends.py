"""Sends after the route has answered (``docs/requirements/mail-client.md``, *Send state
machine*): the threads that run them, and how many may run.

There is no queue. A request takes a slot before anything is claimed, and with none free for its
user it is ``busy`` with nothing written down, so a claimed send always has a thread and starts
within the time it reads as ``sending``. What a thread does with a send (``mailroom.mail.service``)
ends in the send's row, whatever happens, or leaves it ``sending``, which reads as ``unknown``
past mailroom-core's ceiling: that is also what a restart leaves behind. At shutdown, sends get a
moment to finish and are then cut, which ends them as a timeout would.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from typing import TYPE_CHECKING, Final

from mailroom_core.mail import MailError
from mailroom_core.send import Transfer

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Future

logger = logging.getLogger(__name__)

THREADS: Final = 4
PER_USER: Final = 2
GRACE_SECONDS: Final = 5.0
"""How long a send still running at shutdown gets, inside the container's stop timeout."""

type SendJob = Callable[[Transfer], None]
"""Runs one send; the ``Transfer`` is what ``SendRunner.close`` cuts it with."""


class SendSlot:
    """One send's place in the runner, from before its claim: ``start`` it, or ``release`` it.
    Safe to do both, in either order, from different threads: ``release`` does nothing to a
    started send, and a send started after ``release`` (its caller stopped waiting for the
    admission that claimed it) takes its place back, over the limit for a moment, because a
    claimed send must run."""

    def __init__(self, runner: SendRunner, user_id: int) -> None:
        self._runner = runner
        self._user_id = user_id
        self._started = False
        self._released = False

    def start(self, job: SendJob) -> None:
        with self._runner.lock:
            if self._started:
                msg = "this slot has started its send"
                raise ValueError(msg)
            self._started = True
            if self._released:
                self._runner.held[self._user_id] += 1
        self._runner.run(self._user_id, job)

    def release(self) -> None:
        with self._runner.lock:
            if self._started or self._released:
                return
            self._released = True
            self._runner.leave(self._user_id)


class SendRunner:
    """The process's sends, ``threads`` at once, ``per_user`` of one user. Thread-safe; ``close``
    once, at shutdown."""

    def __init__(
        self,
        *,
        threads: int = THREADS,
        per_user: int = PER_USER,
        grace_seconds: float = GRACE_SECONDS,
    ) -> None:
        self._threads = threads
        self._per_user = per_user
        self._grace_seconds = grace_seconds
        self.lock = threading.Lock()
        self.held: Counter[int] = Counter()
        self._running: dict[Future[None], Transfer] = {}
        self._executor = ThreadPoolExecutor(max_workers=threads, thread_name_prefix="send")
        self._closed = False

    def reserve(self, user_id: int) -> SendSlot:
        """A place for one send of the user's, or ``MailError("busy")``."""
        with self.lock:
            if (
                self._closed
                or self.held.total() >= self._threads
                or self.held[user_id] >= self._per_user
            ):
                raise MailError("busy")
            self.held[user_id] += 1
        return SendSlot(self, user_id)

    def leave(self, user_id: int) -> None:
        """Under ``lock``."""
        self.held[user_id] -= 1
        if self.held[user_id] <= 0:
            del self.held[user_id]

    def run(self, user_id: int, job: SendJob) -> None:
        transfer = Transfer()
        with self.lock:
            future = self._executor.submit(self._work, job, transfer)
            self._running[future] = transfer

        def done(finished: Future[None]) -> None:
            with self.lock:
                del self._running[finished]
                self.leave(user_id)

        future.add_done_callback(done)

    @staticmethod
    def _work(job: SendJob, transfer: Transfer) -> None:
        try:
            job(transfer)
        except Exception as error:
            logger.warning(
                "mail_send_job_failed",
                extra={"event": "mail_send_job_failed", "error_type": type(error).__name__},
            )

    def close(self) -> None:
        """No more sends; the ones running get ``grace_seconds``, then their connections are
        cut."""
        with self.lock:
            self._closed = True
            running = list(self._running)
        if running:
            wait(running, timeout=self._grace_seconds)
        with self.lock:
            for transfer in self._running.values():
                transfer.abort()
        self._executor.shutdown(wait=False)
