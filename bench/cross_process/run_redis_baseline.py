#!/usr/bin/env python3
"""channels_redis baseline: same S2/S4 scenarios via Redis for comparison.

Requires local Redis on localhost:6379.
Run: python bench/cross_process/run_redis_baseline.py --scenario S2 --count 500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import statistics
import sys
import time
import uuid
from pathlib import Path

REDIS_CONFIG = {
    "hosts": [("localhost", 6379)],
    "capacity": 10000,
    "expiry": 60,
}


def receiver_creates_channel_and_recv(
    prefix: str,
    result_q: mp.Queue,
    count: int,
    channel_q: mp.Queue,
) -> None:
    """Receiver: create channel, share it, then receive count messages."""
    from channels_redis.core import RedisChannelLayer

    layer = RedisChannelLayer(prefix=prefix, **REDIS_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Receiver creates the channel (so its client_prefix matches)
    channel = loop.run_until_complete(layer.new_channel())

    # Share the channel name with the sender
    channel_q.put(channel)

    latencies = []
    for _ in range(count):
        msg = loop.run_until_complete(layer.receive(channel))
        recv_ts = time.time()
        send_ts = msg.get("_ts", recv_ts)
        latencies.append((recv_ts - send_ts) * 1_000_000)

    result_q.put(latencies)


def receiver_for_group(
    prefix: str,
    result_q: mp.Queue,
    count: int,
    channel_q: mp.Queue,
    group: str,
) -> None:
    """Receiver for S4: create channel, share it, add to group, then receive."""
    from channels_redis.core import RedisChannelLayer

    layer = RedisChannelLayer(prefix=prefix, **REDIS_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    channel = loop.run_until_complete(layer.new_channel())
    loop.run_until_complete(layer.group_add(group, channel))
    channel_q.put(channel)

    latencies = []
    for _ in range(count):
        msg = loop.run_until_complete(layer.receive(channel))
        recv_ts = time.time()
        send_ts = msg.get("_ts", recv_ts)
        latencies.append((recv_ts - send_ts) * 1_000_000)

    result_q.put(latencies)


def run_s2(prefix: str, count: int) -> dict:
    """S2: sender → receiver via a shared channel."""
    from channels_redis.core import RedisChannelLayer

    result_q: mp.Queue = mp.Queue()
    channel_q: mp.Queue = mp.Queue()

    # Start receiver — it creates the channel and shares the name
    proc = mp.Process(
        target=receiver_creates_channel_and_recv,
        args=(prefix, result_q, count, channel_q),
        daemon=True,
    )
    proc.start()

    # Wait for channel name from receiver
    try:
        channel = channel_q.get(timeout=10)
    except Exception:
        print("ERROR: receiver did not create channel in time")
        return {"scenario": "S2_redis", "error": "timeout"}

    # Sender uses a separate layer instance
    sender_layer = RedisChannelLayer(prefix=prefix, **REDIS_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    send_latencies = []
    t_start = time.time()
    for i in range(count):
        msg = {"type": "test", "seq": i, "_ts": time.time()}
        t0 = time.time()
        loop.run_until_complete(sender_layer.send(channel, msg))
        send_latencies.append((time.time() - t0) * 1_000_000)
    t_end = time.time()

    proc.join(timeout=30)
    recv_latencies = result_q.get() if not result_q.empty() else []

    total_time = t_end - t_start
    return {
        "scenario": "S2_redis",
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


def run_s4(prefix: str, n_workers: int, count: int) -> dict:
    """S4: group_send fanout to n_workers receivers."""
    from channels_redis.core import RedisChannelLayer

    group = f"{prefix}.test_group"

    result_q: mp.Queue = mp.Queue()
    channel_q: mp.Queue = mp.Queue()
    procs = []
    for _ in range(n_workers):
        p = mp.Process(
            target=receiver_for_group,
            args=(prefix, result_q, count, channel_q, group),
            daemon=True,
        )
        p.start()
        procs.append(p)

    # Wait for all receivers to create channels and join the group
    channels = []
    deadline = time.time() + 15
    while len(channels) < n_workers and time.time() < deadline:
        try:
            ch = channel_q.get(timeout=1)
            channels.append(ch)
        except Exception:
            pass

    if len(channels) < n_workers:
        print(f"WARNING: only {len(channels)}/{n_workers} receivers ready")

    time.sleep(0.2)  # Let group_add propagate

    # Sender
    sender_layer = RedisChannelLayer(prefix=prefix, **REDIS_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    t_start = time.time()
    for i in range(count):
        loop.run_until_complete(
            sender_layer.group_send(
                group, {"type": "test", "seq": i, "_ts": time.time()}
            )
        )
    t_end = time.time()

    recv_timeout = max(30, count * 0.1)
    for p in procs:
        p.join(timeout=recv_timeout)

    all_latencies = []
    while not result_q.empty():
        all_latencies.extend(result_q.get())

    total_time = t_end - t_start
    return {
        "scenario": "S4_redis",
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
    parser = argparse.ArgumentParser(description="channels_redis baseline benchmark")
    parser.add_argument("--scenario", default="S2", choices=["S2", "S4"])
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--n-workers", type=int, default=4)
    parser.add_argument("--prefix", default=f"br_{uuid.uuid4().hex[:8]}")
    args = parser.parse_args()

    # Verify Redis connection
    import redis

    try:
        r = redis.Redis(host="localhost", port=6379)
        r.ping()
    except Exception as e:
        print(f"ERROR: Cannot connect to Redis: {e}", file=sys.stderr)
        print(
            "Start Redis: docker run -d --name bench-redis -p 6379:6379 redis:7-alpine"
        )
        return 1

    if args.scenario == "S2":
        result = run_s2(args.prefix, args.count)
    else:
        result = run_s4(args.prefix, args.n_workers, args.count)

    out_dir = Path("bench/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    out_path = out_dir / f"{ts}_{args.scenario}_redis.json"
    out_path.write_text(json.dumps(result, indent=2))

    print(f"[{args.scenario}_redis] result written to {out_path}")
    print(json.dumps(result, indent=2))

    # Cleanup Redis keys
    r = redis.Redis(host="localhost", port=6379)
    for key in r.scan_iter(f"{args.prefix}*"):
        r.delete(key)

    return 0


if __name__ == "__main__":
    sys.exit(main())
