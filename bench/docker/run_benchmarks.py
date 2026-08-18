#!/usr/bin/env python3
"""Reproducible 3-way benchmark: InMemory vs channels_shm vs channels_redis.

Runs inside the bench docker container (2 CPUs / 2 GB RAM, see
bench/docker/docker-compose.yml). Orchestrates:

  1. InMemoryChannelLayer single-process send/receive roundtrip (ceiling)
  2. channels_shm  single-process send/receive roundtrip
  3. channels_shm  cross-process send/recv and group fanout
  4. channels_redis cross-process send/recv and group fanout (local redis-server)

Prints a JSON summary; the numbers are copied into README.md and
README.zh-CN.md (run with `python -O` so the release-mode layer is measured).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import subprocess
import sys
import time
import uuid

from bench.xproc.run_group_fanout import run_group_fanout
from bench.xproc.run_redis_baseline import (
    run_group_fanout as run_group_fanout_redis,
)
from bench.xproc.run_redis_baseline import run_send_recv as run_send_recv_redis
from bench.xproc.run_send_recv import run_send_recv
from channels.layers import InMemoryChannelLayer

from channels_shm import SharedMemoryChannelLayer

# A 50-byte ASGI-ish message (~inline size class): typical small chat payload.
_SMALL_MSG = {"type": "chat.message", "text": "hi", "user": "alice", "room": "r1"}

_SHM_CONFIG = {
    "shm_size": 256 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
    "capacity": 10000,
}

_ROUNDTRIPS = 5000
_CROSS_COUNT = 1000
_FANOUT_WORKERS = 4


def _wait_for_redis(timeout: float = 20.0) -> None:
    """Wait until redis-server accepts pings (started as a sidecar process)."""
    import redis

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = redis.Redis(host="localhost", port=6379)
            if r.ping():
                return
        except Exception:
            time.sleep(0.2)
    msg = "redis-server did not become ready in time"
    raise RuntimeError(msg)


def roundtrip_benchmark(make_layer: object, prefix: str) -> dict[str, float | int]:
    """Single-process send/receive roundtrip latency + throughput."""
    loop = asyncio.new_event_loop()
    layer = make_layer(prefix)
    ch = loop.run_until_complete(layer.new_channel("bench."))

    async def one() -> None:
        await layer.send(ch, _SMALL_MSG)
        _ = await layer.receive(ch)

    for _ in range(200):  # warmup (pump, wakeup, caches)
        loop.run_until_complete(one())

    samples: list[float] = []
    t_start = time.perf_counter()
    for _ in range(_ROUNDTRIPS):
        t0 = time.perf_counter()
        loop.run_until_complete(one())
        samples.append((time.perf_counter() - t0) * 1_000_000)
    t_end = time.perf_counter()

    if hasattr(layer, "unlink_shm"):
        loop.run_until_complete(layer.close())  # type: ignore[union-attr]
        layer.unlink_shm()  # type: ignore[union-attr]
    else:
        loop.run_until_complete(layer.close())  # type: ignore[union-attr]
    loop.close()

    samples.sort()
    return {
        "roundtrips": _ROUNDTRIPS,
        "ops_per_sec": round(_ROUNDTRIPS / (t_end - t_start)),
        "latency_mean_us": round(statistics.mean(samples), 1),
        "latency_p50_us": round(samples[_ROUNDTRIPS // 2], 1),
        "latency_p99_us": round(samples[int(_ROUNDTRIPS * 0.99) - 1], 1),
    }


def _inmemory_layer(_prefix: str) -> InMemoryChannelLayer:
    return InMemoryChannelLayer()


def _shm_layer(prefix: str) -> SharedMemoryChannelLayer:
    return SharedMemoryChannelLayer(prefix=prefix, **_SHM_CONFIG)


def main() -> int:
    redis_server = shutil.which("redis-server")
    if redis_server is None:
        msg = "redis-server not found on PATH (install redis-server in the image)"
        raise RuntimeError(msg)
    subprocess.run(  # noqa: S603 - S607 handled via shutil.which
        [redis_server, "--daemonize", "yes", "--save", "", "--appendonly", "no"],
        check=True,
    )
    _wait_for_redis()

    prefix = f"bench_{uuid.uuid4().hex[:8]}"
    results: dict[str, object] = {
        "environment": {
            "container": "docker compose (bench/docker/docker-compose.yml)",
            "cpus": 2,
            "memory_gb": 2,
            "python": sys.version.split()[0],
            "mode": "release (python -O)",
        },
        "scenarios": {},
    }

    results["scenarios"]["roundtrip_single_process"] = {
        "in_memory": roundtrip_benchmark(_inmemory_layer, prefix),
        "channels_shm": roundtrip_benchmark(_shm_layer, prefix),
    }

    results["scenarios"]["send_recv_cross_process"] = {
        "channels_shm": run_send_recv(f"{prefix}_shm", _CROSS_COUNT),
        "channels_redis": run_send_recv_redis(f"{prefix}_rd", _CROSS_COUNT),
    }

    results["scenarios"]["group_fanout"] = {
        "channels_shm": run_group_fanout(
            f"{prefix}_shm", _FANOUT_WORKERS, _CROSS_COUNT
        ),
        "channels_redis": run_group_fanout_redis(
            f"{prefix}_rd", _FANOUT_WORKERS, _CROSS_COUNT
        ),
    }

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
