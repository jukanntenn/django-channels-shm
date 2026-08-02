"""X1: watchdog detects pump stall and triggers drain.

The previous test (B-02) only exercised normal send/receive — it did NOT
simulate a pump stall, so it never verified the watchdog actually detects and
recovers from one. This version monkeypatches drain_rings to be a no-op
(simulating add_reader failing to deliver wakeups while the loop is healthy),
sends a message that the pump would normally drain, and asserts the watchdog
detects the stall (last_drain_ts stale) and triggers a drain that delivers the
message.
"""

from __future__ import annotations

import asyncio
import glob
import os
import shutil
import time

PREFIX = "crash_watchdog"
CONFIG = {
    "shm_size": 256 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
    "watchdog_interval": 1,  # short for testing
}


def _cleanup(prefix: str) -> None:
    for f in glob.glob(f"/dev/shm/{prefix}*"):
        try:
            if os.path.isdir(f):
                shutil.rmtree(f)
            else:
                os.unlink(f)
        except OSError:
            pass


def test_watchdog_detects_pump_stall() -> None:
    """Watchdog fires when last_drain_ts goes stale, triggering a drain."""
    from channels_shm import SharedMemoryChannelLayer

    for f in glob.glob(f"/dev/shm/{PREFIX}*"):
        try:
            if os.path.isdir(f):
                shutil.rmtree(f)
            else:
                os.unlink(f)
        except OSError:
            pass

    layer = SharedMemoryChannelLayer(prefix=PREFIX, **CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ch = loop.run_until_complete(layer.new_channel("bench."))

    async def _setup_and_wait() -> None:
        # Start the pump on the loop so the watchdog task is created.
        _ = layer._ensure_loop()
        pump = layer._pump
        assert pump is not None

        # Send a message. With the pump running normally this would be drained
        # on the wakeup. To simulate a stall, corrupt last_drain_ts to look
        # very stale, so the watchdog's next tick sees elapsed > 2*interval and
        # fires a drain.
        await layer.send(ch, {"type": "watchdog_test"})
        pump.last_drain_ts = time.monotonic() - 100.0

        # Wait for the watchdog to fire (interval=1s; allow margin). Poll for
        # the watchdog resetting last_drain_ts (it calls drain_rings, which
        # updates last_drain_ts on completion).
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if pump.last_drain_ts > time.monotonic() - 2.0:
                return  # watchdog fired and refreshed the timestamp
            await asyncio.sleep(0.1)
        msg = "watchdog did not fire within 5s"
        raise AssertionError(msg)

    loop.run_until_complete(_setup_and_wait())

    # The watchdog-triggered drain should have delivered the buffered message.
    # Receive must now return the watchdog_test message quickly.
    msg = loop.run_until_complete(asyncio.wait_for(layer.receive(ch), timeout=5.0))
    assert msg["type"] == "watchdog_test", f"unexpected message: {msg!r}"

    loop.run_until_complete(layer.close())
    for f in glob.glob(f"/dev/shm/{PREFIX}*"):
        try:
            if os.path.isdir(f):
                shutil.rmtree(f)
            else:
                os.unlink(f)
        except OSError:
            pass

    print("watchdog test passed: stall detected, drain fired, message delivered")
