"""L2: Property-based tests using hypothesis."""

from __future__ import annotations

from typing import cast

import pytest
from hypothesis import given, settings

from channels_shm.serializer import (
    Message,
    normalize_message,
    pack_message,
    unpack_message,
)
from tests.strategies import st_channel_names, st_messages, st_simple_messages

# ── Serialization round-trip ──


@given(msg=st_messages())
@settings(max_examples=100)
def test_msgpack_roundtrip(msg: Message) -> None:
    """Messages should survive pack/unpack round-trip (normalized)."""
    data = pack_message(msg)
    restored = unpack_message(bytes(data))
    assert normalize_message(restored) == normalize_message(msg)


@given(msg=st_simple_messages())
@settings(max_examples=100)
def test_simple_message_roundtrip(msg: Message) -> None:
    """Simple messages should round-trip exactly."""
    data = pack_message(msg)
    restored = unpack_message(bytes(data))
    assert restored == msg


# ── Channel name validation ──


@given(name=st_channel_names())
@settings(max_examples=50)
def test_valid_channel_name_accepted(name: str) -> None:
    """Valid channel names should pass validation."""
    from channels_shm.channel.validator import validate_channel_name

    validate_channel_name(name)  # Should not raise


# ── Capacity ──


async def test_capacity_equivalence() -> None:
    """Capacity should be enforced correctly."""
    import uuid

    from channels_shm import SharedMemoryChannelLayer

    prefix = f"test_cap_{uuid.uuid4().hex[:8]}"
    layer = SharedMemoryChannelLayer(
        capacity=5,
        prefix=prefix,
        shm_size=8 * 1024 * 1024,
        max_channels=50,
        max_groups=10,
        max_processes=4,
        watchdog_interval=None,
    )
    try:
        channel = "test_capacity_prop"
        for i in range(5):
            await layer.send(channel, cast("Message", {"type": "msg", "i": i}))

        from channels_shm.exceptions import ChannelFull

        with pytest.raises(ChannelFull):
            await layer.send(channel, cast("Message", {"type": "overflow"}))
    finally:
        await layer.close()
        layer.unlink_shm()
