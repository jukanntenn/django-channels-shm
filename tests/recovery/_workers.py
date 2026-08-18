"""Worker entry points for recovery tests that spawn child processes.

These functions run in spawned child processes (multiprocessing spawn
re-imports the parent module by name), so they must live in a module importable
from the child. `_project_root` is inserted into sys.path because pytest's
importlib import mode does not put the repo root there for console-script
invocations; the child re-imports `tests.recovery._workers` by name.
"""

from __future__ import annotations

import sys
from pathlib import Path

_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def crash_victim(prefix: str, channel: str) -> None:
    """Create a layer, register on `channel`, then exit via SIGKILL (no cleanup)."""
    import asyncio
    import os
    import time

    from channels_shm import SharedMemoryChannelLayer

    layer = SharedMemoryChannelLayer(
        prefix=prefix,
        capacity=100,
        shm_size=16 * 1024 * 1024,
        max_channels=100,
        max_groups=50,
        max_processes=16,
        max_members_per_group=64,
        watchdog_interval=None,
    )
    # Force registration by creating + sending to a channel.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(layer.send(channel, {"type": "here"}))
    # Signal readiness to parent, then hang until killed.
    with open(f"/dev/shm/{prefix}_victim_pid", "w") as f:
        _ = f.write(str(os.getpid()))
    while True:
        time.sleep(0.5)


def victim_holds_slot(prefix: str, channel: str) -> None:
    """Become the owner of a ring slot, then hang (to be SIGKILL'd mid-ownership)."""
    import asyncio
    import os
    import time

    from channels_shm import SharedMemoryChannelLayer

    layer = SharedMemoryChannelLayer(
        prefix=prefix,
        capacity=4,
        shm_size=16 * 1024 * 1024,
        max_channels=10,
        max_groups=5,
        max_processes=8,
        max_members_per_group=4,
        watchdog_interval=None,
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Enqueue to take slot ownership.
    loop.run_until_complete(layer.send(channel, {"type": "victim"}))
    # Signal readiness, then hang.
    with open(f"/dev/shm/{prefix}_herd_ready", "w") as f:
        _ = f.write(str(os.getpid()))
    while True:
        time.sleep(0.5)
