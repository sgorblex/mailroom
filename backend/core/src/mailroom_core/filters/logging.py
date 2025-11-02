import logging
from sys import stdout

LOGFILE = "imap_thingy.log"
STREAM_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
FILE_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(
    logfile: str = LOGFILE,
    root_level: int = logging.DEBUG,
    stream_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
) -> None:
    """
    Set up logging for the application. This configures the root logger and any external loggers (e.g., 'imapclient').
    - logfile: path to the log file (default: imap_thingy.log)
    - root_level: level for the root logger (default: DEBUG)
    - stream_level: level for stdout (default: INFO)
    """
    root_logger = logging.getLogger()
    if root_logger.hasHandlers():
        return
    root_logger.handlers.clear()
    root_logger.setLevel(root_level)
    stream_handler = logging.StreamHandler(stdout)
    stream_handler.setLevel(stream_level)
    stream_handler.setFormatter(logging.Formatter(STREAM_LOG_FORMAT))
    file_handler = logging.FileHandler(logfile)
    file_handler.setLevel(file_level)
    file_handler.setFormatter(logging.Formatter(FILE_LOG_FORMAT))
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)
