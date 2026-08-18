"""group_send routing tests: empty/self/other-prefix/broadcast/full/errors.

Maps to the groups extension of src/channels_shm/layer.py (never raises
ChannelFull for a full member; wakes each owning process at most once).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from channels_shm.exceptions import MessageTooLarge
from tests.layer._helpers import register_fake_process

if TYPE_CHECKING:
    from channels_shm import SharedMemoryChannelLayer
    from channels_shm.serializer import Message


class TestGroupSend:
    """group_send routing: empty, self, other-prefix, broadcast, full, errors."""

    async def test_to_empty_group(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.group_send("nonexistent", {"type": "msg"})

    async def test_to_self_channel(self, layer: SharedMemoryChannelLayer) -> None:
        ch = await layer.new_channel("test.")
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})

    async def test_to_other_process_channel(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        ch = "test.other_prefix!abc123"
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})

    async def test_broadcast(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.group_add("g", "regular_channel")
        await layer.group_send("g", {"type": "msg"})

    async def test_broadcast_multiple_channels(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        await layer.group_add("g", "ch1")
        await layer.group_add("g", "ch2")
        await layer.group_send("g", {"type": "msg"})

    async def test_multiple_same_other_prefix(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Multiple channels of the same other prefix wake that owner once."""
        await layer.group_add("g", "specific.SAMEPREFIX!abc")
        await layer.group_add("g", "specific.SAMEPREFIX!def")
        await layer.group_send("g", {"type": "msg"})

    async def test_channel_full_silently_skips(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send never raises ChannelFull for a full member."""
        ch = await layer.new_channel("test.")
        await layer.group_add("g", ch)
        for _ in range(layer.capacity):
            await layer.send(ch, {"type": "msg"})
        await layer.group_send("g", {"type": "msg"})

    async def test_message_too_large(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.group_add("g", "ch")
        big_msg: Message = {"type": "big", "data": "x" * (1024 * 1024)}
        with pytest.raises(MessageTooLarge):
            await layer.group_send("g", big_msg)

    async def test_non_dict_message(self, layer: SharedMemoryChannelLayer) -> None:
        with pytest.raises(TypeError):
            await layer.group_send("g", "not a dict")  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

    async def test_lock_none_raises(self, layer: SharedMemoryChannelLayer) -> None:
        """A torn-down lock must not be silently dereferenced."""
        await layer.group_add("g", "ch1")
        original_lock = layer._lock
        layer._lock = None
        try:
            with pytest.raises(RuntimeError, match="closed"):
                await layer.group_send("g", {"type": "msg"})
        finally:
            layer._lock = original_lock

    async def test_dead_process_member(self, layer: SharedMemoryChannelLayer) -> None:
        """group_send to a group containing a dead owner's channel."""
        register_fake_process(
            layer, "dead_prefix3", "/tmp/nonexistent_dead_socket_54321.sock"
        )
        ch = "specific.DEADPREFIX3!abc"
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})

    async def test_delivers_to_all_members(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """A broadcast reaches every group member in FIFO order."""
        channels: list[str] = []
        for _ in range(3):
            ch = await layer.new_channel("test.")
            await layer.group_add("multi", ch)
            channels.append(ch)
        await layer.group_send("multi", {"type": "broadcast"})
        for ch in channels:
            msg = await asyncio.wait_for(layer.receive(ch), timeout=5.0)
            assert msg == {"type": "broadcast"}
