"""What the app's mail tests share: the TLS settings they trust the mail servers with (the app
under test's, and their own clients'), and a wait for the sends they start.

Against the dev mail backends, whose certificates the dev CA signs, the tests verify TLS as
production does: ``MAILROOM_MAIL_CA_FILE`` names that CA (mise sets it). Against throwaway servers
(``scripts/test-mail.sh``, CI's detached steps), whose certificates nothing signs, the variable is
empty and the tests trust the server as mailroom-core's own tests do. That ``mail_ssl_context``
verifies is tested on its own (``test_api_accounts.py``).
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from mailroom.mail.connections import MAIL_CA_FILE_ENV, mail_ssl_context
from mailroom_core.testing import unverified_context

if TYPE_CHECKING:
    import ssl

    from mailroom.mail.sends import SendRunner


def mail_servers_ssl_context() -> ssl.SSLContext:
    """``mail_ssl_context()`` when ``MAILROOM_MAIL_CA_FILE`` names a CA, otherwise one that
    trusts any certificate."""
    if os.environ.get(MAIL_CA_FILE_ENV, "").strip():
        return mail_ssl_context()
    return unverified_context()


def settle_sends(runner: SendRunner, timeout: float = 30) -> None:
    """Waits for the runner's sends, filing and draft removal included: a send still running
    after its test would record its outcome in the next test's transaction."""
    deadline = time.monotonic() + timeout
    while True:
        with runner.lock:
            if runner.held.total() == 0:
                return
        assert time.monotonic() < deadline, "a send is still running"
        time.sleep(0.02)
