"""Structlog + RotatingFileHandler setup (O1 pillar 1, O4).

Per-pid JSON lines file under {obs_dir}/logs/{pid}.jsonl. Multi-process safety
via pid isolation (no QueueHandler daemon thread, per O3 zero-overhead).
Library installs NullHandler by default (stdlib convention, __init__.py:2300).
Observability handler attached only under if __debug__: (O3).
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from channels_shm._obs.config import ObservabilityConfig


def configure_logging(
    config: ObservabilityConfig, pid: int
) -> structlog.stdlib.BoundLogger:
    """Configure structlog to write JSON lines to per-pid rotating file.

    Caller MUST invoke this under `if __debug__:` block. Release (python -O)
    will not call this, leaving only NullHandler installed at import time.
    """
    os.makedirs(config.logs_dir, exist_ok=True)
    log_path = f"{config.logs_dir}/{pid}.jsonl"

    handler = RotatingFileHandler(
        log_path,
        maxBytes=config.log_max_bytes,
        backupCount=config.log_backup_count,
    )

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=handler.stream),
        cache_logger_on_first_use=True,
    )
    # Attach the handler to stdlib logger too (structlog routes through it)
    lib_logger = logging.getLogger("channels_shm")
    lib_logger.addHandler(handler)
    lib_logger.setLevel(logging.INFO)

    return structlog.get_logger("channels_shm").bind(pid=pid)
