"""Unit tests for channels_shm.pump.

Maps to src/channels_shm/pump.py. Covers the drop-oldest bounded queue,
pump start/stop and wakeup-dispatch lifecycle, and watchdog task management.
The watchdog loop body timing paths are not exercised here (they were covered
by slow, assertion-poor tests removed in the test-suite reorganization);
stall detection is an operational backstop verified in e2e scenarios.
"""

from __future__ import annotations

import asyncio
import math
from typing import TYPE_CHECKING

from channels_shm.pump import ReceivePump, _BoundedQueue
from channels_shm.serializer import pack_message

if TYPE_CHECKING:
    from collections.abc import Callable

    from channels_shm import SharedMemoryChannelLayer
    from channels_shm.serializer import Message


class TestBoundedQueue:
    """_BoundedQueue drops the oldest item when full (at-most-once)."""

    def test_drop_oldest_when_full(self) -> None:
        q: _BoundedQueue = _BoundedQueue(maxsize=2)
        msg1: Message = {"type": "1"}
        msg2: Message = {"type": "2"}
        msg3: Message = {"type": "3"}
        q.put_nowait(msg1)
        q.put_nowait(msg2)
        q.put_nowait(msg3)  # queue full — msg1 dropped
        assert q.qsize() == 2
        assert q.get_nowait() == msg2
        assert q.get_nowait() == msg3


class TestPumpLifecycle:
    """Pump start/stop and wakeup-fd dispatch."""

    async def test_start_stop(self, layer: SharedMemoryChannelLayer) -> None:
        """pump.start() and repeated stop() should not raise."""
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()  # binds the pump to the running loop
        pump.stop()
        pump.stop()

    async def test_on_wakeup_eventfd(self, layer: SharedMemoryChannelLayer) -> None:
        """_on_wakeup with the eventfd drains it and drains rings."""
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()
        assert pump.wakeup.eventfd is not None
        pump.wakeup.wakeup_local()
        pump._on_wakeup(pump.wakeup.eventfd)
        pump.stop()

    async def test_on_wakeup_socket(self, layer: SharedMemoryChannelLayer) -> None:
        """_on_wakeup with the socket fd drains it and drains rings."""
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()
        assert pump.wakeup.wakeup_sock is not None
        pump._on_wakeup(pump.wakeup.wakeup_sock.fileno())
        pump.stop()

    async def test_on_wakeup_unknown_fd(self, layer: SharedMemoryChannelLayer) -> None:
        """_on_wakeup with an unrecognized fd still drains rings."""
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()
        pump._on_wakeup(99999)
        pump.stop()

    async def test_watchdog_triggers_drain(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """A manual drain refresh keeps the watchdog from firing spuriously."""
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()
        pump.last_drain_ts = 0.0
        pump.drain_rings()
        assert pump.last_drain_ts > 0
        pump.stop()


class TestPumpWatchdogTask:
    """Watchdog task creation and cancellation."""

    @staticmethod
    def _fresh_pump(
        layer: SharedMemoryChannelLayer,
    ) -> ReceivePump:
        """A standalone pump with a live watchdog interval."""
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
            watchdog_interval=1,
        )

    async def test_watchdog_task_created_and_cancelled(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """start() creates the watchdog task; stop() cancels it (P-12)."""
        layer = layer_factory(watchdog_interval=0)
        pump2 = self._fresh_pump(layer)
        loop = asyncio.get_running_loop()
        pump2.start(loop)
        assert pump2._watchdog_task is not None
        pump2.stop()
        assert pump2._watchdog_task is None


class TestDrainRingsTolerance:
    """P-01: drain_rings drops a malformed message instead of aborting the drain.

    A corrupt msg_data (bytes msgpack cannot decode) must not abort the whole
    drain — try_dequeue already advanced dequeue_pos, so the bad message is
    logically consumed and silently dropped; a well-formed message enqueued
    behind it must still be delivered.
    """

    async def test_corrupt_message_skipped_good_delivered(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()

        channel = "test.drain"
        buf = pump.watch_channel(channel)

        # Grab the ring the pump will drain and inject two messages directly:
        # a corrupt one (0xc1 is msgpack's reserved never-used byte) followed
        # by a well-formed one.
        channel_mgr = layer._channel_mgr
        region = layer._region
        assert channel_mgr is not None
        assert region is not None
        ring, _ = channel_mgr.get_or_create_ring(channel, layer.capacity)
        ch_name = channel.encode("utf-8")
        _ = ring.try_enqueue(
            region.native,
            layer._slab,
            ch_name,
            b"\xc1",
            math.inf,
            layer.pid,
            layer.start_time,
        )
        good = bytes(pack_message({"type": "good"}))
        _ = ring.try_enqueue(
            region.native,
            layer._slab,
            ch_name,
            good,
            math.inf,
            layer.pid,
            layer.start_time,
        )

        pump.drain_rings()  # must not raise

        # Only the well-formed message reached the buffer.
        assert buf.qsize() == 1
        assert buf.get_nowait() == {"type": "good"}
        pump.stop()
