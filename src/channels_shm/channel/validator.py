"""Channel and group name validation, reusing BaseChannelLayer."""

from __future__ import annotations

import re

# Channel name validation regex (from channels BaseChannelLayer).
# Per spec §6.3 / V-01: ASCII letters, digits, `.`, `-`, `_`, `!` (process-specific,
# at most one). Note: upstream channels (layers.py:147) does NOT allow `?`
# (single-reader) at all, and neither does this layer — `?` was a footgun
# (the old regex allowed multiple `?` per name). `!` is structurally limited to
# one by the optional single group `(\![...]*)?`, and `!` is not in any char
# class, matching upstream exactly.
#
# Length bound: 128, the native in-shm name field width (SLOT_CHANNEL_NAME /
# CH_SLOT_NAME / MEMBER_CHANNEL_NAME are all 128 bytes). The old bound (200)
# let names through that the ring silently truncated (message misrouting) and
# that channel_index_create wrote past the 128-byte field into the next slot.
_CHANNEL_NAME_RE = re.compile(r"^[a-zA-Z\d\-_.]+(\![a-zA-Z\d\-_.]*)?$")
_GROUP_NAME_RE = re.compile(r"^[a-zA-Z\d\-_.]+$")
_NAME_MAX_LEN = 128


def validate_channel_name(name: str, *, receive: bool = False) -> None:
    """Validate a channel name per the channels spec."""
    if not name:
        msg = "Channel name must not be empty"
        raise TypeError(msg)
    if len(name) > _NAME_MAX_LEN:
        msg = f"Channel name too long: {len(name)} > {_NAME_MAX_LEN}"
        raise TypeError(msg)
    if not _CHANNEL_NAME_RE.match(name):
        msg = f"Channel name invalid: {name!r}"
        raise TypeError(msg)
    if "!" in name and not name.endswith("!") and receive:
        msg = f"Channel name with ! must end with ! when receive=True: {name!r}"
        raise TypeError(msg)


def validate_group_name(name: str) -> None:
    """Validate a group name per the channels spec."""
    if not name:
        msg = "Group name must not be empty"
        raise TypeError(msg)
    if len(name) > _NAME_MAX_LEN:
        msg = f"Group name too long: {len(name)} > {_NAME_MAX_LEN}"
        raise TypeError(msg)
    if not _GROUP_NAME_RE.match(name):
        msg = f"Group name invalid: {name!r}"
        raise TypeError(msg)
