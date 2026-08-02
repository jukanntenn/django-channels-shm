"""X1 crash-injection fixtures: fork+SIGKILL helpers + observability assertions."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import pytest


def kill_process(pid: int, timeout: float = 5.0) -> None:
    """SIGKILL a process and wait for exit."""
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


def read_metrics(prefix: str) -> dict:
    """Read aggregated metrics from /dev/shm/{prefix}_obs/metrics/*.json."""
    obs = Path(f"/dev/shm/{prefix}_obs/metrics")
    agg: dict[str, dict[str, int]] = {}
    if not obs.exists():
        return agg
    for f in obs.glob("*.json"):
        data = json.loads(f.read_text())
        for c in data.get("counters", []):
            name = c["name"]
            agg.setdefault(name, {})
            for v in c["values"]:
                key = ",".join(
                    f"{k}={val}" for k, val in sorted(v.items()) if k != "value"
                )
                agg[name][key] = agg[name].get(key, 0) + v["value"]
    return agg


def assert_counter_ge(
    metrics: dict, name: str, expected: int = 1, label: str | None = None
) -> None:
    """Assert a counter (with optional label) >= expected."""
    if name not in metrics:
        pytest.fail(f"counter {name} not found in metrics (recovery did NOT fire?)")
    total = (
        sum(metrics[name].values()) if label is None else metrics[name].get(label, 0)
    )
    assert total >= expected, (
        f"counter {name}{f'[{label}]' if label else ''} = {total}, expected >= {expected}"
    )


def assert_counter_eq(
    metrics: dict, name: str, expected: int = 0, label: str | None = None
) -> None:
    """Assert a counter == expected."""
    actual = 0
    if name in metrics:
        actual = (
            sum(metrics[name].values())
            if label is None
            else metrics[name].get(label, 0)
        )
    assert actual == expected, (
        f"counter {name}{f'[{label}]' if label else ''} = {actual}, expected == {expected}"
    )
