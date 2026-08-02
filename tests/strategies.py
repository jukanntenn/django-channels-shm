"""Hypothesis strategies for property-based testing."""

from __future__ import annotations

import string
from typing import TYPE_CHECKING

from hypothesis import strategies as st

if TYPE_CHECKING:
    from channels_shm.serializer import Message, MessageValue

# ── Channel name strategies ──

# Valid channel name characters (§6.3)
_VALID_CHARS = string.ascii_letters + string.digits + ".-_"


@st.composite
def st_channel_names(draw: st.DrawFn) -> str:
    """Generate a valid channel name (without !)."""
    length = draw(st.integers(min_value=1, max_value=50))
    return draw(st.text(alphabet=list(_VALID_CHARS), min_size=1, max_size=length))


@st.composite
def st_process_specific_channels(draw: st.DrawFn) -> str:
    """Generate a valid process-specific channel name (with !)."""
    prefix = draw(st_channel_names())
    local = draw(st.text(alphabet=list(_VALID_CHARS), min_size=1, max_size=20))
    return f"{prefix}!{local}"


@st.composite
def st_group_names(draw: st.DrawFn) -> str:
    """Generate a valid group name."""
    length = draw(st.integers(min_value=1, max_value=50))
    return draw(st.text(alphabet=list(_VALID_CHARS), min_size=1, max_size=length))


# ── Message strategies ──


@st.composite
def st_messages(draw: st.DrawFn) -> Message:
    """Generate a valid ASGI message dict."""
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
    result: dict[str, MessageValue] = {"type": msg_type}

    # Add some optional fields
    fields = draw(
        st.lists(
            st.sampled_from(["text", "bytes", "code", "body", "more_body"]),
            min_size=0,
            max_size=3,
        )
    )
    for field in set(fields):
        if field == "text":
            result["text"] = draw(st.text(max_size=200))
        elif field == "bytes":
            result["bytes"] = draw(st.binary(max_size=200))
        elif field == "code":
            result["code"] = draw(st.integers(min_value=1000, max_value=4999))
        elif field == "body":
            result["body"] = draw(st.binary(max_size=200))
        elif field == "more_body":
            result["more_body"] = draw(st.booleans())

    return result


@st.composite
def st_simple_messages(draw: st.DrawFn) -> Message:
    """Generate simple messages (no binary, for easier comparison)."""
    msg_type = draw(
        st.sampled_from(
            [
                "websocket.connect",
                "websocket.receive",
                "test.message",
            ]
        )
    )
    result: dict[str, MessageValue] = {"type": msg_type}

    if draw(st.booleans()):
        result["text"] = draw(st.text(max_size=100))
    if draw(st.booleans()):
        result["value"] = draw(st.integers(min_value=-1000, max_value=1000))

    return result
