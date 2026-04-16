"""
Structured logging with daily rotation to logs/ directory.
All modules should call get_logger() to obtain a shared logger instance.
"""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

from config.settings import LOGS_DIR

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOGGER_NAME = "daily_data_jobs"


def setup_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """
    Create and configure the application logger.

    Sets up:
    - StreamHandler to stdout (captured by cron via >> cron.log 2>&1)
    - TimedRotatingFileHandler rotating at midnight, 30 days retention

    Idempotent: calling twice with the same name returns the same logger
    without adding duplicate handlers.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        # Already configured — return as-is
        return logger

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # --- stdout handler (INFO+) ---
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # --- rotating file handler (DEBUG+) ---
    os.makedirs(LOGS_DIR, exist_ok=True)
    log_file_path = os.path.join(LOGS_DIR, "pipeline.log")
    file_handler = TimedRotatingFileHandler(
        filename=log_file_path,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
        utc=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Prevent log records from propagating to root logger
    logger.propagate = False

    return logger


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    """
    Return the configured application logger.
    Initialises it on first call if not yet set up.
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        setup_logger(name)
    return logger
