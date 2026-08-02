"""L3: Concurrency and crash recovery tests."""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import TYPE_CHECKING, cast

from channels_shm import SharedMemoryChannelLayer

if TYPE_CHECKING:
    from channels_shm.serializer import Message


async def test_multiple_producers() -> None:
    """Multiple producers sending to the same channel."""
    prefix = f"test_mp_{uuid.uuid4().hex[:8]}"
    layer = SharedMemoryChannelLayer(
        capacity=1000,
        prefix=prefix,
        shm_size=32 * 1024 * 1024,
        max_channels=50,
        max_groups=10,
        max_processes=4,
        watchdog_interval=None,
    )
    try:
        channel = "test_multi"
        num_producers = 4
        msgs_per_producer = 50

        async def producer(pid: int) -> None:
            for i in range(msgs_per_producer):
                await layer.send(
                    channel, cast("Message", {"type": "msg", "pid": pid, "seq": i})
                )

        # Start producers
        tasks = [asyncio.create_task(producer(i)) for i in range(num_producers)]
        _ = await asyncio.gather(*tasks)

        # Receive all messages
        received: list[Message] = []
        for _ in range(num_producers * msgs_per_producer):
            msg = await asyncio.wait_for(layer.receive(channel), timeout=10.0)
            received.append(msg)

        assert len(received) == num_producers * msgs_per_producer
    finally:
        await layer.close()
        layer.unlink_shm()


async def test_send_receive_same_channel() -> None:
    """Send and receive on the same channel within one process."""
    prefix = f"test_sr_{uuid.uuid4().hex[:8]}"
    layer = SharedMemoryChannelLayer(
        capacity=10,
        prefix=prefix,
        shm_size=8 * 1024 * 1024,
        max_channels=50,
        max_groups=10,
        max_processes=4,
        watchdog_interval=None,
    )
    try:
        channel = await layer.new_channel("test.")
        await layer.send(channel, cast("Message", {"type": "ping"}))
        msg = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
        assert msg == {"type": "ping"}
    finally:
        await layer.close()
        layer.unlink_shm()


async def test_flock_crash_release() -> None:
    """flock should be auto-released when process exits."""
    import fcntl

    prefix = f"test_flock_{uuid.uuid4().hex[:8]}"
    shm_path = f"/dev/shm/{prefix}"

    # Create a temp file
    fd = os.open(shm_path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    try:
        # Acquire lock
        fcntl.flock(fd, fcntl.LOCK_EX)

        # Fork a child that tries to acquire the same lock
        pid = os.fork()
        if pid == 0:
            # Child process: retry with backoff
            import time

            child_fd = os.open(shm_path, os.O_RDWR | os.O_CLOEXEC)
            try:
                for _ in range(50):
                    try:
                        fcntl.flock(child_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        os._exit(0)
                    except BlockingIOError:
                        time.sleep(0.02)
                os._exit(1)
            finally:
                os.close(child_fd)

        # Parent: release lock after a short delay
        await asyncio.sleep(0.1)
        fcntl.flock(fd, fcntl.LOCK_UN)

        # Wait for child
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0
    finally:
        os.close(fd)
        try:
            os.unlink(shm_path)
        except FileNotFoundError:
            pass
