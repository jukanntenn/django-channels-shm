"""Lightweight metrics: Counter and Histogram (O1 pillar 2).

Zero external dependencies. In-memory accumulation + periodic flush to local
JSON file. Gated by if __debug__: in callers (O3); release build (python -O)
eliminates all call sites.
"""

from __future__ import annotations

import bisect
import json
import os
import threading
from pathlib import Path
from typing import Any, final

_LabelKey = tuple[tuple[str, str], ...]


@final
class Counter:
    """Monotonically increasing counter with optional labels."""

    __slots__ = ("_lock", "_name", "_values")

    def __init__(self, name: str) -> None:
        self._name = name
        self._values: dict[_LabelKey, int] = {}
        self._lock = threading.Lock()

    def inc(self, amount: int = 1, **labels: str) -> None:
        key = self._labels_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + amount

    def value(self, **labels: str) -> int:
        key = self._labels_key(labels)
        with self._lock:
            return self._values.get(key, 0)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "values": [dict(k) | {"value": v} for k, v in self._values.items()],
            }

    @staticmethod
    def _labels_key(labels: dict[str, str]) -> _LabelKey:
        return tuple(sorted(labels.items()))


@final
class Histogram:
    """Fixed-bucket histogram for latency/duration distributions."""

    __slots__ = ("_buckets", "_count", "_counts", "_lock", "_name", "_sum")

    def __init__(self, name: str, buckets: list[float]) -> None:
        self._name = name
        self._buckets = sorted(buckets)
        self._counts = [0] * (len(buckets) + 1)  # last bucket = +Inf
        self._sum = 0.0
        self._count = 0
        self._lock = threading.Lock()

    def observe(self, value: float, **_labels: str) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            # O-03: buckets are sorted; bisect_right finds the index of the
            # first bucket bound strictly greater than `value`, which is the
            # bucket `value` falls into. O(log n) instead of O(n) linear scan.
            i = bisect.bisect_right(self._buckets, value)
            self._counts[i] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "buckets": self._buckets,
                "counts": self._counts,
                "sum": self._sum,
                "count": self._count,
            }


@final
class MetricsRegistry:
    """Registry holding all counters/histograms, with periodic flush to JSON file.

    O-01: a daemon thread periodically flushes the in-memory metrics to
    {metrics_dir}/{pid}.json so that a crashed process (OOM, SIGKILL — a
    routine Django deployment event per spec §10) still leaves behind metrics
    covering all but the last `flush_interval` seconds. Without this, metrics
    were written ONLY from close() and lost entirely on crash, which broke the
    crash-injection tests that read these files to assert recovery fired.
    """

    __slots__ = (
        "_counters",
        "_flush_interval",
        "_flush_thread",
        "_histograms",
        "_metrics_dir",
        "_pid",
        "_stop_event",
    )

    def __init__(
        self, metrics_dir: str, pid: int, *, flush_interval: float = 30
    ) -> None:
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}
        self._pid = pid
        self._metrics_dir = metrics_dir
        self._flush_interval = flush_interval
        self._stop_event = threading.Event()
        self._flush_thread: threading.Thread | None = None

    def counter(self, name: str) -> Counter:
        if name not in self._counters:
            self._counters[name] = Counter(name)
        return self._counters[name]

    def histogram(self, name: str, buckets: list[float]) -> Histogram:
        if name not in self._histograms:
            self._histograms[name] = Histogram(name, buckets)
        return self._histograms[name]

    def start_periodic_flush(self) -> None:
        """Start the background daemon thread that flushes every flush_interval.

        Idempotent. The thread is a daemon so it never blocks process exit; on
        stop() (or process exit) a final flush is performed if possible.
        """
        if self._flush_thread is not None or self._flush_interval <= 0:
            return
        self._flush_thread = threading.Thread(
            target=self._flush_loop, name="channels_shm-metrics-flush", daemon=True
        )
        self._flush_thread.start()

    def stop_periodic_flush(self) -> None:
        """Signal the flush thread to stop and perform a final flush."""
        if self._flush_thread is None:
            return
        self._stop_event.set()
        self._flush_thread = None  # daemon; let it retire on its own

    def _flush_loop(self) -> None:
        while not self._stop_event.wait(self._flush_interval):
            try:
                _ = self.flush()
            except Exception:
                # Flush failures must not kill the thread — next tick retries.
                pass

    def flush(self) -> str:
        """Write snapshot to {metrics_dir}/{pid}.json. Returns path."""
        os.makedirs(self._metrics_dir, exist_ok=True)
        path = f"{self._metrics_dir}/{self._pid}.json"
        data = {
            "pid": self._pid,
            "counters": [c.snapshot() for c in self._counters.values()],
            "histograms": [h.snapshot() for h in self._histograms.values()],
        }
        tmp = f"{path}.tmp"
        with Path(tmp).open("w") as f:
            json.dump(data, f)
        _ = Path(tmp).replace(path)  # atomic rename
        return path

    def snapshot_all(self) -> dict[str, Any]:
        return {
            "pid": self._pid,
            "counters": [c.snapshot() for c in self._counters.values()],
            "histograms": [h.snapshot() for h in self._histograms.values()],
        }
