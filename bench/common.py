"""Shared benchmark configuration and message tiers (single source of truth).

Every benchmark (pytest-benchmark suite and cross-process scripts) uses these
constants so measurements stay comparable: the same layer config, the same
message shapes, the same capacity budget.
"""

from __future__ import annotations

from typing import TypedDict

from channels_shm.serializer import Message, pack_message


class BenchLayerConfig(TypedDict):
    """Layer constructor kwargs used by every benchmark (single source).

    Typed so `**BENCH_CONFIG` spreads into SharedMemoryChannelLayer.__init__
    with exact key/value checking, instead of a blind dict.
    """

    shm_size: int
    inline_size: int
    max_channels: int
    max_groups: int
    max_processes: int
    max_members_per_group: int
    capacity: int
    watchdog_interval: int | None


# watchdog_interval=None keeps the event loop free of periodic tasks so they
# cannot skew timings.
BENCH_CONFIG: BenchLayerConfig = {
    "shm_size": 256 * 1024 * 1024,
    "inline_size": 512,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
    "capacity": 10000,
    "watchdog_interval": None,
}

# Message tiers: the name states the product semantics and the storage class
# it must exercise (test_tiers.py asserts the classes stay truthful):
# - websocket_event          small chat event, fits a ring slot inline
# - http_request_boundary    largest message still under the 512B inline cutoff
# - http_request_100kb       slab-backed overflow path
# - http_request_1mb         near the 1MB size limit (serializer tier only)
TIERS: dict[str, Message] = {
    "websocket_event": {"type": "websocket.connect"},
    "http_request_boundary": {"type": "http.request", "body": "x" * 460},
    "http_request_100kb": {"type": "http.request", "body": "x" * 100_000},
    "http_request_1mb": {"type": "http.request", "body": "x" * 1_000_000},
}

# Tiers the layer-level benchmarks cover. The 1MB tier is serializer-only:
# on the send path it takes the same slab-overflow route as 100KB, so it adds
# no information while inflating runtime and slab memory.
LAYER_TIERS = ("websocket_event", "http_request_boundary", "http_request_100kb")
SERIALIZER_TIERS = (
    "websocket_event",
    "http_request_boundary",
    "http_request_100kb",
    "http_request_1mb",
)


def packed_sizes() -> dict[str, int]:
    """Encoded size per tier (used by test_tiers.py to keep names truthful)."""
    return {name: len(pack_message(message)) for name, message in TIERS.items()}
