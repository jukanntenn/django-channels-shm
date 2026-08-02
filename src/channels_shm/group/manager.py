"""Group member management."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from channels_shm._native import (
    group_index_create_or_find,
    group_index_lookup,
    group_member_add,
    group_member_remove,
    group_members_read_all,
)

if TYPE_CHECKING:
    from channels_shm._native import ShmRegion, SlabAllocator


class GroupManager:
    """Manages group membership in shared memory.

    INVARIANTS (§7.2 V4.1, G-05): the per-group member array is fixed-length,
    allocated ONCE from the dynamic pool slab by group_index_create_or_find.
    `members_offset` is STABLE for the whole group lifetime — never
    reallocated, never freed until flush. member add/discard toggle the
    in-place `member_active` flag; group_discard down to zero members marks the
    GroupSlot inactive but does NOT free the members array (it is reused if
    the same group name is re-created). This stability is what makes the
    seqlock-free, lock-free group_send read safe: the offset always points at
    valid memory.
    """

    region: ShmRegion
    slab: SlabAllocator
    max_groups: int
    max_members_per_group: int
    group_expiry: int

    def __init__(
        self,
        region: ShmRegion,
        slab: SlabAllocator,
        max_groups: int,
        max_members_per_group: int,
        group_expiry: int,
    ) -> None:
        self.region = region
        self.slab = slab
        self.max_groups = max_groups
        self.max_members_per_group = max_members_per_group
        self.group_expiry = group_expiry

    def add(self, group: str, channel: str) -> None:
        """Add a channel to a group. Must be called under flock."""
        grp_slot_off, members_offset = group_index_create_or_find(
            self.region,
            group,
            self.slab,
            self.max_groups,
            self.max_members_per_group,
        )
        if grp_slot_off == 0:
            # Either the index is full OR the slab is exhausted (R-02: the Rust
            # side returns (0,0) for both; the OOM case used to corrupt the shm
            # header before the guard was added).
            msg = (
                f"Group index full or slab out of memory (max_groups={self.max_groups})"
            )
            raise RuntimeError(msg)

        now = int(time.time())
        ok = group_member_add(
            self.region,
            grp_slot_off,
            members_offset,
            channel,
            now,
            self.max_members_per_group,
            self.group_expiry,
        )
        if not ok:
            msg = f"Group '{group}' full (max_members_per_group={self.max_members_per_group})"
            raise RuntimeError(msg)

    def discard(self, group: str, channel: str) -> None:
        """Remove a channel from a group. Must be called under flock."""
        found, grp_slot_off, members_offset, _count, _active = group_index_lookup(
            self.region, group, self.max_groups
        )
        if not found:
            return

        _ = group_member_remove(
            self.region,
            grp_slot_off,
            members_offset,
            channel,
            self.max_members_per_group,
        )

    def lookup_slot(self, group: str) -> tuple[bool, int, int, int, bool]:
        """Look up a group's index slot. Returns (found, slot_off, members_off,
        member_count, active). Thin wrapper for the layer's slot cache (I-01).
        """
        return group_index_lookup(self.region, group, self.max_groups)

    def get_members(self, group: str) -> list[str]:
        """Get all active (non-expired) members of a group.

        I-01/I-02: lock-free (the index lookup is seqlock-safe, member reads
        are atomic single-byte/8-byte), and uses a single bulk Rust call with
        Rust-side expiry filtering instead of up to max_members_per_group FFI
        round trips. Safe to call without holding the flock (§7.4 step 4:
        lock-free snapshot read).
        """
        found, _grp_slot_off, members_offset, count, active = group_index_lookup(
            self.region, group, self.max_groups
        )
        if not found or not active or count == 0:
            return []

        now = int(time.time())
        # One FFI call; expiry filter (strictly <, §7.1) applied in Rust.
        return group_members_read_all(
            self.region,
            members_offset,
            self.max_members_per_group,
            now,
            self.group_expiry,
        )
