"""Process-local receive pump for message delivery.

The pump is the single execution point per process that touches the wakeup fds
and shared memory ring buffers. It drains messages from watched channels and
delivers them to per-channel BoundedQueues.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from channels_shm.channel.manager import ChannelManager, non_local_name
from channels_shm.serializer import Message, unpack_message

if TYPE_CHECKING:
    from channels_shm._native import ShmRegion, SlabAllocator
    from channels_shm._obs.metrics import MetricsRegistry
    from channels_shm.shm.wakeup import WakeupManager

logger = logging.getLogger(__name__)


class _BoundedQueue(asyncio.Queue[Message]):
    """An asyncio.Queue that drops the oldest item when full.

    Prevents unbounded memory growth from stale process-specific channel buffers.
    Aligned with channels_redis core.py:62-72. Renamed with a leading
    underscore (P-13): this is an internal implementation detail of ReceivePump,
    not part of the module's public surface.
    """

    _metrics: Any  # MetricsRegistry or None; only set under __debug__

    def __init__(self, maxsize: int = 0, *, metrics: Any = None) -> None:
        super().__init__(maxsize)
        self._metrics = metrics

    @override
    def put_nowait(self, item: Message) -> None:
        if self.full():
            _ = self.get_nowait()  # Drop oldest (at-most-once semantics)
            # Observe the drop so silent message loss is diagnosable in
            # production (P-05); at-most-once permits the loss, the counter
            # makes it visible.
            if __debug__ and self._metrics is not None:
                self._metrics.counter("buffer_drop_oldest_total").inc()
        super().put_nowait(item)


class ReceivePump:
    """Process-local receive pump.

    One pump per process, bound to a single event loop. Manages:
    - Watched channels set
    - Per-channel BoundedQueues
    - drain_rings() for message delivery
    - Wakeup fd management
    """

    region: ShmRegion
    slab: SlabAllocator
    channel_mgr: ChannelManager
    wakeup: WakeupManager
    capacity: int
    expiry: int
    pid: int
    start_time: int
    _watched_channels: set[str]
    _buffers: defaultdict[str, _BoundedQueue]
    last_drain_ts: float
    _loop: asyncio.AbstractEventLoop | None
    _watchdog_task: asyncio.Task[None] | None
    _watchdog_interval: int | None
    _watchdog_armed: bool  # False until first watchdog tick (P-11)
    _metrics: MetricsRegistry | None  # only non-None under __debug__
    _log: Any  # structlog BoundLogger or None; only set under __debug__

    def __init__(
        self,
        region: ShmRegion,
        slab: SlabAllocator,
        channel_mgr: ChannelManager,
        wakeup: WakeupManager,
        capacity: int,
        expiry: int,
        pid: int,
        start_time: int,
        watchdog_interval: int | None = 30,
        *,
        metrics: MetricsRegistry | None = None,
        log: Any = None,
    ) -> None:
        self.region = region
        self.slab = slab
        self.channel_mgr = channel_mgr
        self.wakeup = wakeup
        self.capacity = capacity
        self.expiry = expiry
        self.pid = pid
        self.start_time = start_time

        self._watched_channels = set()
        self._metrics = metrics
        self._buffers = defaultdict(
            lambda: _BoundedQueue(maxsize=capacity, metrics=self._metrics)
        )
        # Monotonic clock so NTP slew cannot make `elapsed` go negative and
        # disable the watchdog (P-09). NOTE: cross-process message expiry
        # (ring SLOT_EXPIRY_TS) MUST stay on wall-clock time.time() because
        # different processes share different monotonic origins — do NOT unify.
        self.last_drain_ts = time.monotonic()
        self._loop = None
        self._watchdog_task = None
        self._watchdog_interval = watchdog_interval
        self._watchdog_armed = False
        self._log = log

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Start the pump: register wakeup fds and optional watchdog.

        Discards any prior watchdog task that has not yet finished cancelling
        (P-12): since stop() is synchronous and cannot await the task, a loop
        switch (single-thread async_to_sync) may briefly leave the old task
        running; we drop the reference and let the loop retire it, and create a
        fresh task on the new loop.
        """
        self._loop = loop
        self.wakeup.register_with_loop(loop, self._on_wakeup)

        if self._watchdog_interval is not None and self._watchdog_interval > 0:
            # Drop any not-yet-done prior task rather than leak a reference.
            if self._watchdog_task is not None and not self._watchdog_task.done():
                _ = self._watchdog_task.cancel()
            self._watchdog_task = loop.create_task(self._watchdog_loop())

    def stop(self) -> None:
        """Stop the pump: unregister fds and cancel watchdog."""
        if self._loop is not None:
            self.wakeup.unregister_from_loop(self._loop)
        if self._watchdog_task is not None:
            _ = self._watchdog_task.cancel()
            self._watchdog_task = None

    def watch_channel(self, channel: str) -> _BoundedQueue:
        """Register a channel for watching and return its buffer.

        The buffer is created BEFORE drain_rings so that messages for this
        channel can be delivered during the drain.
        """
        is_new = channel not in self._watched_channels
        self._watched_channels.add(channel)
        # Create buffer FIRST so drain_rings can deliver to it
        buf = self._buffers[channel]

        if is_new:
            # First registration: immediate drain to pick up accumulated messages
            self.drain_rings()

        return buf

    def drain_rings(self) -> None:
        """C-drain: drain all watched channels, deliver to buffers.

        This is the ONLY drain entry point (§0.3).
        Called from: first registration (step f), pump _on_wakeup, watchdog.
        """
        if __debug__ and self._metrics is not None:
            self._metrics.counter("drain_rings_total").inc(source="wakeup")

        channels = list(self._watched_channels)
        random.shuffle(channels)  # Approximate global ordering (§6.7)

        now = time.time()

        for channel in channels:
            ring_key = non_local_name(channel)
            ring = self.channel_mgr.get_ring(ring_key)
            if ring is None:
                continue

            while True:
                result = ring.try_dequeue(
                    self.region,
                    self.slab,
                    now,
                    self.pid,
                    self.start_time,
                )
                if result is None:
                    break  # Ring empty or no more ready messages

                ch_name_bytes, msg_data = result
                # P-01: a malformed/corrupt message must NOT abort the whole
                # drain (which would starve every later watched channel).
                # try_dequeue already advanced dequeue_pos, so the message is
                # already logically consumed; decode/unpack/deliver failures
                # are counted and the bad message is dropped (at-most-once).
                try:
                    full_name = ch_name_bytes.decode("utf-8")
                    # Use __getitem__ (not .get) so defaultdict creates the
                    # buffer if it doesn't exist yet (e.g., during group_send
                    # to multiple channels).
                    buf = self._buffers[full_name]
                    msg = unpack_message(msg_data)
                except Exception:
                    if __debug__ and self._metrics is not None:
                        self._metrics.counter("drain_unpack_failed_total").inc()
                        if self._log is not None:
                            self._log.warning("drain unpack failed, message dropped")
                    continue
                buf.put_nowait(msg)

        self.last_drain_ts = time.monotonic()

    def clear(self) -> None:
        """Clear all watched channels and buffers (for flush)."""
        self._watched_channels.clear()
        self._buffers.clear()

    def _on_wakeup(self, fd: int) -> None:
        """Wakeup callback registered with the event loop."""
        if fd == self.wakeup.eventfd:
            self.wakeup.drain_eventfd()
        elif fd == self.wakeup.socket_fd:
            # Drain all pending wakeup bytes (coalescing, §6.5.3 M3).
            # fd is compared against the cached `socket_fd` rather than calling
            # fileno() per callback (P-02).
            self.wakeup.drain_socket()

        self.drain_rings()

    async def _watchdog_loop(self) -> None:
        """Periodic watchdog: detect pump stalls and trigger drain.

        CAPACITY NOTE (P-06): this coroutine runs on the SAME event loop as the
        pump, so it cannot rescue a dead/hung loop — it only covers the narrow
        case where add_reader stops delivering but the loop is still healthy
        (spec §4.2.4: an operational backstop, not a correctness one).
        """
        if self._watchdog_interval is None:  # pragma: no cover - guarded by start()
            raise RuntimeError("watchdog loop started without an interval")
        while True:
            await asyncio.sleep(self._watchdog_interval)
            # P-11: skip the first tick and re-arm `last_drain_ts`, so a pump
            # that is created long before its first receive doesn't look
            # "stuck" (which would fire a spurious drain + noisy metric).
            if not self._watchdog_armed:
                self._watchdog_armed = True
                self.last_drain_ts = time.monotonic()
                continue
            elapsed = time.monotonic() - self.last_drain_ts
            if elapsed > 2 * self._watchdog_interval:
                if __debug__ and self._metrics is not None:
                    self._metrics.counter("watchdog_pump_stuck_total").inc()
                    if self._log is not None:
                        self._log.warning(
                            "watchdog detected pump stuck",
                            elapsed=elapsed,
                            threshold=2 * self._watchdog_interval,
                        )
                logger.warning(
                    "Watchdog: pump stall detected (%.1fs since last drain), triggering drain",
                    elapsed,
                )
                self.drain_rings()

    def __enter__(self) -> ReceivePump:
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop()
