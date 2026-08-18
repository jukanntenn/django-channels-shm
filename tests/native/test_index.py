"""Integration tests for channel/group index functions exposed via PyO3."""

from __future__ import annotations

from typing import TYPE_CHECKING

from channels_shm._native import (
    channel_index_create,
    channel_index_lookup,
    group_index_create_or_find,
    group_index_lookup,
)

if TYPE_CHECKING:
    from tests.native._types import ShmLayout


def test_channel_index_lookup_empty(layout: ShmLayout) -> None:
    """Lookup on an empty index should return (False, 0, 0, 0, False)."""
    found, slot, ring, cap, non_local = channel_index_lookup(
        layout.region, "no.such.channel", layout.max_channels
    )
    assert found is False
    assert slot == 0
    assert ring == 0
    assert cap == 0
    assert non_local is False


def test_channel_index_create_and_lookup(layout: ShmLayout) -> None:
    """Create then lookup should return the stored metadata."""
    slot_off, existed = channel_index_create(
        layout.region, "test.ch", 0x1000, 16, False, layout.max_channels
    )
    assert existed is False
    assert slot_off != 0

    found, lookup_slot, ring, cap, non_local = channel_index_lookup(
        layout.region, "test.ch", layout.max_channels
    )
    assert found is True
    assert lookup_slot == slot_off
    assert ring == 0x1000
    assert cap == 16
    assert non_local is False


def test_channel_index_create_idempotent(layout: ShmLayout) -> None:
    """Creating the same channel twice should report existed=True on the 2nd."""
    slot1, existed1 = channel_index_create(
        layout.region, "dup.ch", 0x1000, 16, False, layout.max_channels
    )
    assert existed1 is False
    slot2, existed2 = channel_index_create(
        layout.region, "dup.ch", 0x2000, 32, True, layout.max_channels
    )
    assert existed2 is True
    assert slot1 == slot2
    # Lookup reflects the FIRST write, not the second.
    _, _, ring, cap, non_local = channel_index_lookup(
        layout.region, "dup.ch", layout.max_channels
    )
    assert ring == 0x1000
    assert cap == 16
    assert non_local is False


def test_channel_index_create_distinct(layout: ShmLayout) -> None:
    """Distinct channel names should land in distinct slots."""
    s1, _ = channel_index_create(layout.region, "a", 1, 10, False, layout.max_channels)
    s2, _ = channel_index_create(layout.region, "b", 2, 20, False, layout.max_channels)
    s3, _ = channel_index_create(layout.region, "c", 3, 30, True, layout.max_channels)
    assert len({s1, s2, s3}) == 3
    assert channel_index_lookup(layout.region, "a", layout.max_channels)[0]
    assert channel_index_lookup(layout.region, "b", layout.max_channels)[0]
    assert channel_index_lookup(layout.region, "c", layout.max_channels)[0]


def test_channel_index_lookup_nonexistent(layout: ShmLayout) -> None:
    """Lookup of an uncreated channel should report not found."""
    _ = channel_index_create(layout.region, "real", 1, 10, False, layout.max_channels)
    found, _, _, _, _ = channel_index_lookup(
        layout.region, "phantom", layout.max_channels
    )
    assert found is False


def test_group_index_lookup_empty(layout: ShmLayout) -> None:
    """Lookup on an empty group index should return all-zeros."""
    found, slot, members, count, active = group_index_lookup(
        layout.region, "no.such.group", layout.max_groups
    )
    assert found is False
    assert slot == 0
    assert members == 0
    assert count == 0
    assert active is False


def test_group_index_create_or_find_creates(layout: ShmLayout) -> None:
    """First call should create the group and an empty members array."""
    slot, members = group_index_create_or_find(
        layout.region,
        "grp",
        layout.slab,
        layout.max_groups,
        layout.max_members_per_group,
    )
    assert slot != 0
    assert members != 0

    found, lookup_slot, lookup_members, count, active = group_index_lookup(
        layout.region, "grp", layout.max_groups
    )
    assert found is True
    assert lookup_slot == slot
    assert lookup_members == members
    assert count == 0
    assert active is True


def test_group_index_create_or_find_idempotent(layout: ShmLayout) -> None:
    """A second call with the same name should return the same slot/members."""
    slot1, members1 = group_index_create_or_find(
        layout.region,
        "g",
        layout.slab,
        layout.max_groups,
        layout.max_members_per_group,
    )
    slot2, members2 = group_index_create_or_find(
        layout.region,
        "g",
        layout.slab,
        layout.max_groups,
        layout.max_members_per_group,
    )
    assert slot1 == slot2
    assert members1 == members2
