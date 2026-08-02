#!/usr/bin/env python3
"""P1 layer 3: cross-process end-to-end benchmarks.

Run: python bench/cross_process/run_cross_process.py --count 500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

BENCH_CONFIG = {
    "shm_size": 256 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 10,
    "max_processes": 16,
    "max_members_per_group": 64,
    "capacity": 10000,
}


def receiver_worker(
    prefix: str, channel: str, result_q: mp.Queue, count: int, ready_q: mp.Queue
) -> None:
    """Worker: register the channel with the pump, signal ready, then receive.

    The ready_q handshake (B-04) replaces the old time.sleep(0.3) guess: the
    sender waits for this signal before sending, so no early messages are lost
    on slow machines/CI.
    """
    from channels_shm import SharedMemoryChannelLayer

    layer = SharedMemoryChannelLayer(prefix=prefix, **BENCH_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Pre-register the channel with the pump (watch_channel does the initial
    # drain) and ensure the ring exists, BEFORE signaling readiness — so the
    # sender's first message lands in a ring the pump is already watching.
    pump = layer._pump
    assert pump is not None
    pump.watch_channel(channel)
    from channels_shm.channel.manager import non_local_name

    ring_key = non_local_name(channel)
    if layer._channel_mgr.get_ring(ring_key) is None:
        with layer._lock:
            layer._channel_mgr.get_or_create_ring(channel, layer.get_capacity(channel))
    ready_q.put(os.getpid())

    latencies = []
    for _ in range(count):
        msg = loop.run_until_complete(layer.receive(channel))
        recv_ts = time.time()
        send_ts = msg.get("_ts", recv_ts)
        latencies.append((recv_ts - send_ts) * 1_000_000)  # us

    loop.run_until_complete(layer.close())
    result_q.put(latencies)


def run_benchmark(prefix: str, count: int) -> dict:
    """Run cross-process send/recv benchmark using a shared channel."""
    from channels_shm import SharedMemoryChannelLayer

    # Use a fixed shared channel name (no '!' = non-process-specific)
    channel = f"{prefix}.shared_ch"

    result_q: mp.Queue = mp.Queue()
    ready_q: mp.Queue = mp.Queue()

    # Start receiver in subprocess FIRST
    proc = mp.Process(
        target=receiver_worker,
        args=(prefix, channel, result_q, count, ready_q),
        daemon=True,
    )
    proc.start()
    # B-04: wait for the receiver to signal ready (registered + watching),
    # instead of a hardcoded 0.3s sleep that races on slow machines/CI.
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _ = ready_q.get(timeout=0.5)
            break
        except Exception:
            pass

    # Sender
    layer = SharedMemoryChannelLayer(prefix=prefix, **BENCH_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    send_latencies = []
    t_start = time.time()
    for i in range(count):
        msg = {"type": "test", "seq": i, "_ts": time.time()}
        t0 = time.time()
        loop.run_until_complete(layer.send(channel, msg))
        send_latencies.append((time.time() - t0) * 1_000_000)
    t_end = time.time()

    proc.join(timeout=30)
    recv_latencies = result_q.get() if not result_q.empty() else []
    loop.run_until_complete(layer.close())

    total_time = t_end - t_start
    return {
        "scenario": "cross_process",
        "count": count,
        "total_time_s": round(total_time, 3),
        "send_throughput_qps": round(count / total_time) if total_time > 0 else 0,
        "send_latency_p50_us": round(statistics.median(send_latencies), 1)
        if send_latencies
        else 0,
        "send_latency_p99_us": round(
            sorted(send_latencies)[int(len(send_latencies) * 0.99)], 1
        )
        if send_latencies
        else 0,
        "recv_latency_p50_us": round(statistics.median(recv_latencies), 1)
        if recv_latencies
        else 0,
        "recv_latency_p99_us": round(
            sorted(recv_latencies)[int(len(recv_latencies) * 0.99)], 1
        )
        if recv_latencies
        else 0,
    }


def receiver_worker_with_ready(
    prefix: str, channel: str, result_q: mp.Queue, count: int, ready_q: mp.Queue
) -> None:
    """Worker: register channel with pump, signal ready, then receive count messages."""
    from channels_shm import SharedMemoryChannelLayer

    layer = SharedMemoryChannelLayer(prefix=prefix, **BENCH_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Pre-register the channel with the pump without blocking on receive.
    # This calls watch_channel() which registers the channel and does initial drain.
    pump = layer._pump
    assert pump is not None
    pump.watch_channel(channel)
    # Ensure ring exists for this channel
    from channels_shm.channel.manager import non_local_name

    ring_key = non_local_name(channel)
    if layer._channel_mgr.get_ring(ring_key) is None:
        with layer._lock:
            layer._channel_mgr.get_or_create_ring(channel, layer.get_capacity(channel))

    # Signal that this receiver is ready
    ready_q.put(os.getpid())

    # Now receive messages
    latencies = []
    for _ in range(count):
        msg = loop.run_until_complete(layer.receive(channel))
        recv_ts = time.time()
        send_ts = msg.get("_ts", recv_ts)
        latencies.append((recv_ts - send_ts) * 1_000_000)

    loop.run_until_complete(layer.close())
    result_q.put(latencies)


def run_group_benchmark(prefix: str, n_workers: int, count: int) -> dict:
    """Run group_send fan-out benchmark.

    Uses regular (non-process-specific) channels so each receiver gets
    its own ring buffer. Process-specific channels from the same process
    share a ring via non_local_name(), which causes cross-consumer
    interference in MPMC dequeue.
    """
    from channels_shm import SharedMemoryChannelLayer

    layer = SharedMemoryChannelLayer(prefix=prefix, **BENCH_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    group = f"{prefix}.test_group"

    # Use regular (non-process-specific) channels — each gets its own ring
    channels = [f"{prefix}.fanout_ch_{i}" for i in range(n_workers)]
    for ch in channels:
        loop.run_until_complete(layer.group_add(group, ch))

    # Start receivers with readiness signaling
    result_q: mp.Queue = mp.Queue()
    ready_q: mp.Queue = mp.Queue()
    procs = []
    for ch in channels:
        p = mp.Process(
            target=receiver_worker_with_ready,
            args=(prefix, ch, result_q, count, ready_q),
            daemon=True,
        )
        p.start()
        procs.append(p)

    # Wait for all receivers to be ready (registered with pump)
    deadline = time.time() + 10
    ready_count = 0
    while ready_count < n_workers and time.time() < deadline:
        try:
            ready_q.get(timeout=0.5)
            ready_count += 1
        except Exception:
            pass

    if ready_count < n_workers:
        print(f"WARNING: only {ready_count}/{n_workers} receivers ready")

    # group_send — no batching/pauses needed. C-03 fixed the wakeup fan-out
    # (was: _wakeup_broadcast to ALL processes per member; now: each owning
    # process is woken once per group_send via a precomputed client_prefix →
    # socket map). The previous 10ms inter-batch pause (which masked an EAGAIN
    # wakeup-flooding bug) has been removed (B-03); this benchmark now reflects
    # true group_send throughput.
    t_start = time.time()
    for i in range(count):
        loop.run_until_complete(
            layer.group_send(group, {"type": "test", "seq": i, "_ts": time.time()})
        )
    t_end = time.time()

    recv_timeout = max(30, count * 0.1)
    for p in procs:
        p.join(timeout=recv_timeout)

    all_latencies = []
    while not result_q.empty():
        all_latencies.extend(result_q.get())

    loop.run_until_complete(layer.close())

    total_time = t_end - t_start
    return {
        "scenario": "group_fanout",
        "count": count,
        "n_workers": n_workers,
        "total_time_s": round(total_time, 3),
        "send_throughput_qps": round(count / total_time) if total_time > 0 else 0,
        "recv_latency_p50_us": round(statistics.median(all_latencies), 1)
        if all_latencies
        else 0,
        "recv_latency_p99_us": round(
            sorted(all_latencies)[int(len(all_latencies) * 0.99)], 1
        )
        if len(all_latencies) > 0
        else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="S2", choices=["S2", "S4"])
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--n-workers", type=int, default=4)
    parser.add_argument("--prefix", default=f"bench_xp_{uuid.uuid4().hex[:8]}")
    args = parser.parse_args()

    if args.scenario == "S2":
        result = run_benchmark(args.prefix, args.count)
    else:
        result = run_group_benchmark(args.prefix, args.n_workers, args.count)

    out_dir = Path("bench/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    out_path = out_dir / f"{ts}_{args.scenario}.json"
    out_path.write_text(json.dumps(result, indent=2))

    print(f"[{args.scenario}] result written to {out_path}")
    print(json.dumps(result, indent=2))

    # Cleanup
    import glob
    import shutil

    for f in glob.glob(f"/dev/shm/{args.prefix}*"):
        try:
            if os.path.isdir(f):
                shutil.rmtree(f)
            else:
                os.unlink(f)
        except OSError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
