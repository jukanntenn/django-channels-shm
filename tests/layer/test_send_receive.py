"""ASGI send/receive contract tests for SharedMemoryChannelLayer.

Maps to the channel-layer API of src/channels_shm/layer.py: round-trips, FIFO,
message-type fidelity, size limits, reserved-key guards, and expiry semantics.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from channels_shm.exceptions import ChannelFull, MessageTooLarge

if TYPE_CHECKING:
    from collections.abc import Callable

    from channels_shm import SharedMemoryChannelLayer
    from channels_shm.serializer import Message

# Upper bound the pump is allowed to take to deliver an in-process message.
_RECEIVE_TIMEOUT = 5.0


class TestSpecCompliance:
    """ASGI channel-layer spec behaviors."""

    async def test_send_receive_basic(self, layer: SharedMemoryChannelLayer) -> None:
        """Basic send and receive should work."""
        channel = await layer.new_channel("test.")
        await layer.send(channel, {"type": "hello", "data": "world"})
        msg = await asyncio.wait_for(layer.receive(channel), timeout=_RECEIVE_TIMEOUT)
        assert msg == {"type": "hello", "data": "world"}

    async def test_send_receive_fifo(self, layer: SharedMemoryChannelLayer) -> None:
        """Messages should be received in FIFO order."""
        channel = await layer.new_channel("test.")
        messages: list[Message] = [{"type": "msg", "seq": i} for i in range(5)]
        for msg in messages:
            await layer.send(channel, msg)

        received: list[Message] = []
        for _ in range(5):
            msg = await asyncio.wait_for(
                layer.receive(channel), timeout=_RECEIVE_TIMEOUT
            )
            received.append(msg)

        assert received == messages

    async def test_send_dict_required(self, layer: SharedMemoryChannelLayer) -> None:
        """send() must raise TypeError for non-dict messages."""
        channel = await layer.new_channel("test.")
        with pytest.raises(TypeError):
            await layer.send(channel, "not a dict")  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

    async def test_send_message_too_large(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """send() must raise MessageTooLarge for messages > 1MB."""
        channel = await layer.new_channel("test.")
        big_msg: Message = {"type": "big", "data": "x" * (1024 * 1024)}
        with pytest.raises(MessageTooLarge):
            await layer.send(channel, big_msg)

    async def test_send_message_at_limit(self, layer: SharedMemoryChannelLayer) -> None:
        """A message that serializes to exactly the 1MB limit is accepted."""
        channel = await layer.new_channel("test.")
        # 1MB of payload stays under the limit only if the framing overhead
        # keeps the encoded size <= MAX_MESSAGE_SIZE; the round-trip proves it.
        msg: Message = {"type": "big", "data": "x" * (1024 * 1024 - 64)}
        await layer.send(channel, msg)
        received = await asyncio.wait_for(
            layer.receive(channel), timeout=_RECEIVE_TIMEOUT
        )
        assert received == msg

    async def test_send_channel_name_validation(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """send() must reject invalid channel names."""
        with pytest.raises(TypeError):
            await layer.send("", {"type": "test"})
        with pytest.raises(TypeError):
            await layer.send("invalid name with spaces", {"type": "test"})

    async def test_receive_channel_name_validation(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """receive() must reject invalid channel names."""
        with pytest.raises(TypeError):
            _ = await layer.receive("")

    async def test_message_types(self, layer: SharedMemoryChannelLayer) -> None:
        """All spec-required types should round-trip correctly."""
        channel = await layer.new_channel("test.")
        msg: Message = {
            "type": "test",
            "none_val": None,
            "bool_val": True,
            "int_val": 42,
            "float_val": 3.14,
            "str_val": "hello",
            "bytes_val": b"binary",
            "list_val": [1, 2, 3],
            "dict_val": {"nested": True},
        }
        await layer.send(channel, msg)
        received = await asyncio.wait_for(
            layer.receive(channel), timeout=_RECEIVE_TIMEOUT
        )
        assert received["type"] == "test"
        assert received["none_val"] is None
        assert received["bool_val"] is True
        assert received["int_val"] == 42
        assert received["float_val"] == pytest.approx(3.14)
        assert received["str_val"] == "hello"
        assert received["bytes_val"] == b"binary"
        assert received["list_val"] == [1, 2, 3]
        assert received["dict_val"] == {"nested": True}

    async def test_tuple_becomes_list(self, layer: SharedMemoryChannelLayer) -> None:
        """Tuples should be encoded as lists (msgpack behavior)."""
        channel = await layer.new_channel("test.")
        await layer.send(
            channel,
            {"type": "test", "data": (1, 2, 3)},  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
        )
        received = await asyncio.wait_for(
            layer.receive(channel), timeout=_RECEIVE_TIMEOUT
        )
        assert received["data"] == [1, 2, 3]

    async def test_int_overflow(self, layer: SharedMemoryChannelLayer) -> None:
        """Integers outside 64-bit signed range should raise OverflowError."""
        channel = await layer.new_channel("test.")
        with pytest.raises(OverflowError):
            await layer.send(channel, {"type": "test", "val": 2**64})

    async def test_flush_resets_layer(self, layer: SharedMemoryChannelLayer) -> None:
        """flush() resets the layer; sending still works afterwards."""
        channel = "test_flush"
        await layer.send(channel, {"type": "before_flush"})
        await layer.flush()
        await layer.send(channel, {"type": "after_flush"})

    async def test_group_expiry_attribute(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_expiry must be exposed as an attribute."""
        assert hasattr(layer, "group_expiry")
        assert isinstance(layer.group_expiry, int)

    async def test_extensions_attribute(self, layer: SharedMemoryChannelLayer) -> None:
        """extensions must include 'groups' and 'flush'."""
        assert "groups" in layer.extensions
        assert "flush" in layer.extensions


