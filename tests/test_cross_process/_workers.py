"""Worker entry points for cross-process tests.

These functions run in spawned child processes. They MUST be importable
(multiprocessing spawn re-imports the module), so they live at module level.
Each worker reports results/errors to a multiprocessing.Queue.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Ensure project root is on sys.path so multiprocessing spawn can import tests.*
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

if TYPE_CHECKING:
    import multiprocessing as mp


def _make_loop() -> asyncio.AbstractEventLoop:
    """Create and set a new event loop for the worker."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def producer_worker(
    prefix: str,
    channel: str,
    messages: int,
    result_q: mp.Queue[tuple[str, Any]],
    worker_id: str,
) -> None:
    """Send `messages` to `channel` on a layer bound to `prefix`."""
    from channels_shm import SharedMemoryChannelLayer

    loop = _make_loop()
    try:
        layer = SharedMemoryChannelLayer(
            prefix=prefix,
            capacity=1000,
            shm_size=32 * 1024 * 1024,
            max_channels=100,
            max_groups=50,
            max_processes=16,
            max_members_per_group=64,
            watchdog_interval=None,
        )
        for i in range(messages):
            loop.run_until_complete(
                layer.send(channel, {"type": "msg", "src": worker_id, "seq": i})
            )
        loop.run_until_complete(layer.close())
        result_q.put((worker_id, {"sent": messages}))
    except Exception:
        result_q.put((worker_id, {"error": traceback.format_exc()}))
    finally:
        loop.close()


def consumer_worker(
    prefix: str,
    channel: str,
    expected: int,
    result_q: mp.Queue[tuple[str, Any]],
    worker_id: str,
) -> None:
    """Receive `expected` messages from `channel`."""
    from channels_shm import SharedMemoryChannelLayer

    loop = _make_loop()
    try:
        layer = SharedMemoryChannelLayer(
            prefix=prefix,
            capacity=1000,
            shm_size=32 * 1024 * 1024,
            max_channels=100,
            max_groups=50,
            max_processes=16,
            max_members_per_group=64,
            watchdog_interval=None,
        )
        received: list[dict[str, Any]] = []
        for _ in range(expected):
            msg = loop.run_until_complete(
                asyncio.wait_for(layer.receive(channel), timeout=10.0)
            )
            received.append(msg)
        loop.run_until_complete(layer.close())
        result_q.put((worker_id, {"received": received}))
    except Exception:
        result_q.put((worker_id, {"error": traceback.format_exc()}))
    finally:
        loop.close()


def group_member_worker(
    prefix: str,
    group: str,
    channel: str,
    expected: int,
    result_q: mp.Queue[tuple[str, Any]],
    worker_id: str,
) -> None:
    """Join `group` on `channel`, receive `expected` broadcasts."""
    from channels_shm import SharedMemoryChannelLayer

    loop = _make_loop()
    try:
        layer = SharedMemoryChannelLayer(
            prefix=prefix,
            capacity=1000,
            shm_size=32 * 1024 * 1024,
            max_channels=100,
            max_groups=50,
            max_processes=16,
            max_members_per_group=64,
            watchdog_interval=None,
        )
        loop.run_until_complete(layer.group_add(group, channel))
        received: list[dict[str, Any]] = []
        for _ in range(expected):
            msg = loop.run_until_complete(
                asyncio.wait_for(layer.receive(channel), timeout=10.0)
            )
            received.append(msg)
        loop.run_until_complete(layer.close())
        result_q.put((worker_id, {"received": received}))
    except Exception:
        result_q.put((worker_id, {"error": traceback.format_exc()}))
    finally:
        loop.close()


def group_sender_worker(
    prefix: str,
    group: str,
    count: int,
    result_q: mp.Queue[tuple[str, Any]],
    worker_id: str,
) -> None:
    """Broadcast `count` messages to `group`."""
    from channels_shm import SharedMemoryChannelLayer

    loop = _make_loop()
    try:
        layer = SharedMemoryChannelLayer(
            prefix=prefix,
            capacity=1000,
            shm_size=32 * 1024 * 1024,
            max_channels=100,
            max_groups=50,
            max_processes=16,
            max_members_per_group=64,
            watchdog_interval=None,
        )
        for i in range(count):
            loop.run_until_complete(
                layer.group_send(group, {"type": "broadcast", "seq": i})
            )
        loop.run_until_complete(layer.close())
        result_q.put((worker_id, {"sent": count}))
    except Exception:
        result_q.put((worker_id, {"error": traceback.format_exc()}))
    finally:
        loop.close()
