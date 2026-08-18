"""Producer-side send path: serialize + validate + enqueue + wakeup write.

No consumer runs here (the channel is never watched), so nothing ever drains
the ring. Total sends must therefore stay below ring capacity: past that
point a send stops being a microsecond enqueue and becomes the emergency-drain
retry path (multi-millisecond sleeps, then ChannelFull). Rounds are fixed
(pedantic mode, one send per round) so the total is deterministic, not a side
effect of timer calibration that drifts as the machine gets faster.

Overflow-tier rounds are also capped against the slab: overflow messages
allocate 100KB each and are never freed without a consumer, so the total
enqueue budget must stay far below shm_size too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bench.common import BENCH_CONFIG, LAYER_TIERS, TIERS
from bench.py.conftest import send

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture

    from bench.py.conftest import LayerEnv

# Rounds per tier (pedantic runs one send per round, so rounds == total sends).
ROUNDS = {
    "websocket_event": 2000,
    "http_request_boundary": 2000,
    "http_request_100kb": 500,
}
WARMUP_ROUNDS = 20

# Overflow slab budget: 520 sends x 100KB ~= 52MB, comfortably under shm_size.
assert (ROUNDS["http_request_100kb"] + WARMUP_ROUNDS) * 100_000 < BENCH_CONFIG[
    "shm_size"
]


@pytest.mark.benchmark(group="send")
@pytest.mark.parametrize("tier", LAYER_TIERS)
def test_send(benchmark: BenchmarkFixture, env: LayerEnv, tier: str) -> None:
    """Send one message with no consumer; bounded rounds keep the ring from filling.

    The ring is created by the first warmup send (cold path under flock), so
    every timed round measures the warm enqueue only.
    """
    loop, layer, channel = env
    rounds = ROUNDS[tier]
    assert rounds + WARMUP_ROUNDS < BENCH_CONFIG["capacity"], (
        "send budget must stay below ring capacity"
    )
    benchmark.pedantic(
        send,
        args=(loop, layer, channel, TIERS[tier]),
        rounds=rounds,
        warmup_rounds=WARMUP_ROUNDS,
    )
