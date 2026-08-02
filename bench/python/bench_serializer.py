"""P1 layer 1: serializer microbenchmarks (pack/unpack at ASGI size tiers).

H-01 note: pack_message returns a memoryview into the per-task Packer's
internal buffer (zero-copy until the next pack/reset on the same task). To
exercise the full pack path without leaking buffer exports across iterations
(which would make the next reset() raise BufferError), the pack benchmarks
release the view by copying it to bytes — the same cost a non-zero-copy
consumer would pay. The unpack benchmark operates on stable bytes.
"""

from __future__ import annotations

from bench.python.conftest import ASGI_MESSAGES  # type: ignore[import-not-found]
from channels_shm.serializer import pack_message, unpack_message


def _pack_and_release(msg: object) -> None:
    """Pack a message and release the returned buffer export (copy to bytes).

    Mirrors what a consumer that cannot take the memoryview zero-copy path
    would do; keeps the per-task Packer reusable across iterations.
    """
    _ = bytes(pack_message(msg))  # type: ignore[arg-type]


def test_pack_small(benchmark: object) -> None:
    msg = ASGI_MESSAGES["small_50b"]
    benchmark(_pack_and_release, msg)  # type: ignore[union-attr]


def test_pack_medium(benchmark: object) -> None:
    msg = ASGI_MESSAGES["medium_256b"]
    benchmark(_pack_and_release, msg)  # type: ignore[union-attr]


def test_pack_overflow(benchmark: object) -> None:
    msg = ASGI_MESSAGES["overflow_100kb"]
    benchmark(_pack_and_release, msg)  # type: ignore[union-attr]


def test_unpack_small(benchmark: object) -> None:
    msg = ASGI_MESSAGES["small_50b"]
    data = bytes(pack_message(msg))  # type: ignore[arg-type]
    benchmark(unpack_message, data)  # type: ignore[union-attr]
