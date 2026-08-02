"""Message serialization using msgpack."""

from __future__ import annotations

import contextvars
from typing import TypeAlias, cast

import msgpack

# Per §11.2: Message type aliases (recursive to avoid Any).
# MessageValue covers all spec-allowed ASGI message value types.
MessageValue: TypeAlias = (
    str
    | bytes
    | int
    | float
    | bool
    | list["MessageValue"]
    | dict[str, "MessageValue"]
    | None
)
Message: TypeAlias = dict[str, MessageValue]

# Per-task Packer for zero-copy serialization (§6.1).
# Each asyncio task gets its own Packer to prevent cross-send buffer accumulation.
_packer_var: contextvars.ContextVar[msgpack.Packer] = contextvars.ContextVar(
    "shm_packer"
)


def _get_packer() -> msgpack.Packer:
    """Get or create the per-task Packer instance."""
    try:
        return _packer_var.get()
    except LookupError:
        p = msgpack.Packer(
            use_bin_type=True,
            autoreset=False,
            datetime=False,
        )
        _ = _packer_var.set(p)
        return p


def pack_message(message: Message) -> memoryview:
    """Pack a message dict into a memoryview (zero-copy from msgpack internal buffer).

    Args:
        message: The ASGI message dict.

    Returns:
        A memoryview of the packed bytes. The caller must consume or copy
        before the next pack call on the same task.

    Raises:
        OverflowError: If message contains integers outside 64-bit signed range.
    """
    packer = _get_packer()
    packer.reset()
    packer.pack(message)
    return packer.getbuffer()


def unpack_message(data: bytes) -> Message:
    """Unpack a msgpack-encoded message.

    Args:
        data: The raw msgpack bytes.

    Returns:
        The message dict.
    """
    return cast("Message", msgpack.unpackb(data, raw=False))


def normalize_message(message: Message) -> Message:
    """Normalize a message for testing: convert tuples to lists recursively."""
    return cast("Message", _normalize_value(message))


def _normalize_value(value: MessageValue) -> MessageValue:
    """Recursively normalize a value (tuple → list)."""
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _normalize_value(v) for k, v in value.items()}
    return value
