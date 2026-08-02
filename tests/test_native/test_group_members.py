"""Integration tests for group membership operations exposed via PyO3."""

from __future__ import annotations

from typing import TYPE_CHECKING

from channels_shm._native import (
    group_index_create_or_find,
    group_index_lookup,
    group_member_add,
    group_member_read,
    group_member_remove,
)

if TYPE_CHECKING:
    from tests.test_native._types import ShmLayout


def _make_group(layout: ShmLayout, name: str = "g") -> tuple[int, int]:
    """Create a group and return (slot_offset, members_offset)."""
    slot, members = group_index_create_or_find(
        layout.region,
        name,
        layout.slab,
        layout.max_groups,
        layout.max_members_per_group,
    )
    assert slot != 0
    assert members != 0
    return slot, members


def test_add_then_read_member(layout: ShmLayout) -> None:
    """Adding a member should make it readable at index 0."""
    slot, members = _make_group(layout)
    ok = group_member_add(
        layout.region,
        slot,
        members,
        "ch.A",
        now=1000,
        max_members=layout.max_members_per_group,
        group_expiry=layout.group_expiry,
    )
    assert ok is True

    active, name, join_time = group_member_read(layout.region, members, 0)
    assert active is True
    assert name == b"ch.A"
    assert join_time == 1000


def test_add_idempotent(layout: ShmLayout) -> None:
    """Adding the same channel twice should be idempotent (no duplicate)."""
    slot, members = _make_group(layout)
    assert group_member_add(
        layout.region,
        slot,
        members,
        "ch.A",
        1000,
        layout.max_members_per_group,
        layout.group_expiry,
    )
    assert group_member_add(
        layout.region,
        slot,
        members,
        "ch.A",
        2000,
        layout.max_members_per_group,
        layout.group_expiry,
    )
    # Only one slot should be occupied.
    active0, name0, join0 = group_member_read(layout.region, members, 0)
    active1, _, _ = group_member_read(layout.region, members, 1)
    assert active0 is True
    assert name0 == b"ch.A"
    # Second add should have updated the join_time on the same slot.
    assert join0 == 2000
    assert active1 is False


def test_remove_member(layout: ShmLayout) -> None:
    """Removing a member should deactivate its slot."""
    slot, members = _make_group(layout)
    assert group_member_add(
        layout.region,
        slot,
        members,
        "ch.A",
        1000,
        layout.max_members_per_group,
        layout.group_expiry,
    )
    ok = group_member_remove(
        layout.region,
        slot,
        members,
        "ch.A",
        layout.max_members_per_group,
    )
    assert ok is True
    active, _, _ = group_member_read(layout.region, members, 0)
    assert active is False


def test_remove_nonexistent_returns_false(layout: ShmLayout) -> None:
    """Removing a member that isn't in the group should return False."""
    slot, members = _make_group(layout)
    ok = group_member_remove(
        layout.region,
        slot,
        members,
        "ch.X",
        layout.max_members_per_group,
    )
    assert ok is False


def test_add_multiple_members(layout: ShmLayout) -> None:
    """Multiple distinct channels should occupy distinct slots."""
    slot, members = _make_group(layout)
    for i in range(4):
        assert group_member_add(
            layout.region,
            slot,
            members,
            f"ch.{i}",
            1000 + i,
            layout.max_members_per_group,
            layout.group_expiry,
        )
    seen: set[bytes] = set()
    for i in range(4):
        active, name, _ = group_member_read(layout.region, members, i)
        assert active is True
        seen.add(name)
    assert seen == {b"ch.0", b"ch.1", b"ch.2", b"ch.3"}


def test_member_count_increments(layout: ShmLayout) -> None:
    """group_member_add should increment the group's member_count."""
    slot, members = _make_group(layout)
    assert group_member_add(
        layout.region,
        slot,
        members,
        "a",
        1,
        layout.max_members_per_group,
        layout.group_expiry,
    )
    assert group_member_add(
        layout.region,
        slot,
        members,
        "b",
        1,
        layout.max_members_per_group,
        layout.group_expiry,
    )
    _, _, _, count, _ = group_index_lookup(layout.region, "g", layout.max_groups)
    assert count == 2


def test_remove_decrements_count_and_deactivates_group_when_empty(
    layout: ShmLayout,
) -> None:
    """Removing the last member should mark the group inactive."""
    slot, members = _make_group(layout)
    assert group_member_add(
        layout.region,
        slot,
        members,
        "only",
        1,
        layout.max_members_per_group,
        layout.group_expiry,
    )
    assert group_member_remove(
        layout.region,
        slot,
        members,
        "only",
        layout.max_members_per_group,
    )
    _, _, _, count, active = group_index_lookup(layout.region, "g", layout.max_groups)
    assert count == 0
    assert active is False


def test_expired_member_slot_reused(layout: ShmLayout) -> None:
    """Adding a new channel should be able to reuse an expired member slot."""
    slot, members = _make_group(layout)
    # Add a member with join_time in the far past so it's expired.
    # group_expiry default is 86400; join_time=0, now=200000 → expired.
    assert group_member_add(
        layout.region,
        slot,
        members,
        "old",
        0,
        layout.max_members_per_group,
        layout.group_expiry,
    )
    # Add a new member; the expired slot should be reusable.
    ok = group_member_add(
        layout.region,
        slot,
        members,
        "new",
        200_000,
        layout.max_members_per_group,
        layout.group_expiry,
    )
    assert ok is True
