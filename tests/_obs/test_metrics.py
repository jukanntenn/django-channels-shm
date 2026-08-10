"""Unit tests for channels_shm._obs.metrics.

Maps to src/channels_shm/_obs/metrics.py. The counter/histogram/registry are
invoked from `if __debug__:` blocks in production code, but they are plain
thread-safe primitives that need no special build mode to test.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from channels_shm._obs.metrics import Counter, Histogram, MetricsRegistry

if TYPE_CHECKING:
    import pytest


class TestCounter:
    """Monotonic counter with sorted-label keys."""

    def test_inc_and_value(self) -> None:
        c = Counter("c")
        c.inc()
        c.inc(2)
        assert c.value() == 3

    def test_value_unknown_labels_zero(self) -> None:
        assert Counter("c").value(labels="x") == 0

    def test_labels_order_insensitive(self) -> None:
        c = Counter("c")
        c.inc(1, a="1", b="2")
        assert c.value(b="2", a="1") == 1

    def test_snapshot(self) -> None:
        c = Counter("c")
        c.inc(3, channel="x")
        assert c.snapshot() == {"name": "c", "values": [{"channel": "x", "value": 3}]}


class TestHistogram:
    """Fixed-bucket histogram; bisect_right assigns values to buckets."""

    def test_bucket_assignment(self) -> None:
        h = Histogram("h", [1, 5, 10])
        for v in (0.5, 1, 5, 6, 10, 100):
            h.observe(v)
        # bisect_right: a value <= bound lands in that bound's bucket (index =
        # number of bounds <= value); only values above ALL bounds land in the
        # +Inf bucket.
        assert h.snapshot()["counts"] == [1, 1, 2, 2]

    def test_sum_and_count(self) -> None:
        h = Histogram("h", [1])
        h.observe(0.5)
        h.observe(2)
        snap = h.snapshot()
        assert snap["sum"] == 2.5
        assert snap["count"] == 2

    def test_buckets_sorted(self) -> None:
        h = Histogram("h", [10, 1, 5])  # unsorted input
        assert h.snapshot()["buckets"] == [1, 5, 10]

    def test_snapshot(self) -> None:
        h = Histogram("h", [1])
        h.observe(0.5)
        assert h.snapshot() == {
            "name": "h",
            "buckets": [1],
            "counts": [1, 0],
            "sum": 0.5,
            "count": 1,
        }


class TestMetricsRegistry:
    """Counter/histogram ownership and (atomic) JSON flush."""

    def test_counter_singleton(self, tmp_path: Path) -> None:
        r = MetricsRegistry(str(tmp_path), 1)
        assert r.counter("a") is r.counter("a")

    def test_histogram_singleton(self, tmp_path: Path) -> None:
        r = MetricsRegistry(str(tmp_path), 1)
        assert r.histogram("h", [1]) is r.histogram("h", [1])

    def test_snapshot_all(self, tmp_path: Path) -> None:
        r = MetricsRegistry(str(tmp_path), 42)
        r.counter("c").inc(2)
        r.histogram("h", [1]).observe(0.5)
        snap = r.snapshot_all()
        assert snap["pid"] == 42
        assert snap["counters"][0]["name"] == "c"
        assert snap["histograms"][0]["name"] == "h"

    def test_flush_creates_atomic_json(self, tmp_path: Path) -> None:
        r = MetricsRegistry(str(tmp_path), 7)
        r.counter("c").inc(2)
        path = r.flush()
        assert path == f"{tmp_path}/7.json"
        assert Path(path).exists()
        assert not Path(f"{path}.tmp").exists()  # atomic rename left no temp
        data = cast("dict[str, object]", json.loads(Path(path).read_text()))
        assert data["pid"] == 7
        counters = cast("list[dict[str, object]]", data["counters"])
        values = cast("list[dict[str, object]]", counters[0]["values"])
        assert values[0]["value"] == 2

    def test_flush_creates_dirs(self, tmp_path: Path) -> None:
        r = MetricsRegistry(str(tmp_path / "nested" / "dir"), 1)
        assert Path(r.flush()).exists()


class TestPeriodicFlush:
    """Background daemon-thread flush (O-01)."""

    def test_start_idempotent(self, tmp_path: Path) -> None:
        r = MetricsRegistry(str(tmp_path), 1)
        r.start_periodic_flush()
        thread = r._flush_thread
        assert thread is not None
        r.start_periodic_flush()  # no-op
        assert r._flush_thread is thread
        r.stop_periodic_flush()

    def test_start_skips_zero_interval(self, tmp_path: Path) -> None:
        r = MetricsRegistry(str(tmp_path), 1, flush_interval=0)
        r.start_periodic_flush()
        assert r._flush_thread is None

    def test_stop_without_start(self, tmp_path: Path) -> None:
        MetricsRegistry(str(tmp_path), 1).stop_periodic_flush()  # no-op

    def test_periodic_flush_writes_file(self, tmp_path: Path) -> None:
        r = MetricsRegistry(str(tmp_path), 1, flush_interval=0.02)
        r.counter("c").inc()
        r.start_periodic_flush()
        try:
            deadline = time.monotonic() + 2
            while not (tmp_path / "1.json").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            r.stop_periodic_flush()
        assert (tmp_path / "1.json").exists()

    def test_flush_loop_survives_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing flush must not kill the daemon thread (next tick retries)."""
        r = MetricsRegistry(str(tmp_path), 1, flush_interval=0.02)

        def boom(_self: object) -> str:
            raise OSError("disk full")

        monkeypatch.setattr(MetricsRegistry, "flush", boom)
        r.start_periodic_flush()
        time.sleep(0.05)  # let the loop tick and hit the error
        r.stop_periodic_flush()
