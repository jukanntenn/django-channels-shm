"""Channel index management and ring buffer lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from channels_shm._native import (
    RING_HEADER_SIZE,
    SLOT_SIZE,
    channel_index_create,
    channel_index_lookup,
)
from channels_shm._native import Ring as NativeRing

if TYPE_CHECKING:
    from channels_shm._native import ShmRegion, SlabAllocator


def non_local_name(channel: str) -> str:
    """Extract the non-local name from a process-specific channel.

    For "prefix.specific!local" → "prefix.specific!"
    For "plain_channel" → "plain_channel"

    Uses split (not str.index) to avoid the ValueError trap if the `"!" in`
    guard is ever removed in a refactor (C-02).
    """
    if "!" in channel:
        head, _tail = channel.split("!", 1)
        return f"{head}!"
    return channel


def client_prefix_of(channel: str) -> str:
    """Extract the owning process's client_prefix from a channel name.

    For "prefix.{client_prefix}!{suffix}" → "{client_prefix}"
    For "prefix.{client_prefix}!" → "{client_prefix}"
    For "plain_channel" (no '!') → "" (not process-specific)

    The client_prefix is the last '.'-separated segment before '!'. Used by
    receive() ownership validation (L-02) and targeted wakeup (L-03): the
    channel name's "{client_prefix}!" encodes the owning process, which maps to
    a socket path via the wakeup registry (§4.2.4).
    """
    if "!" not in channel:
        return ""
    head, _tail = channel.split("!", 1)
    return head.rsplit(".", 1)[-1]


def is_process_specific(channel: str) -> bool:
    """Check if a channel name is process-specific (contains '!')."""
    return "!" in channel


class ChannelManager:
    """Manages channel index lookups and ring buffer creation."""

    region: ShmRegion
    slab: SlabAllocator
    inline_size: int
    default_capacity: int
    max_channels: int

    def __init__(
        self,
        region: ShmRegion,
        slab: SlabAllocator,
        inline_size: int,
        default_capacity: int,
        max_channels: int,
    ) -> None:
        self.region = region
        self.slab = slab
        self.inline_size = inline_size
        self.default_capacity = default_capacity
        self.max_channels = max_channels

    def get_ring(
        self,
        channel: str,
    ) -> NativeRing | None:
        """Look up the ring for a channel. Returns None if not found."""
        ring_key = non_local_name(channel)
        found, _slot_off, ring_off, _cap, _non_local = channel_index_lookup(
            self.region, ring_key, self.max_channels
        )
        if not found or ring_off == 0:
            return None
        return NativeRing(ring_off)

    def get_or_create_ring(
        self,
        channel: str,
        capacity: int | None = None,
    ) -> tuple[NativeRing, bool]:
        """Get or create a ring for a channel.

        Must be called under flock.

        Returns:
            (ring, is_new) tuple.
        """
        ring_key = non_local_name(channel)
        is_non_local = is_process_specific(channel)
        cap = capacity or self.default_capacity

        # Try lookup first
        found, _slot_off, ring_off, _existing_cap, _non_local = channel_index_lookup(
            self.region, ring_key, self.max_channels
        )
        if found and ring_off != 0:
            return NativeRing(ring_off), False

        # Need to create: allocate ring from slab
        slot_size = SLOT_SIZE  # From native module
        ring_size = RING_HEADER_SIZE + cap * slot_size
        ring_offset = self.slab.alloc_cold(self.region, ring_size)

        # Initialize the ring
        ring = NativeRing(ring_offset)
        ring.init(self.region, cap)

        # Register in channel index
        slot_off, _already = channel_index_create(
            self.region,
            ring_key,
            ring_offset,
            cap,
            is_non_local,
            self.max_channels,
        )
        if slot_off == 0:
            # Index full — free the ring
            self.slab.free_cold(self.region, ring_offset, ring_size)
            msg = f"Channel index full (max_channels={self.max_channels})"
            raise RuntimeError(msg)

        return ring, True
