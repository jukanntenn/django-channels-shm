"""Recovery when a producer sets an enqueue owner and then crashes.

SIGKILL timing is uncontrollable (the owner-set -> write-data window is
nanoseconds), so the test constructs the post-crash state directly: a ring slot
whose owner_pid points at a confirmed-dead PID with the seq left behind. The
next dequeue on that slot must hit the recovery path (consumer sees seq < pos+1
with a dead owner), recycle the slot, and leave the ring usable end to end.
Single-threaded, so there is no consumer-thread race.
"""

from __future__ import annotations

import asyncio
import time
from typing import TypedDict

import pytest

from channels_shm import SharedMemoryChannelLayer
from tests.layout_helpers import (
    CH_SLOT_RING_OFFSET_OFF,
    RING_CAPACITY_OFF,
    RING_SLOT_OWNER_PID_OFF,
    RING_SLOT_OWNER_START_TIME_OFF,
    RING_SLOT_OWNER_TICKET_OFF,
    RING_SLOT_SEQ_OFF,
    first_channel_slot,
    region_native,
    ring_slot_off,
)

pytestmark = pytest.mark.slow


class _Config(TypedDict):
    shm_size: int
    max_channels: int
    max_groups: int
    max_processes: int
    max_members_per_group: int
    capacity: int


CONFIG: _Config = {
    "shm_size": 16 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
    "capacity": 4,
}


def test_owner_crash_during_enqueue_triggers_recover(
    recv_prefix: str,
    recv_cleanup: None,  # noqa: ARG001
) -> None:
    """Inject a dead-owner slot, dequeue, and assert the slot is recycled."""
    layer = SharedMemoryChannelLayer(prefix=recv_prefix, **CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    channel = f"{recv_prefix}.crash_ch"
    loop.run_until_complete(layer.send(channel, {"type": "init"}))

    native = region_native(layer)
    target_slot = first_channel_slot(layer)
    assert target_slot is not None, "no channel slot found after send"

    ring_off = native.load_u64(target_slot + CH_SLOT_RING_OFFSET_OFF)
    assert ring_off != 0, "ring offset must be set"
    ring_slot0 = ring_slot_off(ring_off)
    # cap = ring capacity; dequeue_pos starts at 0, so a seq of 0 with a dead
    # owner reads as "published then owner died before the reader advanced".
    cap = native.read_u32(ring_off + RING_CAPACITY_OFF)

    dead_pid = 999_999  # does not exist -> pid_dead returns true
    native.write_u32(ring_slot0 + RING_SLOT_OWNER_PID_OFF, dead_pid)
    native.store_u64(ring_slot0 + RING_SLOT_OWNER_START_TIME_OFF, 0)
    native.store_u64(ring_slot0 + RING_SLOT_OWNER_TICKET_OFF, 0)
    native.store_u64(ring_slot0 + RING_SLOT_SEQ_OFF, 0)

    async def _drain_and_verify() -> None:
        _ = layer._ensure_loop()
        pump = layer._pump
        assert pump is not None
        _ = pump.watch_channel(channel)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if native.read_u32(ring_slot0 + RING_SLOT_OWNER_PID_OFF) == 0:
                return
            await asyncio.sleep(0.05)
        msg = "recovery did not clear the dead owner within 5s"
        raise AssertionError(msg)

    loop.run_until_complete(_drain_and_verify())

    final_owner_pid = native.read_u32(ring_slot0 + RING_SLOT_OWNER_PID_OFF)
    assert final_owner_pid == 0, (
        f"recovery did not clear the dead owner: owner_pid={final_owner_pid} "
        f"(expected 0 after recover_slot)"
    )
    final_seq = native.load_u64(ring_slot0 + RING_SLOT_SEQ_OFF)
    assert final_seq == cap, (
        f"recovery did not recycle slot seq: seq={final_seq} "
        f"(expected {cap} = ticket(0) + cap)"
    )

    # The ring must remain usable end to end after the recovery.
    loop.run_until_complete(layer.send(channel, {"type": "post_recover"}))
    msg = loop.run_until_complete(asyncio.wait_for(layer.receive(channel), timeout=5.0))
    assert msg["type"] == "post_recover", f"unexpected: {msg!r}"

    loop.run_until_complete(layer.close())
