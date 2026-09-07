#!/usr/bin/env python3
"""channels_redis baseline for the same send_recv / group_fanout scenarios.

Requires a local Redis on localhost:6379.
Run: python -m bench.xproc.run_redis_baseline --scenario send_recv --count 500
"""

from __future__ import annotations

import argparse
import asyncio
import multiprocessing as mp
import sys
import time
import uuid

from channels_redis.core import (
    RedisChannelLayer,  # type: ignore[reportMissingTypeStubs]  # third-party ships no stubs
)

from bench.xproc.common import env_metadata, stats, write_result

REDIS_CONFIG = {
    "hosts": [("localhost", 6379)],
    "capacity": 10000,
    "expiry": 60,
}


def receiver_create_and_recv(
    prefix: str, result_q: mp.Queue[list[float]], count: int, channel_q: mp.Queue[str]
) -> None:
    """Create a channel (so its client_prefix matches this receiver), then receive."""
    layer = RedisChannelLayer(prefix=prefix, **REDIS_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    channel = loop.run_until_complete(layer.new_channel())
    _ = channel_q.put(channel)

    latencies: list[float] = []
    for _ in range(count):
        msg = loop.run_until_complete(layer.receive(channel))
        recv_ts = time.time()
        send_ts = float(msg.get("_ts", recv_ts))
        latencies.append((recv_ts - send_ts) * 1_000_000)
    _ = result_q.put(latencies)


def receiver_for_group(
    prefix: str,
    result_q: mp.Queue[list[float]],
    count: int,
    channel_q: mp.Queue[str],
    group: str,
) -> None:
    """Create a channel, join `group`, signal via channel_q, then receive.

    The channel is published to channel_q only after group_add completes, so
    the sender waiting on channel_q knows membership has propagated — no fixed
    sleep needed.
    """
    layer = RedisChannelLayer(prefix=prefix, **REDIS_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    channel = loop.run_until_complete(layer.new_channel())
    loop.run_until_complete(layer.group_add(group, channel))
    _ = channel_q.put(channel)

    latencies: list[float] = []
    for _ in range(count):
        msg = loop.run_until_complete(layer.receive(channel))
        recv_ts = time.time()
        send_ts = float(msg.get("_ts", recv_ts))
        latencies.append((recv_ts - send_ts) * 1_000_000)
    _ = result_q.put(latencies)


def run_send_recv(prefix: str, count: int) -> dict[str, object]:
    """Send/recv equivalent: one sender, one receiver, via Redis."""
    result_q: mp.Queue[list[float]] = mp.Queue()
    channel_q: mp.Queue[str] = mp.Queue()
    proc = mp.Process(
        target=receiver_create_and_recv,
        args=(prefix, result_q, count, channel_q),
        daemon=True,
    )
    _ = proc.start()

    try:
        channel = channel_q.get(timeout=10)
    except Exception:
        return {"scenario": "send_recv_redis", "error": "receiver timeout"}

    sender = RedisChannelLayer(prefix=prefix, **REDIS_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    send_samples: list[float] = []
    t_start = time.perf_counter()
    for i in range(count):
        msg = {"type": "test", "seq": i, "_ts": time.time()}
        t0 = time.perf_counter()
        loop.run_until_complete(sender.send(channel, msg))
        send_samples.append((time.perf_counter() - t0) * 1_000_000)
    t_end = time.perf_counter()

    proc.join(timeout=max(30, count * 0.1))
    recv_samples = result_q.get() if not result_q.empty() else []

    total = t_end - t_start
    return {
        "scenario": "send_recv_redis",
        "count": count,
        "total_time_s": round(total, 3),
        "send_throughput_qps": round(count / total) if total > 0 else 0,
        "send": stats(send_samples),
        "recv": stats(recv_samples),
        "env": env_metadata(),
    }


def run_group_fanout(prefix: str, n_workers: int, count: int) -> dict[str, object]:
    """Group fanout equivalent: group_send to n_workers receivers, via Redis."""
    group = f"{prefix}.test_group"
    result_q: mp.Queue[list[float]] = mp.Queue()
    channel_q: mp.Queue[str] = mp.Queue()
    procs: list[mp.Process] = []
    for _ in range(n_workers):
        p = mp.Process(
            target=receiver_for_group,
            args=(prefix, result_q, count, channel_q, group),
            daemon=True,
        )
        _ = p.start()
        procs.append(p)

    channels: list[str] = []
    deadline = time.time() + 15
    while len(channels) < n_workers and time.time() < deadline:
        try:
            channels.append(channel_q.get(timeout=1))
        except Exception:
            pass
    if len(channels) < n_workers:
        print(f"WARNING: only {len(channels)}/{n_workers} receivers joined")

    sender = RedisChannelLayer(prefix=prefix, **REDIS_CONFIG)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    t_start = time.perf_counter()
    for i in range(count):
        loop.run_until_complete(
            sender.group_send(group, {"type": "test", "seq": i, "_ts": time.time()})
        )
    t_end = time.perf_counter()

    for p in procs:
        p.join(timeout=max(30, count * 0.1))

    all_latencies: list[float] = []
    while not result_q.empty():
        all_latencies.extend(result_q.get())

    total = t_end - t_start
    return {
        "scenario": "group_fanout_redis",
        "count": count,
        "n_workers": n_workers,
        "total_time_s": round(total, 3),
        "send_throughput_qps": round(count / total) if total > 0 else 0,
        "recv": stats(all_latencies),
        "env": env_metadata(),
    }


def _redis_alive() -> bool:
    import redis

    try:
        r = redis.Redis(host="localhost", port=6379)
        return bool(r.ping())
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario", default="send_recv", choices=["send_recv", "group_fanout"]
    )
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--prefix", default=f"br_{uuid.uuid4().hex[:8]}")
    args = parser.parse_args()

    if not _redis_alive():
        print(
            "ERROR: cannot connect to Redis on localhost:6379. "
            "Start it: docker run -d --name bench-redis -p 6379:6379 redis:7-alpine",
            file=sys.stderr,
        )
        return 1

    if args.scenario == "send_recv":
        result = run_send_recv(args.prefix, args.count)
    else:
        result = run_group_fanout(args.prefix, args.workers, args.count)
    out_path = write_result(result, f"{args.scenario}_redis")
    print(f"[{args.scenario}_redis] result written to {out_path}")

    import redis

    r = redis.Redis(host="localhost", port=6379)
    for key in r.scan_iter(f"{args.prefix}*"):
        r.delete(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
