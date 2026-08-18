"""Unit tests for channels_shm._obs.logging_setup (per-pid JSONL logging).

Maps to src/channels_shm/_obs/logging_setup.py. configure_logging wires a
structlog bound logger to a per-pid RotatingFileHandler; tests point obs_dir at
a temp dir and clean up the handler afterwards (configuring structlog and
attaching a stdlib handler are global side effects).
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from channels_shm._obs.config import ObservabilityConfig
from channels_shm._obs.logging_setup import configure_logging

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


class _ObsLogger(Protocol):
    """Minimal structlog-style logger surface used by these tests.

    structlog ships no type stubs, so the fixture is typed via this protocol
    (same approach as layer.py's _ObsLog) instead of leaking `Any`.
    """

    def info(self, event: str, **fields: object) -> None: ...
    def warning(self, event: str, **fields: object) -> None: ...


def _jsonl_records(path: Path) -> list[dict[str, object]]:
    """Parse a JSONL file into typed records (json.loads yields Any)."""
    return [
        cast("dict[str, object]", json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    ]


@pytest.fixture
def obs_logger(tmp_path: Path) -> Iterator[_ObsLogger]:
    """A configured structlog logger writing into a temp obs dir."""
    config = ObservabilityConfig("prefix", obs_dir=str(tmp_path))
    log = configure_logging(config, pid=4242)
    yield log
    # configure_logging is global state; drop the handler it installed so
    # other tests don't inherit a file handler pointed at a tmp_path.
    channels_shm_logger = logging.getLogger("channels_shm")
    channels_shm_logger.handlers.clear()


def test_writes_per_pid_jsonl(obs_logger: _ObsLogger, tmp_path: Path) -> None:
    """Logging emits one JSON line per event to {obs_dir}/logs/{pid}.jsonl."""
    obs_logger.info("hello", field="value")

    log_file = tmp_path / "logs" / "4242.jsonl"
    assert log_file.exists()
    records = _jsonl_records(log_file)
    assert any(
        rec.get("event") == "hello" and rec.get("field") == "value" for rec in records
    )


def test_log_records_pid(obs_logger: _ObsLogger, tmp_path: Path) -> None:
    """Every record carries the pid the logger was bound to."""
    obs_logger.info("tagged")
    log_file = tmp_path / "logs" / "4242.jsonl"
    records = _jsonl_records(log_file)
    assert records
    assert records[-1]["pid"] == 4242


def test_creates_logs_dir(tmp_path: Path) -> None:
    """configure_logging creates the nested logs directory."""
    config = ObservabilityConfig("prefix", obs_dir=str(tmp_path / "deep" / "obs"))
    _ = configure_logging(config, pid=os.getpid())
    try:
        assert (tmp_path / "deep" / "obs" / "logs").is_dir()
        assert (tmp_path / "deep" / "obs" / "logs" / f"{os.getpid()}.jsonl").exists()
    finally:
        logging.getLogger("channels_shm").handlers.clear()


def test_returns_bound_logger(obs_logger: _ObsLogger) -> None:
    """The returned object is a structlog bound logger with an info method."""
    assert callable(obs_logger.info)
    assert callable(obs_logger.warning)
