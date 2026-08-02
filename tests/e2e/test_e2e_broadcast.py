"""e2e: broadcast (group) consumer tests.

Tests cross-worker group broadcast, same-worker broadcast, multi-message
ordering, and room isolation via the BroadcastConsumer.
"""

from __future__ import annotations

import asyncio

import pytest

websockets = pytest.importorskip("websockets")

pytestmark = pytest.mark.e2e


def _ws_url(host_port: str, path: str) -> str:
    host, _, port = host_port.partition(":")
    return f"ws://{host}:{port or 8000}{path}"


async def test_broadcast_cross_worker(worker_hosts: list[str]) -> None:
    """Client A on worker[0] broadcasts; client B on worker[1] receives it."""
    room = "cross"
    url_a = _ws_url(worker_hosts[0], f"/ws/broadcast/{room}/")
    url_b = _ws_url(worker_hosts[1], f"/ws/broadcast/{room}/")

    async with (
        websockets.connect(url_b) as client_b,
        websockets.connect(url_a) as client_a,
    ):
        # Give client_b time to join the group.
        await asyncio.sleep(1.0)
        await client_a.send("hello-cross-worker")
        try:
            received = await asyncio.wait_for(client_b.recv(), timeout=10.0)
        except TimeoutError:
            pytest.fail("client B did not receive broadcast within 10s")
        assert received == "hello-cross-worker"


async def test_broadcast_same_worker(worker_host: str) -> None:
    """Two clients on the same worker: one broadcasts, the other receives."""
    room = "same-worker"
    url_a = _ws_url(worker_host, f"/ws/broadcast/{room}/")
    url_b = _ws_url(worker_host, f"/ws/broadcast/{room}/")

    async with (
        websockets.connect(url_b) as client_b,
        websockets.connect(url_a) as client_a,
    ):
        await asyncio.sleep(1.0)
        await client_a.send("same-worker-msg")
        received = await asyncio.wait_for(client_b.recv(), timeout=10.0)
        assert received == "same-worker-msg"


async def test_broadcast_multiple_messages(worker_hosts: list[str]) -> None:
    """Sender broadcasts multiple messages; receiver gets them all in order."""
    room = "multi-msg"
    url_sender = _ws_url(worker_hosts[0], f"/ws/broadcast/{room}/")
    url_recv = _ws_url(worker_hosts[1], f"/ws/broadcast/{room}/")

    async with (
        websockets.connect(url_recv) as receiver,
        websockets.connect(url_sender) as sender,
    ):
        await asyncio.sleep(1.0)
        messages = ["msg-0", "msg-1", "msg-2", "msg-3", "msg-4"]
        for msg in messages:
            await sender.send(msg)
        for expected in messages:
            received = await asyncio.wait_for(receiver.recv(), timeout=10.0)
            assert received == expected


async def test_broadcast_room_isolation(worker_hosts: list[str]) -> None:
    """Messages in room-A don't leak to room-B."""
    url_a1 = _ws_url(worker_hosts[0], "/ws/broadcast/roomA/")
    url_a2 = _ws_url(worker_hosts[1], "/ws/broadcast/roomA/")
    url_b1 = _ws_url(worker_hosts[0], "/ws/broadcast/roomB/")

    async with (
        websockets.connect(url_a2) as client_a2,
        websockets.connect(url_a1) as client_a1,
        websockets.connect(url_b1) as client_b1,
    ):
        await asyncio.sleep(1.0)
        # Send in room A
        await client_a1.send("room-a-message")
        # room-A member should receive it
        received = await asyncio.wait_for(client_a2.recv(), timeout=10.0)
        assert received == "room-a-message"
        # room-B member should NOT receive it (give a short window)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client_b1.recv(), timeout=2.0)


async def test_broadcast_multiple_receivers(worker_hosts: list[str]) -> None:
    """One sender, two receivers on different workers both get the message."""
    room = "multi-recv"
    url_sender = _ws_url(worker_hosts[0], f"/ws/broadcast/{room}/")
    url_recv1 = _ws_url(worker_hosts[1], f"/ws/broadcast/{room}/")
    url_recv2 = _ws_url(worker_hosts[2 % len(worker_hosts)], f"/ws/broadcast/{room}/")

    async with (
        websockets.connect(url_recv1) as receiver1,
        websockets.connect(url_recv2) as receiver2,
        websockets.connect(url_sender) as sender,
    ):
        await asyncio.sleep(1.0)
        await sender.send("broadcast-to-many")
        r1 = await asyncio.wait_for(receiver1.recv(), timeout=10.0)
        r2 = await asyncio.wait_for(receiver2.recv(), timeout=10.0)
        assert r1 == "broadcast-to-many"
        assert r2 == "broadcast-to-many"
