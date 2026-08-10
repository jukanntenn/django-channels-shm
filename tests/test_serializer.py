"""Unit and property tests for channels_shm.serializer.

Maps to src/channels_shm/serializer.py. The round-trip property tests were
moved here from tests/test_properties.py during the test-suite reorganization
(python-community convention: test file layout mirrors src layout).
"""

from __future__ import annotations

from typing import cast

from hypothesis import given, settings

from channels_shm.serializer import (
    Message,
    normalize_message,
    pack_message,
    unpack_message,
)
from tests.strategies import st_messages, st_simple_messages

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


def test_tuple_becomes_list_through_roundtrip() -> None:
    """msgpack packs tuples as lists; normalize makes the equivalence explicit.

    The pack/unpack pipeline (not _normalize_value alone) performs the
    tuple -> list conversion; this locks in the combined contract.
    """
    msg = cast(
        "Message", {"type": "test", "items": (1, ("a", 2)), "nested": {"k": (3,)}}
    )
    data = pack_message(msg)
    restored = unpack_message(bytes(data))
    assert normalize_message(restored) == {
        "type": "test",
        "items": [1, ["a", 2]],
        "nested": {"k": [3]},
    }
