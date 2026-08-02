"""X1: seqlock stale odd version recovery (real injection + recovery assert).

Verify that when an index slot is left with odd version (writer crashed
mid-write), lookup skips it and a subsequent create repairs it in-place.
The previous test injected the odd version but only checked basic receive;
this version additionally asserts the slot is REPAIRED (version back to even,
lookup finds the channel). (B-02 / O-02: was a near-empty-shell.)
"""

from __future__ import annotations

import asyncio
import glob
import os
import shutil

PREFIX = "crash_seqlock"
CONFIG = {
    "shm_size": 256 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
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


def test_seqlock_stale_odd_repair() -> None:
    """Construct an odd-version dead slot; verify lookup skips + create repairs."""
    from channels_shm import SharedMemoryChannelLayer

    _cleanup(PREFIX)
    layer = SharedMemoryChannelLayer(prefix=PREFIX, **CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    ch = loop.run_until_complete(layer.new_channel("bench."))
    loop.run_until_complete(layer.send(ch, {"type": "x"}))

    # White-box: find the slot and flip its version to odd (crash mid-write).
    region = layer._region
    assert region is not None
    native = region.native
    ch_off = native.load_u64(56)  # HDR_CHANNEL_INDEX_OFF
    ch_slot_size = 168  # CH_SLOT_SIZE
    ch_slot_version_off = 160  # CH_SLOT_VERSION (name field is [u8;128])
    target_slot = None
    for i in range(CONFIG["max_channels"]):
        slot_off = ch_off + i * ch_slot_size
        if native.read_u16(slot_off + 8) > 0:  # CH_SLOT_NAME_LEN
            target_slot = slot_off
            break
    assert target_slot is not None, "no channel slot found to corrupt"
    # Corrupt: set version to odd (1).
    native.store_u64(target_slot + ch_slot_version_off, 1)

    # lookup must SKIP this odd slot → send triggers create → create repairs.
    loop.run_until_complete(layer.send(ch, {"type": "after_repair"}))

    # RECOVERY ASSERTION (new): the slot must now have an even version again
    # (channel_index_create's lazy-repair resets odd → clean even baseline).
    version_after = native.load_u64(target_slot + ch_slot_version_off)
    assert version_after % 2 == 0, (
        f"seqlock stale-odd was not repaired: version={version_after} (odd)"
    )

    # And the channel must be usable end-to-end.
    msg = loop.run_until_complete(asyncio.wait_for(layer.receive(ch), timeout=5.0))
    assert msg["type"] in ("x", "after_repair")

    loop.run_until_complete(layer.close())
    _cleanup(PREFIX)
    print(f"stale-odd repair test passed (version {version_after} even)")
