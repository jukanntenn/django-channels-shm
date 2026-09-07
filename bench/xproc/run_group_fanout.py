#!/usr/bin/env python3
"""Cross-process group_send fanout: one sender, N receiver processes.

Each receiver gets its own regular (non-process-specific) channel. Regular
channels are used on purpose: process-specific channels from one process share
a ring via non_local_name(), and sharing a ring across consumers causes MPMC
dequeue interference — the very thing this scenario is not measuring.

Run: python -m bench.xproc.run_group_fanout --count 500 --workers 4
"""

from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import sys
import time
import uuid
from typing import TYPE_CHECKING

from bench.common import BENCH_CONFIG
from bench.xproc.common import (
    cleanup_shm,
    env_metadata,
    receiver_worker,
    stats,
    write_result,
)
from channels_shm import SharedMemoryChannelLayer

if TYPE_CHECKING:
    from channels_shm.serializer import Message


def run_group_fanout(prefix: str, n_workers: int, count: int) -> dict[str, object]:
    """Fan one message per iteration out to `n_workers` receiver processes."""
    layer = SharedMemoryChannelLayer(prefix=prefix, **BENCH_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    group = f"{prefix}.test_group"
    channels = [f"{prefix}.fanout_ch_{i}" for i in range(n_workers)]
    for ch in channels:
        loop.run_until_complete(layer.group_add(group, ch))

    result_q: mp.Queue[list[float]] = mp.Queue()
    ready_q: mp.Queue[int] = mp.Queue()
    procs: list[mp.Process] = []
    for ch in channels:
        p = mp.Process(
            target=receiver_worker,
            args=(prefix, ch, result_q, count, ready_q),
            daemon=True,
        )
        _ = p.start()
        procs.append(p)

    deadline = time.time() + 10
    ready_count = 0
    while ready_count < n_workers and time.time() < deadline:
        try:
            _ = ready_q.get(timeout=0.5)
            ready_count += 1
        except Exception:
            pass
    if ready_count < n_workers:
        print(f"WARNING: only {ready_count}/{n_workers} receivers ready")

    t_start = time.perf_counter()
    for i in range(count):
        msg: Message = {"type": "test", "seq": i, "_ts": time.time()}
        loop.run_until_complete(layer.group_send(group, msg))
    t_end = time.perf_counter()

    for p in procs:
        p.join(timeout=max(30, count * 0.1))

    all_latencies: list[float] = []
    while not result_q.empty():
        all_latencies.extend(result_q.get())

    loop.run_until_complete(layer.close())

    total = t_end - t_start
    return {
        "scenario": "group_fanout",
        "count": count,
        "n_workers": n_workers,
        "total_time_s": round(total, 3),
        "send_throughput_qps": round(count / total) if total > 0 else 0,
        "recv": stats(all_latencies),
        "env": env_metadata(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--prefix", default=f"bench_xp_{uuid.uuid4().hex[:8]}")
    args = parser.parse_args()

    result = run_group_fanout(args.prefix, args.workers, args.count)
    out_path = write_result(result, "group_fanout")
    print(f"[group_fanout] result written to {out_path}")

    cleanup_shm(args.prefix)
    return 0


if __name__ == "__main__":
    sys.exit(main())
