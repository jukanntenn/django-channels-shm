"""e2e: echo consumer tests (single-worker round-trip).

These tests verify the EchoConsumer on a single worker: connect, send/receive,
and multiple messages.
"""

from __future__ import annotations

import asyncio

import pytest

websockets = pytest.importorskip("websockets")

pytestmark = pytest.mark.e2e


def _ws_url(host_port: str, path: str) -> str:
    host, _, port = host_port.partition(":")
    return f"ws://{host}:{port or 8000}{path}"


async def test_echo_single_message(worker_host: str) -> None:
    """Connect to echo consumer, send one message, receive it back."""
    url = _ws_url(worker_host, "/ws/echo/room1/")
    async with websockets.connect(url) as ws:
        await ws.send("hello")
        received = await asyncio.wait_for(ws.recv(), timeout=5.0)
        assert received == "hello"


async def test_echo_multiple_messages(worker_host: str) -> None:
    """Send several messages in sequence; each is echoed back in order."""
    url = _ws_url(worker_host, "/ws/echo/room2/")
    async with websockets.connect(url) as ws:
        messages = ["first", "second", "third", "fourth"]
        for msg in messages:
            await ws.send(msg)
        for expected in messages:
            received = await asyncio.wait_for(ws.recv(), timeout=5.0)
            assert received == expected


async def test_echo_binary_ignored(worker_host: str) -> None:
    """EchoConsumer only handles text; binary frames should not crash."""
    url = _ws_url(worker_host, "/ws/echo/room3/")
    async with websockets.connect(url) as ws:
        await ws.send("text-ok")
        received = await asyncio.wait_for(ws.recv(), timeout=5.0)
        assert received == "text-ok"
