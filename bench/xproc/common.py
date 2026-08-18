"""Shared pieces for the cross-process scenario benchmarks.

Each scenario spawns real receiver processes against one shared-memory region
and reports a latency/throughput JSON snapshot to bench/results/. The ready
handshake replaces fixed sleeps: the sender waits until every receiver has
registered its channel with the pump, so no message is sent before the
delivery machinery is in place — fixed sleeps raced on slow CI machines and
lost early messages.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast

from bench.common import BENCH_CONFIG
from channels_shm import SharedMemoryChannelLayer
from channels_shm.channel.manager import non_local_name

if TYPE_CHECKING:
    import multiprocessing as mp

    from channels_shm.serializer import Message


def receiver_worker(
    prefix: str,
    channel: str,
    result_q: mp.Queue[list[float]],
    count: int,
    ready_q: mp.Queue[int],
) -> None:
    """Register the channel with the pump, signal readiness, then receive `count` messages.

    The registration must happen before the sender starts: watch_channel does
    the initial drain and ring creation, so the sender's first message lands in
    a ring the pump is already watching.
    """
    layer = SharedMemoryChannelLayer(prefix=prefix, **BENCH_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    pump = layer._pump
    channel_mgr = layer._channel_mgr
    lock = layer._lock
    assert pump is not None
    assert channel_mgr is not None
    assert lock is not None
    _ = pump.watch_channel(channel)
    ring_key = non_local_name(channel)
    if channel_mgr.get_ring(ring_key) is None:
        with lock:
            _ = channel_mgr.get_or_create_ring(channel, layer.get_capacity(channel))
    _ = ready_q.put(os.getpid())

    latencies: list[float] = []
    for _ in range(count):
        msg = loop.run_until_complete(layer.receive(channel))
        recv_ts = time.time()
        send_ts = cast("float", msg.get("_ts", recv_ts))
        latencies.append((recv_ts - send_ts) * 1_000_000)  # us

    loop.run_until_complete(layer.close())
    _ = result_q.put(latencies)


def cleanup_shm(prefix: str) -> None:
    """Remove the shared-memory region and wakeup sockets created for a prefix."""
    for f in glob.glob(f"/dev/shm/{prefix}*"):
        try:
            if os.path.isdir(f):
                shutil.rmtree(f)
            else:
                os.unlink(f)
        except OSError:
            pass


def percentile(samples: list[float], pct: float) -> float:
    """p-th percentile of sorted samples (empty list -> 0)."""
    if not samples:
        return 0.0
    return sorted(samples)[min(len(samples) - 1, int(len(samples) * pct))]


def env_metadata() -> dict[str, object]:
    """Machine / build context recorded with every snapshot for traceability."""
    return {
        "cpu_model": _cpu_model(),
        "cpu_count": os.cpu_count(),
        "python": sys.version.split()[0],
        "commit": _git_rev(),
        "mode": "release (python -O)" if sys.flags.optimize else "dev",
    }


def write_result(result: dict[str, object], scenario: str) -> Path:
    """Write one scenario snapshot to bench/results/ and print it."""
    out_dir = Path("bench/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"{scenario}_{stamp}.json"
    _ = out_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return out_path


def send_latencies(
    layer: SharedMemoryChannelLayer,
    loop: asyncio.AbstractEventLoop,
    channel: str,
    count: int,
) -> list[float]:
    """Send `count` messages, sampling per-op wall time with a monotonic clock.

    Per-op samples use perf_counter (monotonic, immune to NTP slew). The
    in-message `_ts` timestamp stays on wall-clock time.time() because it is
    compared across processes, which have no shared monotonic origin.
    """
    latencies: list[float] = []
    for i in range(count):
        msg: Message = {"type": "test", "seq": i, "_ts": time.time()}
        t0 = time.perf_counter()
        loop.run_until_complete(layer.send(channel, msg))
        latencies.append((time.perf_counter() - t0) * 1_000_000)
    return latencies


def stats(samples: list[float]) -> dict[str, float]:
    """Summary stats for a latency sample list (0 for empty)."""
    if not samples:
        return {"median_us": 0.0, "p99_us": 0.0}
    return {
        "median_us": round(statistics.median(samples), 1),
        "p99_us": round(percentile(samples, 0.99), 1),
    }


def _cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return os.uname().machine


def _git_rev() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        rev = subprocess.run(  # noqa: S603 - S607 handled via shutil.which
            [git, "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if rev.returncode == 0:
            return rev.stdout.strip()
    except OSError:
        pass
    return "unknown"
