"""The example config, as the TypeScript server's `src/testing/fixtures.ts` had it: its accounts
and limits are what core's config tests check."""

import re
from functools import cache
from importlib.resources import files


@cache
def example_config_text() -> str:
    """mailroom-mcp's example config, kept next to this module as a fixture."""
    return files("mailroom_core.testing").joinpath("config.example.toml").read_text()


@cache
def local_config_text() -> str:
    """The example config with every mail endpoint on a closed local port.

    The example names real mail servers: pointed here, no test can ever attempt a login there
    (the no-network guard refuses it as well).
    """
    return re.sub(
        r'host = "[^"]+", port = \d+', 'host = "127.0.0.1", port = 9', example_config_text()
    )
