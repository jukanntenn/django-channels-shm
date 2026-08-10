"""Unit tests for channels_shm.group.manager.

Maps to src/channels_shm/group/manager.py. Covers membership add/discard,
get_members expiry filtering, and the index-full / group-full errors.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from channels_shm import SharedMemoryChannelLayer


class TestGroupManager:
    """Membership lifecycle."""

    async def test_discard_nonexistent_group(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """discard on a non-existent group should not raise."""
        await layer.group_discard("nonexistent", "ch")

    async def test_get_members_empty_group(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """get_members on a non-existent group returns empty list."""
        group_mgr = layer._group_mgr
        assert group_mgr is not None
        assert group_mgr.get_members("nonexistent") == []

    async def test_add_and_discard(self, layer: SharedMemoryChannelLayer) -> None:
        """Add then discard a member, verify group is empty after."""
        await layer.group_add("g", "ch1")
        group_mgr = layer._group_mgr
        assert group_mgr is not None
        assert "ch1" in group_mgr.get_members("g")
        await layer.group_discard("g", "ch1")
        assert "ch1" not in group_mgr.get_members("g")

    async def test_discard_nonexistent_member(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """discard a member that's not in the group should not raise."""
        await layer.group_add("g", "ch1")
        await layer.group_discard("g", "nonexistent_member")


class TestGroupManagerErrors:
    """Structural capacity errors."""

    async def test_group_index_full(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """group_add raises RuntimeError when the group index is full."""
        layer = layer_factory(max_groups=1)
        await layer.group_add("g1", "ch1")
        with pytest.raises(RuntimeError, match="Group index full"):
            await layer.group_add("g2", "ch1")

    async def test_group_members_full(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """group_add raises RuntimeError when the group is full."""
        layer = layer_factory(max_members_per_group=1)
        await layer.group_add("g", "ch1")
        with pytest.raises(RuntimeError, match="Group 'g' full"):
            await layer.group_add("g", "ch2")

    async def test_get_members_expired(self, layer: SharedMemoryChannelLayer) -> None:
        """get_members skips members past the group expiry."""
        await layer.group_add("g", "ch1")
        group_mgr = layer._group_mgr
        assert group_mgr is not None
        with patch(
            "channels_shm.group.manager.time.time", return_value=time.time() + 100000
        ):
            members = group_mgr.get_members("g")
        assert members == [], "expired members should be skipped"


class TestGroupSlotCache:
    """I-01: the per-process group-slot offset cache tracks structural changes.

    group_add populates the cache (group_send reads it lock-free); flush
    invalidates every offset, dropping the whole cache.
    """

    async def test_add_populates_cache(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.group_add("g", "ch1")
        assert "g" in layer._group_slot_cache
        assert layer._group_slot_cache["g"] != 0

    async def test_flush_clears_cache(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.group_add("g", "ch1")
        assert layer._group_slot_cache  # populated by the add above
        await layer.flush()
        assert layer._group_slot_cache == {}
