"""X1 section15.1 E2: enqueue owner set then crash -> recover fires.

VERIFIES §15.1 E2 recovery, replacing the previous empty shell (B-02/O-02:
the old test defined child_fill_and_hang but never called it, and used
`assert ... or True` to make the assertion always pass).

Strategy (F-2 decision): white-box injection + behavioral recovery assertion.
True SIGKILL timing is uncontrollable (the owner-set → write-data window is
nanoseconds), so we directly construct the RESULT state of an E2 crash — a
ring slot with owner_pid set to a confirmed-dead PID, seq left "behind" — and
verify the next DEQUEUE on that slot triggers recover_slot (the primary E2
recovery path in ring.rs: a consumer finds seq < pos+1 with a dead owner).
We then assert the slot was recovered (seq advanced, owner cleared) and the
ring remains usable. Single-threaded: no consumer-thread race risk.
"""

from __future__ import annotations

import asyncio
import glob
import os
import shutil
import time

PREFIX = "crash_e2"
CONFIG = {
    "shm_size": 256 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
    "capacity": 4,
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


def test_e2_enqueue_owner_crash_triggers_recover() -> None:
    """Inject an E2 dead-owner slot, then dequeue and assert recover fired."""
    from channels_shm import SharedMemoryChannelLayer

    _cleanup(PREFIX)
    layer = SharedMemoryChannelLayer(prefix=PREFIX, **CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    channel = f"{PREFIX}.crash_test_ch"
    # Send one message to create the ring + index slot (occupies slot 0).
    loop.run_until_complete(layer.send(channel, {"type": "init"}))

    # White-box layout (name field is [u8;128]): CH_SLOT_NAME_LEN=8,
    # CH_SLOT_RING_OFFSET=144, CH_SLOT_SIZE=168. Update if layout.rs changes.
    region = layer._region
    assert region is not None
    native = region.native
    ch_off = native.load_u64(56)  # HDR_CHANNEL_INDEX_OFF
    ch_slot_size = 168
    ch_ring_offset_off = 144
    target_slot = None
    for i in range(CONFIG["max_channels"]):
        slot_off = ch_off + i * ch_slot_size
        if native.read_u16(slot_off + 8) > 0:  # CH_SLOT_NAME_LEN
            target_slot = slot_off
            break
    assert target_slot is not None, "no channel slot found after send"

    ring_off = native.load_u64(target_slot + ch_ring_offset_off)
    assert ring_off != 0, "ring offset must be set"
    # RING_HEADER_SIZE = 40; ring slot 0.
    ring_slot0 = ring_off + 40
    # Vyukov ring slot field offsets (layout.rs, not in public surface):
    # SEQ=0, OWNER_PID=8, OWNER_TICKET=16, OWNER_START_TIME=24.
    slot_seq_off = 0
    slot_owner_pid_off = 8
    slot_owner_ticket_off = 16
    slot_owner_starttime_off = 24

    # White-box E2 injection on slot 0: simulate a producer that set the
    # owner mid-enqueue then crashed. Slot 0's seq is left "behind" relative
    # to dequeue_pos (seq < pos+1), with a confirmed-dead owner. The next
    # dequeue will hit the recover path (ring.rs: seq < pos+1 && owner != 0
    # && pid_dead → recover_slot).
    dead_pid = 999_999  # does not exist → pid_dead returns true
    cap = native.read_u32(ring_off + 16)  # RING_CAPACITY
    native.write_u32(ring_slot0 + slot_owner_pid_off, dead_pid)
    native.store_u64(ring_slot0 + slot_owner_starttime_off, 0)
    native.store_u64(ring_slot0 + slot_owner_ticket_off, 0)
    # Force seq behind: dequeue_pos starts at 0, so seq=0 with a dead owner
    # is "behind" only once dequeue advances. Set seq to a value that the
    # first dequeue (pos=0, checks seq < pos+1=1) sees as behind: seq must be
    # < 1, i.e. 0 — but slot 0 currently has seq=1 (init published). Overwrite
    # to simulate the crash BEFORE publish: seq stays at the initial 0.
    native.store_u64(ring_slot0 + slot_seq_off, 0)

    # Watch the channel + drain. The pump's drain_rings calls try_dequeue,
    # which on slot 0 sees seq(0) < pos+1(1) AND owner(999999) dead → recover.
    # Run inside an async helper so _ensure_loop (which needs a running loop)
    # works, and pump.watch_channel's immediate drain fires the recovery.
    async def _drain_and_verify() -> None:
        _ = layer._ensure_loop()
        pump = layer._pump
        assert pump is not None
        _ = pump.watch_channel(channel)
        # The recover path recycles slot 0 and clears the owner. Poll for the
        # owner being cleared (proof recover_slot ran).
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if native.read_u32(ring_slot0 + slot_owner_pid_off) == 0:
                return
            await asyncio.sleep(0.05)
        msg = "E2 recover did not clear the dead owner within 5s"
        raise AssertionError(msg)

    loop.run_until_complete(_drain_and_verify())

    # RECOVERY ASSERTION: the dead owner we injected must have been cleared.
    final_owner_pid = native.read_u32(ring_slot0 + slot_owner_pid_off)
    assert final_owner_pid == 0, (
        f"E2 recover did not clear the dead owner: owner_pid={final_owner_pid} "
        f"(expected 0 after recover_slot)"
    )
    # And seq must have advanced to ticket + cap (slot recycled).
    final_seq = native.load_u64(ring_slot0 + slot_seq_off)
    assert final_seq == cap, (
        f"E2 recover did not recycle slot seq: seq={final_seq} "
        f"(expected {cap} = ticket(0) + cap)"
    )

    # And the ring must remain usable end-to-end.
    loop.run_until_complete(layer.send(channel, {"type": "post_recover"}))
    msg = loop.run_until_complete(asyncio.wait_for(layer.receive(channel), timeout=5.0))
    assert msg["type"] == "post_recover", f"unexpected: {msg!r}"

    loop.run_until_complete(layer.close())
    _cleanup(PREFIX)
    print("E2 test passed: dead-owner slot recovered via dequeue, ring usable")
