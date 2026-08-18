"""White-box helpers for inspecting / corrupting the native shm layout.

Offset arithmetic is derived from the ABI constants the Rust module exposes
(``compute_offsets``, ``CH_SLOT_SIZE``, ``RING_HEADER_SIZE``, ``SLOT_SIZE``),
never from bare magic numbers. The few intra-slot field offsets the module does
not expose are defined once here (with a pointer to the Rust source), so a
layout change touches exactly one place instead of a scatter of literals.

Why these tests reach into raw memory at all: the recovery/robustness tests
fabricate crash states (dead owner, stale-odd seqlock, ring-less slot) that no
public API can produce, because SIGKILL timing is uncontrollable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from channels_shm._native import (
    CH_SLOT_SIZE,
    GRP_SLOT_SIZE,
    RING_HEADER_SIZE,
    SLOT_SIZE,
    compute_offsets,
)

if TYPE_CHECKING:
    from channels_shm import SharedMemoryChannelLayer
    from channels_shm._native import ShmRegion

# Intra-slot field offsets (Rust crates/_channels_shm_native/src/layout.rs).
# Not re-exported by the native module, so defined here once.
CH_SLOT_NAME_LEN_OFF = 8
CH_SLOT_RING_OFFSET_OFF = 144
CH_SLOT_VERSION_OFF = 160
GRP_SLOT_NAME_LEN_OFF = 8
GRP_SLOT_VERSION_OFF = 160

# Ring header + ring-slot field offsets (layout.rs).
RING_CAPACITY_OFF = 16
RING_SLOT_SEQ_OFF = 0
RING_SLOT_OWNER_PID_OFF = 8
RING_SLOT_OWNER_TICKET_OFF = 16
RING_SLOT_OWNER_START_TIME_OFF = 24


def region_native(layer: SharedMemoryChannelLayer) -> ShmRegion:
    """The live native ShmRegion of a layer (asserts it exists)."""
    region = layer._region
    assert region is not None
    return region.native


def channel_index_off(layer: SharedMemoryChannelLayer) -> int:
    """Byte offset of the channel index within the header (compute_offsets[0])."""
    return compute_offsets(
        layer.max_channels,
        layer.max_groups,
        layer.max_members_per_group,
        layer.max_processes,
    )[0]


def group_index_off(layer: SharedMemoryChannelLayer) -> int:
    """Byte offset of the group index within the header (compute_offsets[1])."""
    return compute_offsets(
        layer.max_channels,
        layer.max_groups,
        layer.max_members_per_group,
        layer.max_processes,
    )[1]


def first_channel_slot(layer: SharedMemoryChannelLayer) -> int | None:
    """Offset of the first channel-index slot whose name length is nonzero.

    A non-zero name length means the slot is occupied (writes are seqlock
    guarded, so a committed slot is never half-written with a non-zero length).
    """
    native = region_native(layer)
    ch_off = channel_index_off(layer)
    for i in range(layer.max_channels):
        slot_off = ch_off + i * CH_SLOT_SIZE
        if native.read_u16(slot_off + CH_SLOT_NAME_LEN_OFF) > 0:
            return slot_off
    return None


def first_group_slot(layer: SharedMemoryChannelLayer) -> int | None:
    """Offset of the first occupied group-index slot (mirror of the channel one)."""
    native = region_native(layer)
    grp_off = group_index_off(layer)
    for i in range(layer.max_groups):
        slot_off = grp_off + i * GRP_SLOT_SIZE
        if native.read_u16(slot_off + GRP_SLOT_NAME_LEN_OFF) > 0:
            return slot_off
    return None


def ring_slot_off(ring_off: int, index: int = 0) -> int:
    """Byte offset of the `index`-th ring slot (after the ring header)."""
    return ring_off + RING_HEADER_SIZE + index * SLOT_SIZE
