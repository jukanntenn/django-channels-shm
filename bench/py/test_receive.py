"""Consumer-side receive path: wakeup read + pump drain + unpack + deliver.

Setup enqueues one message via layer.send, then the timed receive runs the
loop: the wakeup fires, the pump drains the ring into the channel buffer, and
receive returns. This is the machinery that separates this layer from the
InMemory/Redis baselines, measured without producer-side work.

Why the drain lands deterministically inside the timed region: send runs to
completion with no await points, so the loop never polls the eventfd during
setup; only the timed receive's await lets the wakeup callback run — one drain
per round, exactly once.

The InMemory baseline receives straight from an asyncio.Queue — there is no
pump — so the difference between the two rows is the pump delivery cost.
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import pytest

from bench.common import BENCH_CONFIG, TIERS
from bench.py.conftest import receive, send

if TYPE_CHECKING:
    import asyncio

    from channels.layers import InMemoryChannelLayer
    from pytest_benchmark.fixture import BenchmarkFixture

    from bench.py.conftest import LayerEnv

ROUNDS = 2000
WARMUP_ROUNDS = 20


@pytest.mark.benchmark(group="receive")
@pytest.mark.parametrize("tier", ["websocket_event", "http_request_100kb"])
def test_receive_shm(benchmark: BenchmarkFixture, env: LayerEnv, tier: str) -> None:
    """Receive one message through the pump, enqueued by the per-round setup."""
    loop, layer, channel = env
    message = TIERS[tier]
    assert BENCH_CONFIG["capacity"] > ROUNDS + WARMUP_ROUNDS, (
        "receive budget must stay below ring capacity"
    )
    setup = partial(send, loop, layer, channel, message)
    benchmark.pedantic(
        receive,
        args=(loop, layer, channel),
        setup=setup,
        rounds=ROUNDS,
        warmup_rounds=WARMUP_ROUNDS,
    )


@pytest.mark.benchmark(group="receive")
def test_receive_inmemory(
    benchmark: BenchmarkFixture,
    inmemory_env: tuple[asyncio.AbstractEventLoop, InMemoryChannelLayer, str],
) -> None:
    """InMemory receive baseline: direct queue get, no pump."""
    loop, layer, channel = inmemory_env
    message = TIERS["websocket_event"]
    setup = partial(send, loop, layer, channel, message)
    benchmark.pedantic(
        receive,
        args=(loop, layer, channel),
        setup=setup,
        rounds=ROUNDS,
        warmup_rounds=WARMUP_ROUNDS,
    )
