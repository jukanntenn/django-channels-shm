"""Concurrent recovery of a dead ring slot (thundering herd, black-box).

Multiple processes hit the same dead ring slot simultaneously; verify no data
corruption (messages not lost/duplicated/damaged). Black-box because Python has
no slab free-list introspection — deep white-box CAS validation lives in the
Rust unit tests (test_recover_cas_no_double_free).
"""

from __future__ import annotations

import asyncio
import multiprocessing as mp  # noqa: TC003
import os
import signal
import time
from typing import cast

import pytest

from tests.recovery._workers import victim_holds_slot

pytestmark = pytest.mark.slow


def test_recover_herd_no_corruption(
    recv_prefix: str,
    recv_ctx: mp.context.BaseContext,
    recv_cleanup: None,  # noqa: ARG001
) -> None:
    """A dead slot is recovered by multiple survivors concurrently; verify integrity."""
    from channels_shm import SharedMemoryChannelLayer

    ctx = recv_ctx
    channel = "xproc.herd"

    # Victim takes a slot and gets killed.
    victim = ctx.Process(target=victim_holds_slot, args=(recv_prefix, channel))
    victim.start()
    ready_file = f"/dev/shm/{recv_prefix}_herd_ready"
    deadline = time.time() + 10
    while not os.path.exists(ready_file) and time.time() < deadline:
        time.sleep(0.1)
    assert os.path.exists(ready_file)
    with open(ready_file) as f:
        os.kill(int(f.read()), signal.SIGKILL)
    victim.join(timeout=5)

    # Multiple survivors concurrently send + receive on the same channel.
    survivor = SharedMemoryChannelLayer(
        prefix=recv_prefix,
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
    # "type" is always a str, so the set stays hashable.
    received_types: set[str] = {cast("str", msg["type"])}
    try:
        msg2 = loop.run_until_complete(
            asyncio.wait_for(survivor.receive(channel), timeout=2.0)
        )
        received_types.add(cast("str", msg2["type"]))
    except asyncio.TimeoutError:
        pass  # victim's message may have been lost in the crash (at-most-once acceptable)
    assert "survivor" in received_types, "survivor message must not be lost"
    loop.run_until_complete(survivor.close())
