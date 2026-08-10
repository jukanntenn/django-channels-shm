"""Unit tests for channels_shm.channel.manager.

Maps to src/channels_shm/channel/manager.py. Covers the pure name-parsing
helpers and ChannelManager ring lookup/creation paths, including the
index-full error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from channels_shm.channel.manager import (
    client_prefix_of,
    is_process_specific,
    non_local_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from channels_shm import SharedMemoryChannelLayer


class TestNameHelpers:
    """Pure functions used for process-specific channel routing."""

    def test_non_local_name_process_specific(self) -> None:
        assert non_local_name("prefix.specific!local") == "prefix.specific!"

    def test_non_local_name_plain(self) -> None:
        assert non_local_name("plain_channel") == "plain_channel"

    def test_non_local_name_only_bang(self) -> None:
        """Split happens at the FIRST '!' — the local suffix may contain '!'."""
        assert non_local_name("prefix!a!b") == "prefix!"

    def test_client_prefix_of_process_specific(self) -> None:
        assert client_prefix_of("specific.abc123!def") == "abc123"

    def test_client_prefix_of_no_suffix(self) -> None:
        assert client_prefix_of("specific.abc123!") == "abc123"

    def test_client_prefix_of_plain(self) -> None:
        assert client_prefix_of("plain_channel") == ""

    def test_client_prefix_of_multiple_dots(self) -> None:
        """The owning prefix is the last dot-separated segment before '!'."""
        assert client_prefix_of("a.b.c!d") == "c"

    def test_is_process_specific(self) -> None:
        assert is_process_specific("a!b")
        assert is_process_specific("a!")
        assert not is_process_specific("a")


class TestChannelManager:
    """Ring lookup and creation."""

    async def test_get_or_create_ring_existing(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """get_or_create_ring returns the same ring on a second lookup."""
        channel_mgr = layer._channel_mgr
        assert channel_mgr is not None
        ring1, is_new = channel_mgr.get_or_create_ring("test.ch", 10)
        assert is_new
        ring2, is_new2 = channel_mgr.get_or_create_ring("test.ch", 10)
        assert not is_new2
        assert ring1.offset() == ring2.offset()

    async def test_get_ring_not_found(self, layer: SharedMemoryChannelLayer) -> None:
        """get_ring on a non-existent channel returns None."""
        channel_mgr = layer._channel_mgr
        assert channel_mgr is not None
        assert channel_mgr.get_ring("nonexistent_channel") is None

    async def test_get_ring_found(self, layer: SharedMemoryChannelLayer) -> None:
        """get_ring after creation returns the ring."""
        channel_mgr = layer._channel_mgr
        assert channel_mgr is not None
        ring1, _ = channel_mgr.get_or_create_ring("test.ch", 10)
        ring2 = channel_mgr.get_ring("test.ch")
        assert ring2 is not None
        assert ring1.offset() == ring2.offset()


class TestChannelIndexFull:
    """get_or_create_ring raises RuntimeError when the channel index is full."""

    async def test_index_full(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        layer = layer_factory(max_channels=1)
        channel_mgr = layer._channel_mgr
        assert channel_mgr is not None
        _, is_new = channel_mgr.get_or_create_ring("ch1", 10)
        assert is_new
        with pytest.raises(RuntimeError, match="Channel index full"):
            _ = channel_mgr.get_or_create_ring("ch2", 10)
