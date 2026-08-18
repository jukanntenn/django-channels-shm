"""End-to-end send+receive roundtrip on one reused channel.

The channel is created once by the fixture: in production a channel lives
across many messages, so creating one per iteration would measure uuid
generation instead of the operation under test.

Calibration mode is safe here because every iteration consumes what it sends —
the ring never accumulates. The pump drain (wakeup read, ring dequeue, unpack,
buffer deliver) happens inside the timed region while receive awaits, so the
roundtrip number is the true user-visible latency including delivery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bench.common import TIERS
from bench.py.conftest import roundtrip

if TYPE_CHECKING:
    import asyncio

    from channels.layers import InMemoryChannelLayer
    from pytest_benchmark.fixture import BenchmarkFixture

    from bench.py.conftest import LayerEnv


@pytest.mark.benchmark(group="roundtrip")
@pytest.mark.parametrize("tier", ["websocket_event", "http_request_100kb"])
def test_roundtrip_shm(benchmark: BenchmarkFixture, env: LayerEnv, tier: str) -> None:
    """Send+receive one message end to end on the shm layer."""
    loop, layer, channel = env
    benchmark(roundtrip, loop, layer, channel, TIERS[tier])


@pytest.mark.benchmark(group="roundtrip")
def test_roundtrip_inmemory(
    benchmark: BenchmarkFixture,
    inmemory_env: tuple[asyncio.AbstractEventLoop, InMemoryChannelLayer, str],
) -> None:
    """InMemory baseline for the same roundtrip shape."""
    loop, layer, channel = inmemory_env
    benchmark(roundtrip, loop, layer, channel, TIERS["websocket_event"])
