"""Hypothesis strategies shared by the property tests.

Every strategy here generates only inputs the validators/layer accept, so
property tests assert acceptance (and rejection of the *complement*) without
re-deriving the grammar in each test file. The one deliberate exception is
st_tuple_bearing_values, which targets the serializer's lenient domain
(tuples inside message values): its round-trip contract is equality after
normalize_message, not exact equality.
"""

from __future__ import annotations

import string
from typing import TYPE_CHECKING, cast

from hypothesis import strategies as st

if TYPE_CHECKING:
    from channels_shm.serializer import Message

# The chars the channel/group name validators accept (regex alphabet).
_VALID_CHARS = string.ascii_letters + string.digits + ".-_"

# 64-bit signed bounds msgpack can encode; reused so basedpyright keeps the
# strategy's int type concrete (an inline -(2**63) would infer Any).
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

# ── Channel / group name strategies ──


@st.composite
def st_channel_names(draw: st.DrawFn) -> str:
    """A valid non-process-specific channel name (no '!')."""
    return draw(st.text(alphabet=list(_VALID_CHARS), min_size=1, max_size=50))


@st.composite
def st_process_specific_channels(draw: st.DrawFn) -> str:
    """A valid process-specific channel name (one '!', suffix optional)."""
    prefix = draw(st_channel_names())
    suffix = draw(st.text(alphabet=list(_VALID_CHARS), min_size=0, max_size=20))
    return f"{prefix}!{suffix}"


@st.composite
def st_receive_channel_names(draw: st.DrawFn) -> str:
    """A process-specific channel name in receive-side form (ends with '!')."""
    prefix = draw(st_channel_names())
    return f"{prefix}!"


@st.composite
def st_group_names(draw: st.DrawFn) -> str:
    """A valid group name (no '!')."""
    return draw(st.text(alphabet=list(_VALID_CHARS), min_size=1, max_size=50))


# ── Message strategies ──


@st.composite
def st_messages(draw: st.DrawFn) -> Message:
    """A valid ASGI message covering every msgpack value type.

    ints stay inside the 64-bit signed range msgpack accepts (the 2**64
    overflow path is a separate, targeted test). floats include ±inf — they
    round-trip exactly through float64 msgpack — but exclude nan: IEEE 754
    nan != nan even without serialization, so no equality property can hold.
    """
    msg_type = draw(
        st.sampled_from(
            [
                "websocket.connect",
                "websocket.receive",
                "websocket.disconnect",
                "http.request",
                "http.response.body",
                "test.message",
            ]
        )
    )
    result: dict[str, object] = {"type": msg_type}

    fields = draw(
        st.lists(
            st.sampled_from(
                ["text", "bytes", "int", "float", "code", "body", "more_body", "nested"]
            ),
            min_size=0,
            max_size=3,
        )
    )
    for field in set(fields):
        if field == "text":
            result["text"] = draw(st.text(max_size=200))
        elif field == "bytes":
            result["bytes"] = draw(st.binary(max_size=200))
        elif field == "int":
            result["int"] = draw(
                st.integers(min_value=_INT64_MIN, max_value=_INT64_MAX)
            )
        elif field == "float":
            result["float"] = draw(st.floats(allow_nan=False))
        elif field == "code":
            result["code"] = draw(st.integers(min_value=1000, max_value=4999))
        elif field == "body":
            result["body"] = draw(st.binary(max_size=200))
        elif field == "more_body":
            result["more_body"] = draw(st.booleans())
        elif field == "nested":
            result["nested"] = draw(
                st.lists(
                    st.one_of(
                        st.integers(min_value=_INT64_MIN, max_value=_INT64_MAX),
                        st.text(max_size=20),
                        st.booleans(),
                    ),
                    max_size=3,
                )
            )

    return cast("Message", result)


@st.composite
def st_simple_messages(draw: st.DrawFn) -> Message:
    """A small text/int message that must round-trip byte-for-byte.

    Distinct from :func:`st_messages`: this one only uses values msgpack
    preserves exactly (no binary normalization questions), so the round-trip
    assertion can compare with plain equality.
    """
    msg_type = draw(
        st.sampled_from(
            [
                "websocket.connect",
                "websocket.receive",
                "test.message",
            ]
        )
    )
    result: dict[str, object] = {"type": msg_type}

    if draw(st.booleans()):
        result["text"] = draw(st.text(max_size=100))
    if draw(st.booleans()):
        result["value"] = draw(st.integers(min_value=-1000, max_value=1000))

    return cast("Message", result)


@st.composite
def st_tuple_bearing_values(draw: st.DrawFn) -> object:
    """A nested value tree whose containers may be tuples (lenient domain).

    Distinct from the msgpack-exact containers of st_messages: msgpack encodes
    tuples as arrays, so the round-trip contract for this domain is equality
    after normalize_message (tuple -> list), not exact equality.
    """
    return draw(
        st.recursive(
            st.one_of(
                st.integers(min_value=_INT64_MIN, max_value=_INT64_MAX),
                st.floats(allow_nan=False),
                st.text(max_size=20),
                st.booleans(),
            ),
            lambda children: st.one_of(
                st.lists(children, max_size=3),
                st.lists(children, max_size=3).map(tuple),
                st.dictionaries(st.text(max_size=10), children, max_size=3),
            ),
            max_leaves=15,
        )
    )
