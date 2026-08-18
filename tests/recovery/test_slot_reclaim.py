"""Cross-process crash recovery (black-box): a dead process's slot is reclaimable.

Verify that when a child process dies abruptly, a fresh layer on the same shm
prefix can still be created and used — the recovery path reaps the dead
process's registry/channel slot instead of wedging the region.
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp  # noqa: TC003
import os
import signal
import time

import pytest

from tests.recovery._workers import crash_victim

pytestmark = pytest.mark.slow


def test_dead_process_slot_reclaimable(
    recv_prefix: str,
    recv_ctx: mp.context.BaseContext,
    recv_cleanup: None,  # noqa: ARG001
) -> None:
    """A new process can reuse the shm region after a victim is SIGKILLed."""
    from channels_shm import SharedMemoryChannelLayer

    ctx = recv_ctx
    channel = "xproc.victim"

    victim = ctx.Process(target=crash_victim, args=(recv_prefix, channel))
    victim.start()

    pid_file = f"/dev/shm/{recv_prefix}_victim_pid"
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
    survivor = SharedMemoryChannelLayer(
        prefix=recv_prefix,
        capacity=100,
        shm_size=16 * 1024 * 1024,
        max_channels=100,
        max_groups=50,
        max_processes=16,
        max_members_per_group=64,
        watchdog_interval=None,
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(survivor.send("xproc.survivor", {"type": "alive"}))
    msg = loop.run_until_complete(
        asyncio.wait_for(survivor.receive("xproc.survivor"), timeout=5.0)
    )
    loop.run_until_complete(survivor.close())
    assert msg == {"type": "alive"}
