"""group_send fanout: serialize once + N enqueues + deduplicated wakeups.

Members are process-specific channels owned by this process, so the wakeup
dedup path applies: all owners are the same, so one group_send produces a
single local wakeup regardless of member count.

Members are deliberately not watched: an unwatched channel means the pump has
nothing to drain, so no consumer-side work bleeds into the timed region (the
receive benchmark measures that side). The flip side is that rings only fill —
group_send skips full members without erroring — so the enqueue budget is
bounded (pedantic rounds) and asserted to stay below capacity.

InMemoryChannelLayer is the baseline for the same fanout shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bench.common import BENCH_CONFIG, TIERS
from bench.py.conftest import group_send

if TYPE_CHECKING:
    import asyncio

    from channels.layers import InMemoryChannelLayer
    from pytest_benchmark.fixture import BenchmarkFixture

    from bench.py.conftest import LayerEnv

MEMBERS = 8
ROUNDS = 1000
WARMUP_ROUNDS = 20


@pytest.mark.benchmark(group="group")
def test_group_send_shm(benchmark: BenchmarkFixture, env: LayerEnv) -> None:
    """Fan out one small message to 8 members owned by this process."""
    loop, layer, _channel = env
    group = "bench.group"
    for _ in range(MEMBERS):
        member = loop.run_until_complete(layer.new_channel("bench."))
        loop.run_until_complete(layer.group_add(group, member))
    assert BENCH_CONFIG["capacity"] > (ROUNDS + WARMUP_ROUNDS) * MEMBERS, (
        "group fanout budget must stay below ring capacity"
    )
    benchmark.pedantic(
        group_send,
        args=(loop, layer, group, TIERS["websocket_event"]),
        rounds=ROUNDS,
        warmup_rounds=WARMUP_ROUNDS,
    )


@pytest.mark.benchmark(group="group")
def test_group_send_inmemory(
    benchmark: BenchmarkFixture,
    inmemory_env: tuple[asyncio.AbstractEventLoop, InMemoryChannelLayer, str],
) -> None:
    """InMemory baseline for the same fanout shape."""
    loop, layer, _channel = inmemory_env
    group = "bench.group"
    for _ in range(MEMBERS):
        member = loop.run_until_complete(layer.new_channel("bench."))
        loop.run_until_complete(layer.group_add(group, member))
    benchmark.pedantic(
        group_send,
        args=(loop, layer, group, TIERS["websocket_event"]),
        rounds=ROUNDS,
        warmup_rounds=WARMUP_ROUNDS,
    )
