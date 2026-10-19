"""The example config and the mailbox secrets it names, as `src/testing/fixtures.ts` has them.

The bootstrap's own (the public URL, the login hash, the signing key) are mailroom-mcp's, in
`mailroom_mcp.testing`.
"""

import os
import re
from functools import cache
from pathlib import Path


@cache
def example_config_text() -> str:
    """config.example.toml, from the root of the repository this package is checked out in.

    Works only from a checkout of this repository: the file is at its root, not in the package.
    """
    for directory in Path(__file__).resolve().parents:
        if (directory / "config.example.toml").is_file():
            return (directory / "config.example.toml").read_text()
    raise FileNotFoundError("config.example.toml: not in a checkout of the mailroom-mcp repository")


@cache
def local_config_text() -> str:
    """The example config with every mail endpoint on a closed local port.

    The example names real mail servers: pointed here, no test can ever attempt a login there
    (the no-network guard refuses it as well).
    """
    return re.sub(
        r'host = "[^"]+", port = \d+', 'host = "127.0.0.1", port = 9', example_config_text()
    )


def config_env() -> dict[str, str]:
    """The mailbox secrets the example config names, with a fresh secrets key."""
    return {
        "MAILROOM_SECRETS_KEY": os.urandom(32).hex(),
        "HOME_TOKEN": "home-secret",
        "WORK_APP_PASSWORD": "google-secret",
    }