class TestApiContractGuards:
    """Reserved-key and ownership guards."""

    async def test_send_rejects_reserved_key(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """__asgi_channel__ must not be smuggled into messages."""
        ch = await layer.new_channel("test.")
        msg: Message = {"type": "test", "__asgi_channel__": "x"}
        with pytest.raises(ValueError, match="__asgi_channel__"):
            await layer.send(ch, msg)

    async def test_group_send_rejects_reserved_key(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        await layer.group_add("g", "ch")
        msg: Message = {"type": "test", "__asgi_channel__": "x"}
        with pytest.raises(ValueError, match="__asgi_channel__"):
            await layer.group_send("g", msg)

    async def test_receive_other_process_channel(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Receiving on another process's private channel is rejected."""
        ch = "specific.OTHERPREFIX!abc123"
        with pytest.raises(ValueError, match="owned by another process"):
            _ = await layer.receive(ch)


class TestExpiry:
    """Wall-clock message expiry: expired messages must never be delivered."""

    async def test_expired_message_skipped(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """A message past its expiry is dropped, not delivered.

        The pump filters on dequeue using the ring's expiry timestamp; waiting
        past expiry then receiving must come back empty rather than return the
        stale message.
        """
        layer = layer_factory(expiry=1)
        channel = await layer.new_channel("test.")
        await layer.send(channel, {"type": "short-lived"})

        # Let the expiry elapse, then force a drain by watching the channel.
        await asyncio.sleep(1.2)
        _ = layer._ensure_loop()
        pump = layer._pump
        assert pump is not None
        _ = pump.watch_channel(channel)  # initial drain drops the expired message
        assert pump._buffers[channel].qsize() == 0


class TestChannelCapacityBehavior:
    """Per-channel capacity overrides end to end (see test_config for get_capacity)."""

    async def test_override_capacity_limits_sends(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        layer = layer_factory(channel_capacity={"chat.*": 3})
        channel = "chat.full"
        for _ in range(3):
            await layer.send(channel, {"type": "msg"})
        with pytest.raises(ChannelFull):
            await layer.send(channel, {"type": "overflow"})

    async def test_default_capacity_accepts_default_budget(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """A non-matching channel uses the default capacity (capacity=10)."""
        channel = "plain.full"
        for _ in range(layer.capacity):
            await layer.send(channel, {"type": "msg"})
        with pytest.raises(ChannelFull):
            await layer.send(channel, {"type": "overflow"})
