"""Serializer hot path: pack / unpack across every message tier.

pack_message returns a memoryview into the per-task packer buffer; msgpack is
configured with autoreset=False, so a second pack on the same task while the
previous view is still exported raises BufferError. The benchmark copies the
view to bytes — the same copy the ring write performs on the send path — which
also keeps the packer reusable across iterations.

The unpack side consumes plain bytes, so it needs no such workaround.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from bench.common import SERIALIZER_TIERS, TIERS
from channels_shm.serializer import Message, pack_message, unpack_message

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture


def _pack_and_release(message: Message) -> None:
    _ = bytes(pack_message(message))


def _unpack(data: bytes) -> None:
    _ = unpack_message(data)


@pytest.mark.benchmark(group="serializer")
@pytest.mark.parametrize("tier", SERIALIZER_TIERS)
def test_pack(benchmark: BenchmarkFixture, tier: str) -> None:
    """Serialize one message; every tier is state-free so calibration mode is safe."""
    benchmark(_pack_and_release, TIERS[tier])


@pytest.mark.benchmark(group="serializer")
@pytest.mark.parametrize("tier", SERIALIZER_TIERS)
def test_unpack(benchmark: BenchmarkFixture, tier: str) -> None:
    """Deserialize one message from its packed bytes (prepared outside the timer)."""
    data = bytes(pack_message(TIERS[tier]))
    benchmark(_unpack, data)
