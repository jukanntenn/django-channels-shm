"""P1 layer 1: send inline vs overflow (validates spec §5.2 '95%+ zero-alloc')."""

from __future__ import annotations

import asyncio

from bench.python.conftest import ASGI_MESSAGES  # type: ignore[import-not-found]

# Config for benchmarks
_BENCH_CONFIG = {
    "shm_size": 256 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
    "capacity": 10000,
}


def test_send_inline_50b(benchmark: object) -> None:
    """Benchmark send of a small inline message (50B)."""
    loop = asyncio.new_event_loop()
    layer = __import__("channels_shm").SharedMemoryChannelLayer(
        prefix="bench_send_50b", **_BENCH_CONFIG
    )
    ch = loop.run_until_complete(layer.new_channel("bench."))

    async def _send() -> None:
        await layer.send(ch, ASGI_MESSAGES["small_50b"])

    benchmark(lambda: loop.run_until_complete(_send()))  # type: ignore[union-attr]
    loop.run_until_complete(layer.close())
    layer.unlink_shm()
    loop.close()


def test_send_overflow_100kb(benchmark: object) -> None:
    """Benchmark send of a large overflow message (100KB)."""
    loop = asyncio.new_event_loop()
    layer = __import__("channels_shm").SharedMemoryChannelLayer(
        prefix="bench_send_100k", **_BENCH_CONFIG
    )
    ch = loop.run_until_complete(layer.new_channel("bench."))

    async def _send() -> None:
        await layer.send(ch, ASGI_MESSAGES["overflow_100kb"])

    benchmark(lambda: loop.run_until_complete(_send()))  # type: ignore[union-attr]
    loop.run_until_complete(layer.close())
    layer.unlink_shm()
    loop.close()
