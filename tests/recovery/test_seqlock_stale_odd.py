"""Seqlock stale-odd recovery: a crashed writer leaves a slot mid-version.

When an index slot is left with an odd version (writer crashed mid-write),
lookup must skip it, and a subsequent create must repair it in place — the
version flips back to even and the slot becomes findable again.
"""

from __future__ import annotations

import asyncio
from typing import TypedDict

import pytest

from channels_shm import SharedMemoryChannelLayer
from tests.layout_helpers import (
    CH_SLOT_VERSION_OFF,
    first_channel_slot,
    region_native,
)

pytestmark = pytest.mark.slow


class _Config(TypedDict):
    shm_size: int
    max_channels: int
    max_groups: int
    max_processes: int
    max_members_per_group: int


CONFIG: _Config = {
    "shm_size": 16 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
}


def test_seqlock_stale_odd_repair(
    recv_prefix: str,
    recv_cleanup: None,  # noqa: ARG001
) -> None:
    """Construct an odd-version dead slot; verify lookup skips + create repairs."""
    layer = SharedMemoryChannelLayer(prefix=recv_prefix, **CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    ch = loop.run_until_complete(layer.new_channel("bench."))
    loop.run_until_complete(layer.send(ch, {"type": "x"}))

    native = region_native(layer)
    target_slot = first_channel_slot(layer)
    assert target_slot is not None, "no channel slot found to corrupt"
    native.store_u64(target_slot + CH_SLOT_VERSION_OFF, 1)

    # Lookup must skip the odd slot, so the send below takes the create path,
    # which repairs the slot in place.
    loop.run_until_complete(layer.send(ch, {"type": "after_repair"}))

    version_after = native.load_u64(target_slot + CH_SLOT_VERSION_OFF)
    assert version_after % 2 == 0, (
        f"stale-odd slot was not repaired: version={version_after} (odd)"
    )

    # And the channel must be usable end to end.
    msg = loop.run_until_complete(asyncio.wait_for(layer.receive(ch), timeout=5.0))
    assert msg["type"] in ("x", "after_repair")

    loop.run_until_complete(layer.close())
