#!/usr/bin/env python3
"""Cross-process send/receive scenario: one sender, one receiver process.

The receiver registers the channel with its pump and signals readiness; the
sender then streams `count` messages. Reported numbers separate the producer
side (send latency/throughput, monotonic per-op clock) from the consumer side
(receive latency, wall-clock `_ts` deltas across processes).

Run: python -m bench.xproc.run_send_recv --count 500
"""

from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import sys
import time
import uuid

from bench.common import BENCH_CONFIG
from bench.xproc.common import (
    cleanup_shm,
    env_metadata,
    receiver_worker,
    send_latencies,
    stats,
    write_result,
)
from channels_shm import SharedMemoryChannelLayer


def run_send_recv(prefix: str, count: int) -> dict[str, object]:
    """One sender process, one receiver process, on a shared regular channel."""
    channel = f"{prefix}.shared_ch"
    result_q: mp.Queue[list[float]] = mp.Queue()
    ready_q: mp.Queue[int] = mp.Queue()

    receiver = mp.Process(
        target=receiver_worker,
        args=(prefix, channel, result_q, count, ready_q),
        daemon=True,
    )
    receiver.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _ = ready_q.get(timeout=0.5)
            break
        except Exception:
            pass
    else:
        msg = "receiver did not signal readiness within 10s"
        raise RuntimeError(msg)

    layer = SharedMemoryChannelLayer(prefix=prefix, **BENCH_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    t_start = time.perf_counter()
    send_samples = send_latencies(layer, loop, channel, count)
    t_end = time.perf_counter()

    receiver.join(timeout=max(30, count * 0.1))
    recv_samples = result_q.get() if not result_q.empty() else []

    loop.run_until_complete(layer.close())

    total = t_end - t_start
    return {
        "scenario": "send_recv",
        "count": count,
        "total_time_s": round(total, 3),
        "send_throughput_qps": round(count / total) if total > 0 else 0,
        "send": stats(send_samples),
        "recv": stats(recv_samples),
        "env": env_metadata(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--prefix", default=f"bench_xp_{uuid.uuid4().hex[:8]}")
    args = parser.parse_args()

    result = run_send_recv(args.prefix, args.count)
    out_path = write_result(result, "send_recv")
    print(f"[send_recv] result written to {out_path}")

    cleanup_shm(args.prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
