"""Logging configuration for the IMAP thingy library."""

import logging
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

    This configures the root logger with both stream (stdout) and file handlers.
    If the root logger already has handlers configured, this function does nothing.
    The logging format includes timestamp, level, logger name, and message.

    Args:
        logfile: Path to the log file (default: "imap_thingy.log").
        root_level: Logging level for the root logger (default: logging.INFO).
        stream_level: Logging level for stdout stream handler (default: logging.INFO).
        file_level: Logging level for the file handler (default: logging.INFO).

    """
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
