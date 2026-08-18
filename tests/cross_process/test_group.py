"""Cross-process group broadcast (Layer A, slow marker)."""

from __future__ import annotations

import multiprocessing as mp  # noqa: TC003
from typing import Any

import pytest

from tests.cross_process._workers import group_member_worker, group_sender_worker

pytestmark = pytest.mark.slow


def test_group_broadcast_cross_process(
    xproc_prefix: str,
    xproc_ctx: mp.context.BaseContext,
    xproc_cleanup: None,  # noqa: ARG001
) -> None:
    """2 members in different processes each receive 5 broadcasts from a 3rd process.

    Members receive on non-process-specific channels (no '!'), since a
    process-specific channel can only be received by its owning process (L-02).
    """
    group = "xproc.group"
    ch_a = f"{xproc_prefix}.member_a"
    ch_b = f"{xproc_prefix}.member_b"
    queue: mp.Queue[tuple[str, Any]] = xproc_ctx.Queue()

    member_a = xproc_ctx.Process(
        target=group_member_worker,
        args=(xproc_prefix, group, ch_a, 5, queue, "member_a"),
    )
    member_b = xproc_ctx.Process(
        target=group_member_worker,
        args=(xproc_prefix, group, ch_b, 5, queue, "member_b"),
    )
    sender = xproc_ctx.Process(
        target=group_sender_worker,
        args=(xproc_prefix, group, 5, queue, "sender"),
    )
    # Start members first so they join before the sender broadcasts.
    member_a.start()
    member_b.start()
    # Give members time to group_add. A short sleep is acceptable here.
    import time

    time.sleep(1.0)
    sender.start()

    member_a.join(timeout=40)
    member_b.join(timeout=40)
    sender.join(timeout=20)

    assert member_a.exitcode == 0
    assert member_b.exitcode == 0
    assert sender.exitcode == 0

    results = {}
    while not queue.empty():
        wid, payload = queue.get()
        results[wid] = payload

    for wid in ("member_a", "member_b", "sender"):
        assert "error" not in results.get(wid, {}), results[wid]
    assert len(results["member_a"]["received"]) == 5
    assert len(results["member_b"]["received"]) == 5
    # Both members see the same broadcast sequence.
    seqs_a = [m["seq"] for m in results["member_a"]["received"]]
    seqs_b = [m["seq"] for m in results["member_b"]["received"]]
    assert seqs_a == seqs_b == [0, 1, 2, 3, 4]
