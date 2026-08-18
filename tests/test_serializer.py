"""Unit and property tests for channels_shm.serializer.

Maps to src/channels_shm/serializer.py. Round-trip properties cover every
msgpack value type the layer accepts, tuple-bearing lenient inputs are pinned
to normalize-equality, and the boundary tests lock down the 64-bit integer
limit that overflow corruption depends on.
"""

from __future__ import annotations

from typing import cast

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from channels_shm.serializer import (
    Message,
    normalize_message,
    pack_message,
    unpack_message,
)
from tests.strategies import (
    st_messages,
    st_simple_messages,
    st_tuple_bearing_values,
)

# ── Serialization round-trip ──

# bytes() drops the zero-copy packer buffer export immediately: hypothesis
# keeps failing examples' tracebacks (and thus these locals) alive, which
# would otherwise make every later pack call raise BufferError (see
# pack_message's "caller must consume or copy" note).


@example(
    msg={"type": "test.message", "float": float("inf")}
)  # pin ±inf through the full message pipeline
@given(msg=st_messages())
def test_msgpack_roundtrip(msg: Message) -> None:
    """Messages should survive pack/unpack round-trip (normalized)."""
    data = bytes(pack_message(msg))
    restored = unpack_message(data)
    assert normalize_message(restored) == normalize_message(msg)


@given(msg=st_simple_messages())
def test_simple_message_roundtrip(msg: Message) -> None:
    """Simple messages should round-trip exactly."""
    data = bytes(pack_message(msg))
    restored = unpack_message(data)
    assert restored == msg


def test_tuple_becomes_list_through_roundtrip() -> None:
    """msgpack packs tuples as lists; normalize makes the equivalence explicit.

    The pack/unpack pipeline (not _normalize_value alone) performs the
    tuple -> list conversion; this locks in the combined contract.
    """
    msg = cast(
        "Message", {"type": "test", "items": (1, ("a", 2)), "nested": {"k": (3,)}}
    )
    data = bytes(pack_message(msg))
    restored = unpack_message(data)
    assert normalize_message(restored) == {
        "type": "test",
        "items": [1, ["a", 2]],
        "nested": {
            "k": [
                3,
            ]
        },
    }


@example(value=(1,))  # regression pin: tuples must normalize to their list form
@given(value=st_tuple_bearing_values())
def test_lenient_tuple_roundtrip(value: object) -> None:
    """Tuple-bearing values round-trip as their list form (normalize equality).

    MessageValue (the documented domain) has no tuples, but the msgpack path
    accepts them (encoded as arrays). This pins the lenient contract:
    unpack(pack(x)) == normalize(x) for any nested x.
    """
    msg = cast("Message", {"type": "test", "data": value})
    data = bytes(pack_message(msg))
    restored = unpack_message(data)
    assert normalize_message(restored) == normalize_message(msg)


# ── Value-type boundaries ──


@given(value=st.integers(min_value=-(2**63), max_value=2**63 - 1))
def test_int64_roundtrip(value: int) -> None:
    """Any 64-bit signed integer survives the round-trip exactly."""
    data = bytes(pack_message({"type": "test", "v": value}))
    restored = unpack_message(data)
    assert restored["v"] == value


@example(value=float("inf"))  # boundary pins: ±inf encode as float64 infinities
@example(value=float("-inf"))
@given(value=st.floats(allow_nan=False))
def test_float_roundtrip(value: float) -> None:
    """Any non-nan float — including ±inf — survives the round-trip exactly."""
    data = bytes(pack_message({"type": "test", "v": value}))
    restored = unpack_message(data)
    assert restored["v"] == value


@given(value=st.binary())
def test_binary_roundtrip(value: bytes) -> None:
    """Arbitrary bytes survive the round-trip (msgpack bin type)."""
    data = bytes(pack_message({"type": "test", "v": value}))
    restored = unpack_message(data)
    assert restored["v"] == value


@given(
    value=st.recursive(
        st.one_of(
            st.integers(min_value=-(2**63), max_value=2**63 - 1),
            st.text(max_size=20),
            st.booleans(),
        ),
        lambda children: (
            st.lists(children, max_size=3)
            | st.dictionaries(st.text(max_size=10), children, max_size=3)
        ),
        max_leaves=30,
    )
)
def test_nested_structure_roundtrip(value: object) -> None:
    """Deeply nested lists/dicts round-trip without loss."""
    msg = cast("Message", {"type": "test", "data": value})
    data = bytes(pack_message(msg))
    restored = unpack_message(data)
    assert restored["data"] == value


def test_pack_overflow_int64() -> None:
    """Integers above the 64-bit signed range must raise OverflowError."""
    with pytest.raises(OverflowError):
        _ = pack_message({"type": "test", "v": 2**64})


def test_pack_negative_overflow_int64() -> None:
    with pytest.raises(OverflowError):
        _ = pack_message({"type": "test", "v": -(2**63) - 1})
