"""Pytest-benchmark fixtures for channels_shm (P1 layer 1-2)."""

from __future__ import annotations

# P2 ASGI real message size tiers (spec §5.2: ASGI messages mostly 50-500B msgpack)
ASGI_MESSAGES: dict[str, dict] = {
    "small_50b": {"type": "websocket.connect"},
    "medium_256b": {"type": "http.request", "body": "x" * 200},
    "inline_boundary_512b": {"type": "http.request", "body": "x" * 460},
    "overflow_100kb": {"type": "http.request", "body": "x" * 100_000},
    "max_1mb": {"type": "http.request", "body": "x" * 1_000_000},
}
