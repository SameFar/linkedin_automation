"""Logging setup for every linkedos entry point (CLI, UI, scheduler).

One root handler set: a console stream plus a rotating file at `data/logs/linkedos.log`.
Modules never configure logging themselves; they call `get_logger(__name__)` and let
whichever entry point started the process decide where the records go.

Nothing here ever formats a secret. Settings are read for the level and paths only.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from linkedos.core.config import get_settings

PACKAGE_LOGGER = "linkedos"
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


def configure_logging(level: str | None = None, log_dir: Path | None = None) -> logging.Logger:
    """Attach console and rotating-file handlers to the `linkedos` logger.

    Idempotent: calling it twice does not duplicate handlers, which matters because
    Streamlit re-executes the app script on every interaction.

    Args:
        level: Overrides `settings.log_level`.
        log_dir: Overrides `settings.log_dir`. The directory is created if absent.

    Returns:
        The configured `linkedos` package logger.
    """
    settings = get_settings()
    resolved_level = (level or settings.log_level).upper()
    resolved_dir = log_dir if log_dir is not None else settings.log_dir

    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(resolved_level)
    # Records stop here; the root logger stays out of it.
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    console = logging.StreamHandler(stream=sys.stderr)
    console.setFormatter(formatter)
    logger.addHandler(console)

    resolved_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        resolved_dir / "linkedos.log",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger for a module.

    Pass `__name__`. Modules inside the package already sit under `linkedos.*`, so
    they inherit the configured handlers; anything else is re-parented explicitly.
    """
    if name == PACKAGE_LOGGER or name.startswith(f"{PACKAGE_LOGGER}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{PACKAGE_LOGGER}.{name}")
