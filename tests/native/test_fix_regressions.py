"""Native-module regression tests for the 2026-09 bug-fix batch.

Mirrors the Rust unit tests at the pyo3 boundary:
- Full never advances enqueue_pos (CAS-claim admission).
- capacity < 2 is rejected (0 divides by zero; 1 is not representable in the
  seq encoding — published-unconsumed and recycled both equal pos+1).
- Names longer than the 128-byte field width are rejected, not truncated.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

import pytest

from channels_shm._native import HDR_SIZE, Ring, SlabAllocator

if TYPE_CHECKING:
    from tests.native._types import NativeRegion

from tests.native.test_ring import _make_ring


def test_full_does_not_advance_enqueue_pos(region_64k: NativeRegion) -> None:
    """Every Full return consumes no ticket: drain → same-ticket retry works."""
    ring, slab = _make_ring(region_64k, capacity=2, inline_size=512)
    enq_pos_off = HDR_SIZE  # RING_ENQUEUE_POS = 0 within the ring header

    assert ring.try_enqueue(region_64k.region, slab, b"ch", b"a", math.inf, 1, 0)
    assert ring.try_enqueue(region_64k.region, slab, b"ch", b"b", math.inf, 1, 0)
    for _ in range(6):
        assert not ring.try_enqueue(
            region_64k.region, slab, b"ch", b"x", math.inf, 1, 0
        )
    assert region_64k.region.load_u64(enq_pos_off) == 2

    # Drain both slots; the next enqueue lands on ticket 2.
    assert ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0) is not None
    assert ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0) is not None
    assert ring.try_enqueue(region_64k.region, slab, b"ch", b"c", math.inf, 1, 0)
    assert region_64k.region.load_u64(enq_pos_off) == 3


@pytest.mark.parametrize("bad_capacity", [0, 1])
def test_init_rejects_capacity_below_two(
    region_64k: NativeRegion, bad_capacity: int
) -> None:
    """capacity=0 divides by zero on the first enqueue; capacity=1 cannot
    distinguish published from recycled (silent overwrite) — both rejected."""
    slab_off = HDR_SIZE + 4096
    slab_size = region_64k.size - slab_off
    slab = SlabAllocator(slab_off, slab_size)
    slab.init(region_64k.region)
    ring = Ring(HDR_SIZE)
    with pytest.raises(ValueError, match=">= 2"):
        ring.init(region_64k.region, bad_capacity)


def test_try_enqueue_rejects_overlong_name(region_64k: NativeRegion) -> None:
    """A 129-byte name is rejected instead of silently truncated (the old
    truncation misrouted the message: the pump delivered by the stored name,
    so the real receiver never saw it)."""
    ring, slab = _make_ring(region_64k, capacity=4, inline_size=512)
    with pytest.raises(ValueError, match="too long"):
        _ = ring.try_enqueue(region_64k.region, slab, b"x" * 129, b"m", math.inf, 1, 0)
    # 128 is fine.
    assert ring.try_enqueue(region_64k.region, slab, b"x" * 128, b"m", math.inf, 1, 0)


def test_reset_bumps_generation(region_64k: NativeRegion) -> None:
    """The flush fence: reset() bumps RING_GENERATION (offset 40, layout v2)
    before tearing down state, so in-flight ops can detect the race."""
    ring, _ = _make_ring(region_64k, capacity=2, inline_size=512)
    gen_off = HDR_SIZE + 40  # RING_GENERATION within the ring header
    assert region_64k.region.load_u64(gen_off) == 0
    ring.reset(region_64k.region)
    assert region_64k.region.load_u64(gen_off) == 1
    ring.reset(region_64k.region)
    assert region_64k.region.load_u64(gen_off) == 2


def test_flush_race_stress_ring_stays_functional(region_64k: NativeRegion) -> None:
    """Producers + a reset storm: every op stays bounded (no hang), and after
    the storm a full roundtrip works. The fence retracts raced operations
    instead of publishing into the wiped pool; no wedge, no crash."""
    import threading

    ring, slab = _make_ring(region_64k, capacity=8, inline_size=512)
    done = threading.Event()
    producer_ok: list[bool] = []

    def producer() -> None:
        sent = 0
        while sent < 50:
            if ring.try_enqueue(region_64k.region, slab, b"ch", b"m", math.inf, 1, 0):
                sent += 1
        producer_ok.append(sent == 50)
        done.set()

    def storm() -> None:
        for _ in range(200):
            ring.reset(region_64k.region)
            time.sleep(0.0005)

    # No process-level watchdog here: a wedge would hold the GIL inside the
    # native call, so a Timer could never fire anyway — the Rust twin
    # (test_ring_flush_race_stress_no_wedge) owns hang protection via a
    # deadline assert.
    tp = threading.Thread(target=producer)
    ts = threading.Thread(target=storm)
    tp.start()
    ts.start()
    # Drain concurrently so the ring can turn over during the storm.
    deadline = time.monotonic() + 10.0
    received = 0
    while not done.is_set() or received < 1:
        assert time.monotonic() < deadline, "flush-race stress wedged"
        if ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0) is not None:
            received += 1
    ts.join(timeout=5)
    tp.join(timeout=5)

    assert producer_ok == [True]
    assert received >= 1
    # Fully functional after the storm.
    assert ring.try_enqueue(region_64k.region, slab, b"ch", b"final", math.inf, 1, 0)
    result = ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0)
    assert result is not None
    assert result[1] == b"final"
