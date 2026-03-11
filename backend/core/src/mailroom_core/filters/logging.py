"""Deprecated logging helper kept for backward compatibility.

This module previously configured the root logger with stream and file handlers.
It is no longer recommended; configure logging in your own application instead.
"""

import logging
import warnings
from sys import stdout

LOGFILE = "imap_thingy.log"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(
    logfile: str = LOGFILE,
    root_level: int = logging.INFO,
    stream_level: int = logging.INFO,
    file_level: int = logging.INFO,
) -> None:
    """Set up logging for the application.

    .. deprecated::
        Use ``logging.basicConfig()`` or your own logging configuration instead.
    """
    warnings.warn(
        "imap_thingy.logging.setup_logging() is deprecated. Configure logging in your application directly (e.g. logging.basicConfig()).",
        FutureWarning,
        stacklevel=2,
    )
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        return
    root_logger.setLevel(root_level)
    stream_handler = logging.StreamHandler(stdout)
    stream_handler.setLevel(stream_level)
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    file_handler = logging.FileHandler(logfile)
    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
