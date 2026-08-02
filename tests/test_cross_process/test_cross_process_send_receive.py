"""Cross-process send/receive (Layer A, slow marker)."""

from __future__ import annotations

import multiprocessing as mp  # noqa: TC003
from typing import Any

import pytest

from tests.test_cross_process._workers import consumer_worker, producer_worker

pytestmark = pytest.mark.slow


def test_one_producer_one_consumer(
    xproc_prefix: str,
    xproc_ctx: mp.context.BaseContext,
    xproc_cleanup: None,  # noqa: ARG001
) -> None:
    """Process A sends 20 messages; process B receives all 20 in order."""
    channel = "xproc.channel"
    queue: mp.Queue[tuple[str, Any]] = xproc_ctx.Queue()

    consumer = xproc_ctx.Process(
        target=consumer_worker,
        args=(xproc_prefix, channel, 20, queue, "consumer"),
    )
    producer = xproc_ctx.Process(
        target=producer_worker,
        args=(xproc_prefix, channel, 20, queue, "producer"),
    )
    consumer.start()
    producer.start()
    consumer.join(timeout=30)
    producer.join(timeout=30)

    assert not consumer.is_alive(), "consumer timed out"
    assert not producer.is_alive(), "producer timed out"
    assert consumer.exitcode == 0, f"consumer exit {consumer.exitcode}"
    assert producer.exitcode == 0, f"producer exit {producer.exitcode}"

    results = {}
    while not queue.empty():
        wid, payload = queue.get()
        results[wid] = payload

    assert "error" not in results.get("producer", {}), results["producer"]
    assert "error" not in results.get("consumer", {}), results["consumer"]
    received = results["consumer"]["received"]
    assert len(received) == 20
    assert [m["seq"] for m in received] == list(range(20))


def test_multi_producer_one_consumer(
    xproc_prefix: str,
    xproc_ctx: mp.context.BaseContext,
    xproc_cleanup: None,  # noqa: ARG001
) -> None:
    """3 producers x 10 messages each -> consumer receives 30 total."""
    channel = "xproc.multi"
    queue: mp.Queue[tuple[str, Any]] = xproc_ctx.Queue()

    consumer = xproc_ctx.Process(
        target=consumer_worker,
        args=(xproc_prefix, channel, 30, queue, "consumer"),
    )
    producers = [
        xproc_ctx.Process(
            target=producer_worker,
            args=(xproc_prefix, channel, 10, queue, f"p{i}"),
        )
        for i in range(3)
    ]
    consumer.start()
    for p in producers:
        p.start()
    consumer.join(timeout=60)
    for p in producers:
        p.join(timeout=60)

    assert consumer.exitcode == 0
    assert all(p.exitcode == 0 for p in producers)

    results = {}
    while not queue.empty():
        wid, payload = queue.get()
        results[wid] = payload

    for wid in ("p0", "p1", "p2", "consumer"):
        assert "error" not in results.get(wid, {}), results[wid]
    assert len(results["consumer"]["received"]) == 30
