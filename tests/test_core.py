"""L1: Spec compliance tests for SharedMemoryChannelLayer."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from channels_shm.exceptions import ChannelFull, MessageTooLarge

if TYPE_CHECKING:
    from channels_shm import SharedMemoryChannelLayer
    from channels_shm.serializer import Message

# ── Basic send/receive ──


async def test_send_receive_basic(layer: SharedMemoryChannelLayer) -> None:
    """Basic send and receive should work."""
    channel = await layer.new_channel("test.")
    await layer.send(channel, cast("Message", {"type": "hello", "data": "world"}))
    msg = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
    assert msg == {"type": "hello", "data": "world"}


async def test_send_receive_fifo(layer: SharedMemoryChannelLayer) -> None:
    """Messages should be received in FIFO order."""
    channel = await layer.new_channel("test.")
    messages: list[Message] = [
        cast("Message", {"type": "msg", "seq": i}) for i in range(5)
    ]
    for msg in messages:
        await layer.send(channel, msg)

    received: list[Message] = []
    for _ in range(5):
        msg = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
        received.append(msg)

    assert received == messages


async def test_send_dict_required(layer: SharedMemoryChannelLayer) -> None:
    """send() must raise TypeError for non-dict messages."""
    channel = await layer.new_channel("test.")
    with pytest.raises(TypeError):
        await layer.send(channel, "not a dict")  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]


async def test_send_message_too_large(layer: SharedMemoryChannelLayer) -> None:
    """send() must raise MessageTooLarge for messages > 1MB."""
    channel = await layer.new_channel("test.")
    big_msg = cast("Message", {"type": "big", "data": "x" * (1024 * 1024)})
    with pytest.raises(MessageTooLarge):
        await layer.send(channel, big_msg)


async def test_channel_full(layer: SharedMemoryChannelLayer) -> None:
    """send() must raise ChannelFull when capacity is exceeded."""
    channel = "test_capacity"
    for i in range(layer.capacity):
        await layer.send(channel, cast("Message", {"type": "msg", "seq": i}))

    with pytest.raises(ChannelFull):
        await layer.send(channel, cast("Message", {"type": "overflow"}))


async def test_send_channel_name_validation(layer: SharedMemoryChannelLayer) -> None:
    """send() must reject invalid channel names."""
    with pytest.raises(TypeError):
        await layer.send("", cast("Message", {"type": "test"}))
    with pytest.raises(TypeError):
        await layer.send("invalid name with spaces", cast("Message", {"type": "test"}))


async def test_receive_channel_name_validation(layer: SharedMemoryChannelLayer) -> None:
    """receive() must reject invalid channel names."""
    with pytest.raises(TypeError):
        _ = await layer.receive("")


# ── new_channel ──


async def test_new_channel_returns_unique(layer: SharedMemoryChannelLayer) -> None:
    """new_channel() should return unique channel names."""
    channels: set[str] = set()
    for _ in range(100):
        ch = await layer.new_channel("test.")
        assert ch not in channels
        channels.add(ch)


async def test_new_channel_format(layer: SharedMemoryChannelLayer) -> None:
    """new_channel() should return names with the correct format."""
    ch = await layer.new_channel("http.request")
    assert ch.startswith("http.request.")
    assert "!" in ch


# ── Groups ──


async def test_group_add_send_discard(layer: SharedMemoryChannelLayer) -> None:
    """Group operations should work correctly."""
    channel = await layer.new_channel("test.")
    await layer.group_add("test_group", channel)
    await layer.group_send(
        "test_group", cast("Message", {"type": "group_msg", "data": "hello"})
    )
    msg = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
    assert msg == {"type": "group_msg", "data": "hello"}
    await layer.group_discard("test_group", channel)


async def test_group_send_no_channel_full(layer: SharedMemoryChannelLayer) -> None:
    """group_send() must never raise ChannelFull."""
    channel = await layer.new_channel("test.")
    await layer.group_add("test_group", channel)

    # Fill the channel
    for i in range(layer.capacity):
        await layer.send(channel, cast("Message", {"type": "fill", "seq": i}))

    # group_send should silently drop, not raise
    await layer.group_send("test_group", cast("Message", {"type": "should_drop"}))


async def test_group_send_multiple_channels(layer: SharedMemoryChannelLayer) -> None:
    """group_send() should send to all group members."""
    channels: list[str] = []
    for _ in range(3):
        ch = await layer.new_channel("test.")
        await layer.group_add("multi_group", ch)
        channels.append(ch)

    await layer.group_send("multi_group", cast("Message", {"type": "broadcast"}))

    for ch in channels:
        msg = await asyncio.wait_for(layer.receive(ch), timeout=5.0)
        assert msg == {"type": "broadcast"}


async def test_group_expiry_attribute(layer: SharedMemoryChannelLayer) -> None:
    """group_expiry must be exposed as an attribute."""
    assert hasattr(layer, "group_expiry")
    assert isinstance(layer.group_expiry, int)


async def test_extensions_attribute(layer: SharedMemoryChannelLayer) -> None:
    """extensions must include 'groups' and 'flush'."""
    assert "groups" in layer.extensions
    assert "flush" in layer.extensions


# ── Flush ──


async def test_flush(layer: SharedMemoryChannelLayer) -> None:
    """flush() should reset the layer to empty state."""
    channel = "test_flush"
    await layer.send(channel, cast("Message", {"type": "before_flush"}))
    await layer.flush()
    # After flush, sending should work (ring was reset)
    await layer.send(channel, cast("Message", {"type": "after_flush"}))


# ── Message types ──


async def test_message_types(layer: SharedMemoryChannelLayer) -> None:
    """All spec-required types should round-trip correctly."""
    channel = await layer.new_channel("test.")
    msg = cast(
        "Message",
        {
            "type": "test",
            "none_val": None,
            "bool_val": True,
            "int_val": 42,
            "float_val": 3.14,
            "str_val": "hello",
            "bytes_val": b"binary",
            "list_val": [1, 2, 3],
            "dict_val": {"nested": True},
        },
    )
    await layer.send(channel, msg)
    received = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
    assert received["type"] == "test"
    assert received["none_val"] is None
    assert received["bool_val"] is True
    assert received["int_val"] == 42
    assert received["float_val"] == pytest.approx(3.14)
    assert received["str_val"] == "hello"
    assert received["bytes_val"] == b"binary"
    assert received["list_val"] == [1, 2, 3]
    assert received["dict_val"] == {"nested": True}


async def test_tuple_becomes_list(layer: SharedMemoryChannelLayer) -> None:
    """Tuples should be encoded as lists (msgpack behavior, §8.4)."""
    channel = await layer.new_channel("test.")
    await layer.send(channel, cast("Message", {"type": "test", "data": (1, 2, 3)}))
    received = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
    assert received["data"] == [1, 2, 3]


# ── OverflowError for large ints ──


async def test_int_overflow(layer: SharedMemoryChannelLayer) -> None:
    """Integers outside 64-bit signed range should raise OverflowError."""
    channel = await layer.new_channel("test.")
    with pytest.raises(OverflowError):
        await layer.send(channel, cast("Message", {"type": "test", "val": 2**64}))
