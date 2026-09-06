"""Watchdog compaction wiring and pump coverage gaps.

The watchdog tick now runs native compact under the global flock (§9.7
watchdog-triggered repair): it finishes E1/D1 residual slots and repairs
parked by a crashed recoverer. Two consecutive ticks form compact's two-pass
confirmation. Also covers the stall-detection drain, the drop-oldest metric,
watchdog task replacement on restart, and the context manager.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from channels_shm.pump import ReceivePump, _BoundedQueue

if TYPE_CHECKING:
    from collections.abc import Callable

    from channels_shm import SharedMemoryChannelLayer
    from channels_shm.shm.lock import FlushLock


def _fresh_pump(
    layer: SharedMemoryChannelLayer,
    *,
    interval: int = 1,
    lock: FlushLock | None = None,
) -> ReceivePump:
    region = layer._region
    slab = layer._slab
    channel_mgr = layer._channel_mgr
    wakeup = layer._wakeup
    assert region is not None
    assert slab is not None
    assert channel_mgr is not None
    assert wakeup is not None
    return ReceivePump(
        region=region.native,
        slab=slab,
        channel_mgr=channel_mgr,
        wakeup=wakeup,
        capacity=layer.capacity,
        expiry=layer.expiry,
        pid=layer.pid,
        start_time=layer.start_time,
        watchdog_interval=interval,
        lock=lock,
    )


class TestWatchdogCompactWiring:
    """The watchdog tick compacts under flock; without a lock it skips."""

    async def test_layer_passes_its_lock_to_the_pump(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        pump = layer._pump
        assert pump is not None
        assert pump._lock is layer._lock

    async def test_watchdog_compacts_under_flock_and_detects_stall(
        self,
        layer: SharedMemoryChannelLayer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import channels_shm.pump as pump_mod

        compact_calls: list[tuple[object, ...]] = []

        def spy_compact(*args: object, **_kwargs: object) -> None:
            compact_calls.append(args)

        monkeypatch.setattr(pump_mod, "native_compact", spy_compact)
        drained: list[int] = []
        pump = _fresh_pump(layer, interval=1, lock=layer._lock)
        monkeypatch.setattr(pump, "drain_rings", lambda: drained.append(1))

        loop = asyncio.get_running_loop()
        pump.start(loop)
        await asyncio.sleep(1.15)  # tick 1: compact + arm (P-11)
        assert len(compact_calls) == 1
        assert not drained, "fresh pump must not look stuck (P-11 re-arm)"

        # Stall the drain timestamp: tick 2 must detect it and drain.
        pump.last_drain_ts = time.monotonic() - 10.0
        await asyncio.sleep(1.15)  # tick 2: compact + stall → drain
        pump.stop()
        assert len(compact_calls) == 2
        assert drained, "stall detection must trigger a drain"

    async def test_watchdog_skips_compact_without_lock(
        self,
        layer: SharedMemoryChannelLayer,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import channels_shm.pump as pump_mod

        compact_calls: list[tuple[object, ...]] = []

        def spy_compact(*args: object, **_kwargs: object) -> None:
            compact_calls.append(args)

        monkeypatch.setattr(pump_mod, "native_compact", spy_compact)
        pump = _fresh_pump(layer, interval=1, lock=None)
        pump.start(asyncio.get_running_loop())
        await asyncio.sleep(1.15)
        pump.stop()
        assert compact_calls == [], "pump without a lock must not compact"

    async def test_watchdog_task_replaced_on_restarted_start(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """start() on an already-running pump discards the prior task (P-12)."""
        layer = layer_factory(watchdog_interval=0)
        pump = _fresh_pump(layer, interval=3600)
        loop = asyncio.get_running_loop()
        pump.start(loop)
        first = pump._watchdog_task
        assert first is not None
        assert not first.done()
        pump.start(loop)
        second = pump._watchdog_task
        assert second is not None
        assert second is not first
        pump.stop()


class TestBoundedQueueMetrics:
    """The drop-oldest path increments its diagnostic counter (P-05)."""

    def test_drop_oldest_counts_metric(self) -> None:
        class _Counter:
            count: int

            def __init__(self) -> None:
                self.count = 0

            def inc(self, **_labels: object) -> None:
                self.count += 1

        class _Metrics:
            drop: _Counter

            def __init__(self) -> None:
                self.drop = _Counter()

            def counter(self, _name: str) -> _Counter:
                return self.drop

        metrics = _Metrics()
        q: _BoundedQueue = _BoundedQueue(maxsize=1, metrics=metrics)
        q.put_nowait({"type": "1"})
        q.put_nowait({"type": "2"})  # full → drop oldest, count it
        assert q.qsize() == 1
        assert q.get_nowait() == {"type": "2"}
        assert metrics.drop.count == 1


class TestPumpContextManager:
    async def test_context_manager_stops(self, layer: SharedMemoryChannelLayer) -> None:
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()
        with pump as entered:
            assert entered is pump
        pump.stop()  # idempotent


class TestWatchdogIntervalGuard:
    async def test_watchdog_loop_without_interval_raises(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """The loop body guards against a None interval (start() filters it)."""
        pump = _fresh_pump(layer, interval=1)
        pump._watchdog_interval = None
        with pytest.raises(RuntimeError, match="without an interval"):
            await pump._watchdog_loop()
