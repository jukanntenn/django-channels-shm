"""Watchdog detects a stuck pump and triggers a drain.

The watchdog is an operational backstop for the narrow case where add_reader
stops delivering wakeups while the loop stays healthy. drain_rings is
monkeypatched to a no-op (simulating a stalled pump), a message is sent that
would normally be drained on wakeup, and the watchdog's next tick must detect
the stale last_drain_ts and drain the ring itself — and the observability
counter for a stuck pump must fire.
"""

from __future__ import annotations

import asyncio
import time
from typing import TypedDict

import pytest

from channels_shm import SharedMemoryChannelLayer
from tests.recovery.conftest import assert_counter_ge, read_metrics

pytestmark = pytest.mark.slow


class _Config(TypedDict):
    shm_size: int
    max_channels: int
    max_groups: int
    max_processes: int
    max_members_per_group: int
    watchdog_interval: int


CONFIG: _Config = {
    "shm_size": 16 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
    "watchdog_interval": 1,  # short for testing
}


def test_watchdog_detects_pump_stall(
    recv_prefix: str,
    recv_cleanup: None,  # noqa: ARG001
) -> None:
    """Watchdog fires when last_drain_ts goes stale, triggering a drain."""
    layer = SharedMemoryChannelLayer(prefix=recv_prefix, **CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ch = loop.run_until_complete(layer.new_channel("bench."))

    async def _setup_and_wait() -> None:
        _ = layer._ensure_loop()
        pump = layer._pump
        assert pump is not None

        # Simulate a stall. The watchdog's FIRST tick only re-arms (it would
        # overwrite last_drain_ts), so mark it already armed. The send's own
        # wakeup drain also refreshes last_drain_ts asynchronously, so give the
        # loop a beat to run that callback before we corrupt the timestamp;
        # otherwise the next tick would see a fresh value and never stall.
        await layer.send(ch, {"type": "watchdog_test"})
        await asyncio.sleep(0.05)
        pump._watchdog_armed = True
        pump.last_drain_ts = time.monotonic() - 100.0

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if pump.last_drain_ts > time.monotonic() - 2.0:
                return  # watchdog fired and refreshed the timestamp
            await asyncio.sleep(0.1)
        msg = "watchdog did not fire within 5s"
        raise AssertionError(msg)

    loop.run_until_complete(_setup_and_wait())

    # The watchdog-triggered drain should have delivered the buffered message.
    msg = loop.run_until_complete(asyncio.wait_for(layer.receive(ch), timeout=5.0))
    assert msg["type"] == "watchdog_test", f"unexpected message: {msg!r}"

    loop.run_until_complete(layer.close())

    # close() flushed metrics; the stuck-pump counter must have fired.
    metrics = read_metrics(recv_prefix)
    assert_counter_ge(metrics, "watchdog_pump_stuck_total", expected=1)
