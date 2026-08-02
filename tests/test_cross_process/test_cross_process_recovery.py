"""Cross-process crash recovery (Layer A, slow marker).

Verify that when a child process dies abruptly, its channel slot is reclaimed
by the layer's pid_dead recovery path.
"""

from __future__ import annotations

import os
import signal
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import multiprocessing as mp

pytestmark = pytest.mark.slow


def _crash_victim(prefix: str, channel: str) -> None:
    """Create a layer, register on `channel`, then exit via SIGKILL (no cleanup)."""
    from channels_shm import SharedMemoryChannelLayer

    layer = SharedMemoryChannelLayer(
        prefix=prefix,
        capacity=100,
        shm_size=16 * 1024 * 1024,
        max_channels=100,
        max_groups=50,
        max_processes=16,
        max_members_per_group=64,
        watchdog_interval=None,
    )
    # Force registration by creating + sending to a channel.
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(layer.send(channel, {"type": "here"}))
    # Signal readiness to parent, then hang until killed.
    pid = os.getpid()
    with open(f"/dev/shm/{prefix}_victim_pid", "w") as f:
        f.write(str(pid))
    while True:
        time.sleep(0.5)


def test_dead_process_slot_reclaimable(
    xproc_prefix: str,
    xproc_ctx: mp.context.BaseContext,
    xproc_cleanup: None,  # noqa: ARG001
) -> None:
    """A new process can reuse the shm region after a victim is killed.

    This is a smoke test for the recovery path: we don't assert on internal
    registry internals (white-box), only that a fresh layer can be created and
    used after another process died without cleanup.
    """
    ctx = xproc_ctx
    channel = "xproc.victim"

    victim = ctx.Process(target=_crash_victim, args=(xproc_prefix, channel))
    victim.start()

    # Wait for the victim to register its pid.
    pid_file = f"/dev/shm/{xproc_prefix}_victim_pid"
    deadline = time.time() + 10
    while not os.path.exists(pid_file) and time.time() < deadline:
        time.sleep(0.1)
    assert os.path.exists(pid_file), "victim did not signal readiness"

    with open(pid_file) as f:
        victim_pid = int(f.read())
    os.kill(victim_pid, signal.SIGKILL)
    victim.join(timeout=5)
    assert not victim.is_alive()

    # A new process should now be able to construct a layer on the same prefix.
    from channels_shm import SharedMemoryChannelLayer

    survivor = SharedMemoryChannelLayer(
        prefix=xproc_prefix,
        capacity=100,
        shm_size=16 * 1024 * 1024,
        max_channels=100,
        max_groups=50,
        max_processes=16,
        max_members_per_group=64,
        watchdog_interval=None,
    )
    import asyncio

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(survivor.send("xproc.survivor", {"type": "alive"}))
    msg = loop.run_until_complete(
        asyncio.wait_for(survivor.receive("xproc.survivor"), timeout=5.0)
    )
    loop.run_until_complete(survivor.close())
    assert msg == {"type": "alive"}
