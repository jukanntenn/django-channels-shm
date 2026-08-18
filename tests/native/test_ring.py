"""Integration tests for the Ring (Vyukov MPMC) buffer exposed via PyO3."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from channels_shm._native import HDR_SIZE, Ring, SlabAllocator

if TYPE_CHECKING:
    from tests.native._types import NativeRegion


def _make_ring(
    region_bundle: NativeRegion,
    capacity: int,
    inline_size: int,
) -> tuple[Ring, SlabAllocator]:
    """Init a slab allocator and a ring at HDR_SIZE inside the region.

    The slab pool starts at HDR_SIZE + 4096 with whatever space remains.
    Callers must pass a region large enough for both the ring slots and a
    non-zero slab pool (use the ``region_64k`` fixture).
    """
    slab_off = HDR_SIZE + 4096
    slab_size = region_bundle.size - slab_off
    assert slab_size > 0, "region too small for slab pool"
    slab = SlabAllocator(slab_off, slab_size)
    slab.init(region_bundle.region)
    # Write the inline_size into the header so Ring.inline_size() can read it.
    region_bundle.region.write_u32(24, inline_size)  # HDR_INLINE_SIZE = 24
    ring = Ring(HDR_SIZE)
    ring.init(region_bundle.region, capacity)
    return ring, slab


def test_init_capacity(region_64k: NativeRegion) -> None:
    """Ring.capacity should return the capacity passed to init."""
    ring, _ = _make_ring(region_64k, capacity=8, inline_size=512)
    assert ring.capacity(region_64k.region) == 8
    assert ring.offset() == HDR_SIZE


def test_enqueue_dequeue_basic(region_64k: NativeRegion) -> None:
    """A single enqueue/dequeue should round-trip the message bytes."""
    ring, slab = _make_ring(region_64k, capacity=4, inline_size=512)
    assert ring.try_enqueue(region_64k.region, slab, b"ch", b"msg", math.inf, 1, 0)
    result = ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0)
    assert result is not None
    ch, msg = result
    assert ch == b"ch"
    assert msg == b"msg"


def test_fifo_order(region_64k: NativeRegion) -> None:
    """Messages should come out in the same order they went in."""
    ring, slab = _make_ring(region_64k, capacity=4, inline_size=512)
    for i in range(3):
        assert ring.try_enqueue(
            region_64k.region, slab, b"ch", bytes([i]), math.inf, 1, 0
        )
    for i in range(3):
        result = ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0)
        assert result is not None
        _, msg = result
        assert msg == bytes([i])


def test_empty_returns_none(region_64k: NativeRegion) -> None:
    """try_dequeue on an empty ring should return None."""
    ring, slab = _make_ring(region_64k, capacity=4, inline_size=512)
    assert ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0) is None


def test_full_returns_false(region_64k: NativeRegion) -> None:
    """try_enqueue on a full ring should return False."""
    ring, slab = _make_ring(region_64k, capacity=2, inline_size=512)
    assert ring.try_enqueue(region_64k.region, slab, b"ch", b"a", math.inf, 1, 0)
    assert ring.try_enqueue(region_64k.region, slab, b"ch", b"b", math.inf, 1, 0)
    # Ring is now full (capacity=2).
    assert not ring.try_enqueue(region_64k.region, slab, b"ch", b"c", math.inf, 1, 0)


def test_expired_message_skipped(region_64k: NativeRegion) -> None:
    """A message whose expiry has passed should be skipped on dequeue."""
    ring, slab = _make_ring(region_64k, capacity=4, inline_size=512)
    # expiry_ts in the past → already expired.
    assert ring.try_enqueue(region_64k.region, slab, b"ch", b"old", 0.0, 1, 0)
    # now is in the future → message is expired.
    assert ring.try_dequeue(region_64k.region, slab, 1.0, 1, 0) is None


def test_overflow_message(region_64k: NativeRegion) -> None:
    """A message larger than inline_size should be stored via the slab."""
    ring, slab = _make_ring(region_64k, capacity=4, inline_size=64)
    big_msg = b"x" * 200  # larger than inline_size (64)
    assert ring.try_enqueue(region_64k.region, slab, b"ch", big_msg, math.inf, 1, 0)
    result = ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0)
    assert result is not None
    _, msg = result
    assert msg == big_msg


def test_reset_clears_ring(region_64k: NativeRegion) -> None:
    """reset should empty the ring so dequeue returns None afterwards."""
    ring, slab = _make_ring(region_64k, capacity=4, inline_size=512)
    assert ring.try_enqueue(region_64k.region, slab, b"ch", b"msg", math.inf, 1, 0)
    ring.reset(region_64k.region)
    assert ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0) is None


def test_wrap_around(region_64k: NativeRegion) -> None:
    """The ring should wrap around and keep serving messages past capacity."""
    ring, slab = _make_ring(region_64k, capacity=2, inline_size=512)
    # capacity=2 → enqueue/dequeue several times to force wrap-around.
    for i in range(6):
        assert ring.try_enqueue(
            region_64k.region, slab, b"ch", bytes([i]), math.inf, 1, 0
        )
        result = ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0)
        assert result is not None
        _, msg = result
        assert msg == bytes([i])


def test_dequeue_at_most_once(region_64k: NativeRegion) -> None:
    """Each enqueued message should be dequeued exactly once."""
    ring, slab = _make_ring(region_64k, capacity=4, inline_size=512)
    n = 4
    for i in range(n):
        assert ring.try_enqueue(
            region_64k.region, slab, b"ch", bytes([i]), math.inf, 1, 0
        )
    seen: list[int] = []
    for _ in range(n):
        result = ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0)
        assert result is not None
        _, msg = result
        seen.append(msg[0])
    # No more messages.
    assert ring.try_dequeue(region_64k.region, slab, math.inf, 1, 0) is None
    assert sorted(seen) == list(range(n))
