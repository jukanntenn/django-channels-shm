"""Shared fixtures and environment metadata for the single-process suite.

Timing caveat (applies to every benchmark in this directory): pytest-benchmark
drives a sync callable, so each timed iteration wraps one operation in
loop.run_until_complete — task creation plus one loop step costs ~2-5us. The
InMemory baseline pays the same overhead, so the shared cost cancels out in
relative comparison and only inflates absolute numbers.

Type-checking note: benchmark.pedantic has an untyped library signature, so
basedpyright flags reportUnknownMemberType at the call sites; those few
diagnostics are absorbed in the type-check baseline (the library ships no
types for pedantic, and the rule is not suppressible via type: ignore).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import TYPE_CHECKING, Protocol

import pytest
from channels.layers import InMemoryChannelLayer

from bench.common import BENCH_CONFIG
from channels_shm import SharedMemoryChannelLayer

if TYPE_CHECKING:
    from collections.abc import Iterator

    from channels_shm.serializer import Message

    LayerEnv = tuple[asyncio.AbstractEventLoop, SharedMemoryChannelLayer, str]


class SendReceiveLayer(Protocol):
    """Minimal surface the benchmark helpers drive (shm and InMemory both satisfy it)."""

    async def send(self, channel: str, message: Message) -> None: ...

    async def receive(self, channel: str) -> Message: ...

    async def group_send(self, group: str, message: Message) -> None: ...


@pytest.fixture
def env() -> Iterator[LayerEnv]:
    """Fresh loop, shm layer and one reusable channel, cleaned up on exit.

    A unique prefix per test keeps layers isolated from stale state left by
    interrupted runs. The channel is created once and reused because channel
    creation is lifecycle work, not per-message work: production channels
    outlive many messages, so creating one per iteration would measure uuid
    generation instead of the operation under test.
    """
    loop = asyncio.new_event_loop()
    prefix = f"bench_{uuid.uuid4().hex[:8]}"
    layer = SharedMemoryChannelLayer(prefix=prefix, **BENCH_CONFIG)
    channel = loop.run_until_complete(layer.new_channel("bench."))
    try:
        yield loop, layer, channel
    finally:
        loop.run_until_complete(layer.close())
        layer.unlink_shm()
        loop.close()


@pytest.fixture
def inmemory_env() -> Iterator[
    tuple[asyncio.AbstractEventLoop, InMemoryChannelLayer, str]
]:
    """InMemory baseline with the same capacity budget as the shm layer.

    The capacity matters: with the default (100) the group baseline would
    raise ChannelFull long before the shm side, breaking the comparison.
    """
    loop = asyncio.new_event_loop()
    layer = InMemoryChannelLayer(capacity=BENCH_CONFIG["capacity"])
    channel = loop.run_until_complete(layer.new_channel("bench."))
    try:
        yield loop, layer, channel
    finally:
        loop.close()


def send(
    loop: asyncio.AbstractEventLoop,
    layer: SendReceiveLayer,
    channel: str,
    message: Message,
) -> None:
    """Synchronously send one message (the single timed unit for send benches)."""
    loop.run_until_complete(layer.send(channel, message))


def receive(
    loop: asyncio.AbstractEventLoop, layer: SendReceiveLayer, channel: str
) -> None:
    """Synchronously receive one message (the single timed unit for receive benches)."""
    _ = loop.run_until_complete(layer.receive(channel))


def roundtrip(
    loop: asyncio.AbstractEventLoop,
    layer: SendReceiveLayer,
    channel: str,
    message: Message,
) -> None:
    """Send then receive on one channel — the end-to-end user-visible latency."""
    loop.run_until_complete(layer.send(channel, message))
    _ = loop.run_until_complete(layer.receive(channel))


def group_send(
    loop: asyncio.AbstractEventLoop,
    layer: SendReceiveLayer,
    group: str,
    message: Message,
) -> None:
    """Fan one message out to every member of a group."""
    loop.run_until_complete(layer.group_send(group, message))


def pytest_benchmark_update_machine_info(
    config: object, machine_info: dict[str, object]
) -> None:
    """Record the CPU so saved runs from different machines are not compared blindly."""
    _ = config
    machine_info["cpu_model"] = _cpu_model()
    machine_info["cpu_count"] = os.cpu_count()


def pytest_benchmark_update_commit_info(
    config: object, commit_info: dict[str, object]
) -> None:
    """Record whether the run measured the release or the dev build.

    Benchmarks are only meaningful with the release build (python -O disables
    the observability block inside the layer); the flag makes the build mode
    visible in every saved run instead of being a silent assumption.
    """
    _ = config
    commit_info["mode"] = "release (python -O)" if sys.flags.optimize else "dev"


def pytest_benchmark_update_json(
    config: object, benchmarks: object, output_json: dict[str, object]
) -> None:
    """Embed the exact layer config in saved runs for reproducibility."""
    _ = config, benchmarks
    output_json["layer_config"] = BENCH_CONFIG


def _cpu_model() -> str:
    """Best-effort CPU model name (used by the machine_info hook above)."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return os.uname().machine
