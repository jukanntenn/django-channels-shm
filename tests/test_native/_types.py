"""Helpers shared by test_native modules.

Defines the runtime classes used by the fixtures so test modules can import
them by name for type annotations. pytest fixtures still match by parameter
name, but having real classes keeps static analysis happy.
"""

from __future__ import annotations

import ctypes
import mmap

from channels_shm._native import ShmRegion, SlabAllocator, compute_offsets, shm_init


def _anon_mmap(size: int) -> mmap.mmap:
    """Create a private anonymous mmap of the given size (zeroed)."""
    return mmap.mmap(-1, size, access=mmap.ACCESS_WRITE)


def _mmap_address(mm: mmap.mmap) -> int:
    """Get the base address of an mmap via ctypes (same trick as region.py)."""
    c_buf = (ctypes.c_char * 1).from_buffer(mm)
    return ctypes.addressof(c_buf)


class NativeRegion:
    """Bundle a mmap, native ShmRegion, and total size for a test."""

    mm: mmap.mmap
    region: ShmRegion
    size: int

    def __init__(self, size: int) -> None:
        self.mm = _anon_mmap(size)
        self.size = size
        self.region = ShmRegion(_mmap_address(self.mm), size)

    def close(self) -> None:
        self.mm.close()


class ShmLayout:
    """A fully initialized shm layout in a private mmap, ready for index ops."""

    region_bundle: NativeRegion
    slab: SlabAllocator
    max_channels: int
    max_groups: int
    max_members_per_group: int
    max_processes: int
    inline_size: int
    default_capacity: int
    expiry: int
    group_expiry: int

    def __init__(
        self,
        *,
        max_channels: int = 32,
        max_groups: int = 16,
        max_members_per_group: int = 8,
        max_processes: int = 8,
        inline_size: int = 512,
        default_capacity: int = 16,
        expiry: int = 60,
        group_expiry: int = 86400,
        pool_size: int = 128 * 1024,
    ) -> None:
        self.max_channels = max_channels
        self.max_groups = max_groups
        self.max_members_per_group = max_members_per_group
        self.max_processes = max_processes
        self.inline_size = inline_size
        self.default_capacity = default_capacity
        self.expiry = expiry
        self.group_expiry = group_expiry

        offsets = compute_offsets(
            max_channels, max_groups, max_members_per_group, max_processes
        )
        pool_off = offsets[5]
        total_size = pool_off + pool_size
        self.region_bundle = NativeRegion(total_size)

        # Slab allocator over the dynamic pool.
        self.slab = SlabAllocator(pool_off, pool_size)

        # Initialize the layout: header, indices, registry, slab.
        shm_init(
            self.region_bundle.region,
            total_size,
            inline_size,
            default_capacity,
            expiry,
            group_expiry,
            max_channels,
            max_groups,
            max_members_per_group,
            max_processes,
            self.slab,
        )

    @property
    def region(self) -> ShmRegion:
        return self.region_bundle.region

    def close(self) -> None:
        self.region_bundle.close()


__all__ = ["NativeRegion", "ShmLayout"]
