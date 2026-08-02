"""P1 layer 2: end-to-end send/receive roundtrip, vs InMemory baseline (spec §12.5)."""

from __future__ import annotations

import asyncio

from channels.layers import InMemoryChannelLayer

from channels_shm import SharedMemoryChannelLayer

# Small config for benchmarks
_BENCH_CONFIG = {
    "shm_size": 256 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
}


def test_e2e_roundtrip_shm(benchmark: object) -> None:
    """Benchmark send+receive roundtrip with SharedMemoryChannelLayer."""
    loop = asyncio.new_event_loop()
    layer = SharedMemoryChannelLayer(prefix="bench_e2e_shm", **_BENCH_CONFIG)

    async def roundtrip() -> None:
        ch = await layer.new_channel("bench.")
        await layer.send(ch, {"type": "test"})
        _ = await layer.receive(ch)

    benchmark(lambda: loop.run_until_complete(roundtrip()))  # type: ignore[union-attr]
    loop.run_until_complete(layer.close())
    layer.unlink_shm()
    loop.close()


def test_e2e_roundtrip_inmemory(benchmark: object) -> None:
    """Benchmark send+receive roundtrip with InMemoryChannelLayer."""
    loop = asyncio.new_event_loop()
    layer = InMemoryChannelLayer()

    async def roundtrip() -> None:
        ch = await layer.new_channel("bench.")
        await layer.send(ch, {"type": "test"})
        _ = await layer.receive(ch)

    benchmark(lambda: loop.run_until_complete(roundtrip()))  # type: ignore[union-attr]
    loop.close()
