"""Integration tests for the SlabAllocator exposed via PyO3."""

from __future__ import annotations

from typing import TYPE_CHECKING

from channels_shm._native import SlabAllocator

if TYPE_CHECKING:
    from tests.native._types import NativeRegion


def test_init_and_alloc_small(region_64k: NativeRegion) -> None:
    """init + alloc should hand out a non-zero offset for a small request."""
    slab = SlabAllocator(0, region_64k.size)
    slab.init(region_64k.region)
    off = slab.alloc(region_64k.region, 100)
    assert off != 0


def test_alloc_distinct_offsets(region_64k: NativeRegion) -> None:
    """Two allocations should return distinct offsets."""
    slab = SlabAllocator(0, region_64k.size)
    slab.init(region_64k.region)
    off1 = slab.alloc(region_64k.region, 100)
    off2 = slab.alloc(region_64k.region, 100)
    assert off1 != 0
    assert off2 != 0
    assert off1 != off2


def test_free_then_reuse(region_64k: NativeRegion) -> None:
    """free() should push to the free list; next alloc should reuse it."""
    slab = SlabAllocator(0, region_64k.size)
    slab.init(region_64k.region)
    off1 = slab.alloc(region_64k.region, 100)
    slab.free(region_64k.region, off1, 100)
    off2 = slab.alloc(region_64k.region, 100)
    assert off1 == off2


def test_alloc_cold_path(region_64k: NativeRegion) -> None:
    """alloc_cold should work without holding the spinlock (cold-path variant)."""
    slab = SlabAllocator(0, region_64k.size)
    slab.init(region_64k.region)
    off1 = slab.alloc_cold(region_64k.region, 200)
    slab.free_cold(region_64k.region, off1, 200)
    off2 = slab.alloc_cold(region_64k.region, 200)
    assert off1 == off2


def test_alloc_different_size_classes(region_64k: NativeRegion) -> None:
    """Allocations in different size classes should land at different offsets."""
    slab = SlabAllocator(0, region_64k.size)
    slab.init(region_64k.region)
    off1 = slab.alloc(region_64k.region, 100)  # class 512
    off2 = slab.alloc(region_64k.region, 600)  # class 2048
    assert off1 != off2


def test_reset_clears_state(region_64k: NativeRegion) -> None:
    """reset should clear free lists and bump pointer so alloc works again."""
    slab = SlabAllocator(0, region_64k.size)
    slab.init(region_64k.region)
    _ = slab.alloc(region_64k.region, 100)
    slab.reset(region_64k.region)
    # After reset, a fresh alloc should still succeed (bump pointer was reset).
    off = slab.alloc(region_64k.region, 100)
    assert off != 0


def test_free_zero_offset_is_noop(region_64k: NativeRegion) -> None:
    """free(0, ...) should be a no-op (zero is the sentinel for failure)."""
    slab = SlabAllocator(0, region_64k.size)
    slab.init(region_64k.region)
    # Should not raise.
    slab.free(region_64k.region, 0, 100)


def test_alloc_too_large_returns_zero(region_64k: NativeRegion) -> None:
    """alloc of a size larger than the biggest size class should return 0."""
    from channels_shm._native import SIZE_CLASSES

    slab = SlabAllocator(0, region_64k.size)
    slab.init(region_64k.region)
    too_large = SIZE_CLASSES[-1] + 1
    assert slab.alloc(region_64k.region, too_large) == 0
