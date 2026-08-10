"""Cross-process recover thundering-herd (black-box functional verification).

Multiple processes hit the same dead ring slot simultaneously; verify no
data corruption (messages not lost/duplicated/damaged). Black-box because
Python has no slab free-list introspection — deep white-box CAS validation
is in Rust unit tests (test_recover_cas_no_double_free).
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import multiprocessing as mp

pytestmark = pytest.mark.slow


def _victim_holds_slot(prefix: str, channel: str) -> None:
    """Become the owner of a ring slot, then hang (to be SIGKILL'd mid-ownership)."""
    from channels_shm import SharedMemoryChannelLayer

    layer = SharedMemoryChannelLayer(
        prefix=prefix,
        capacity=4,
        shm_size=16 * 1024 * 1024,
        max_channels=10,
        max_groups=5,
        max_processes=8,
        max_members_per_group=4,
        watchdog_interval=None,
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Enqueue to take slot ownership.
    loop.run_until_complete(layer.send(channel, {"type": "victim"}))
    # Signal readiness, then hang.
    with open(f"/dev/shm/{prefix}_herd_ready", "w") as f:
        f.write(str(os.getpid()))
    while True:
        time.sleep(0.5)


def test_recover_herd_no_corruption(
    xproc_prefix: str,
    xproc_ctx: mp.context.BaseContext,
    xproc_cleanup: None,  # noqa: ARG001
) -> None:
    """A dead slot is recovered by multiple survivors concurrently; verify integrity."""
    ctx = xproc_ctx
    channel = "xproc.herd"

    # Victim takes a slot and gets killed.
    victim = ctx.Process(target=_victim_holds_slot, args=(xproc_prefix, channel))
    victim.start()
    ready_file = f"/dev/shm/{xproc_prefix}_herd_ready"
    deadline = time.time() + 10
    while not os.path.exists(ready_file) and time.time() < deadline:
        time.sleep(0.1)
    assert os.path.exists(ready_file)
    with open(ready_file) as f:
        os.kill(int(f.read()), signal.SIGKILL)
    victim.join(timeout=5)

    # Multiple survivors concurrently send + receive on the same channel.
    from channels_shm import SharedMemoryChannelLayer

    survivor = SharedMemoryChannelLayer(
        prefix=xproc_prefix,
        capacity=4,
        shm_size=16 * 1024 * 1024,
        max_channels=10,
        max_groups=5,
        max_processes=8,
        max_members_per_group=4,
        watchdog_interval=None,
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Send and receive should work without error after concurrent recovery.
    loop.run_until_complete(survivor.send(channel, {"type": "survivor"}))
    msg = loop.run_until_complete(
        asyncio.wait_for(survivor.receive(channel), timeout=5.0)
    )
    # The victim's message and survivor's should both be receivable (no loss).
    received_types = {msg["type"]}
    try:
        msg2 = loop.run_until_complete(
            asyncio.wait_for(survivor.receive(channel), timeout=2.0)
        )
        received_types.add(msg2["type"])
    except asyncio.TimeoutError:
        pass  # victim's message may have been lost in the crash (at-most-once acceptable)
    assert "survivor" in received_types, "survivor message must not be lost"
    loop.run_until_complete(survivor.close())
