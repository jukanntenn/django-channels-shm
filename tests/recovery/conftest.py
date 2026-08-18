"""Fixtures + helpers for fault-injection recovery tests.

These tests SIGKILL child processes or fabricate corrupted shm states, so each
test gets a unique prefix (parallel-safe) plus a fixture that removes every
artifact afterwards. Like cross_process, this area is slow and Linux-only.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import shutil
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="recovery tests require Linux MAP_SHARED + AF_UNIX",
)

Counter = dict[str, int]


@pytest.fixture
def recv_prefix() -> str:
    """Unique shm prefix for one recovery test (parallel-safe)."""
    return f"recv_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def recv_ctx() -> Iterator[mp.context.BaseContext]:
    """multiprocessing spawn context (clean child state, no inherited fds)."""
    yield mp.get_context("spawn")  # noqa: PT022


@pytest.fixture
def recv_cleanup(recv_prefix: str) -> Iterator[None]:
    """Remove every artifact (shm file, wakeup dir, obs dir) under the prefix."""
    yield
    for path in (
        f"/dev/shm/{recv_prefix}",
        f"/dev/shm/{recv_prefix}_wakeup",
        f"/dev/shm/{recv_prefix}_obs",
    ):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.islink(path) or os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


def kill_process(pid: int, timeout: float = 5.0) -> None:
    """SIGKILL a process and wait for it to exit."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.01)
    pytest.fail(f"process {pid} did not die within {timeout}s")


def read_metrics(prefix: str) -> dict[str, Counter]:
    """Aggregated counters from /dev/shm/{prefix}_obs/metrics/*.json."""
    obs = Path(f"/dev/shm/{prefix}_obs/metrics")
    agg: dict[str, Counter] = {}
    if not obs.exists():
        return agg
    for f in obs.glob("*.json"):
        data = cast(
            "dict[str, list[dict[str, object]]]",
            json.loads(f.read_text()),
        )
        for c in data.get("counters", []):
            name = cast("str", c["name"])
            values = cast("list[dict[str, object]]", c["values"])
            for v in values:
                key = ",".join(
                    f"{k}={val}" for k, val in sorted(v.items()) if k != "value"
                )
                counter = agg.setdefault(name, {})
                counter[key] = counter.get(key, 0) + cast("int", v["value"])
    return agg


def assert_counter_ge(
    metrics: dict[str, Counter], name: str, expected: int = 1
) -> None:
    """Assert a counter's total across all labels is >= expected."""
    if name not in metrics:
        pytest.fail(f"counter {name} not found in metrics (recovery did NOT fire?)")
    assert sum(metrics[name].values()) >= expected, (
        f"counter {name} total = {sum(metrics[name].values())}, expected >= {expected}"
    )
