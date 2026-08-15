"""Headless cross-process acceptance run (the release-checklist smoke test).

Spawns N worker processes. Every worker creates its OWN SharedMemoryChannelLayer
(fresh mmap of the same region + its own AF_UNIX wakeup socket) and joins a
shared group; the parent then group_sends M messages and every worker must
receive all of them. That fan-out across processes is exactly what this
library exists for, so this command is also how a freshly built or freshly
installed channels-shm is accepted before a release (works against a path
build and against a PyPI/TestPyPI install alike).

Exit code is non-zero on any shortfall, so it can gate CI or scripts.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
from multiprocessing import get_context

from channels_shm import SharedMemoryChannelLayer
from django.core.management.base import BaseCommand, CommandError

# Modest caps: a demo run must be light on /dev/shm (default 64 MiB in many
# containers) and finish in seconds.
LAYER_KWARGS = {
    "capacity": 1000,
    "shm_size": 16 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 50,
    "max_processes": 64,
    "max_members_per_group": 64,
}
RECV_TIMEOUT_S = 30.0


def _worker(prefix: str, group: str, n_messages: int, ready, results) -> None:
    """Child process: join the group, then receive until full or timed out."""
    # Imported inside the worker only; the child never touches Django settings.
    from channels_shm import SharedMemoryChannelLayer as Layer

    async def main() -> None:
        layer = Layer(prefix=prefix, **LAYER_KWARGS)
        channel = await layer.new_channel("demo.")
        await layer.group_add(group, channel)
        ready.set()

        got = 0
        deadline = time.monotonic() + RECV_TIMEOUT_S
        while got < n_messages:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(layer.receive(channel), timeout=remaining)
            except TimeoutError:
                break
            got += 1

        await layer.close()
        results.put((os.getpid(), got))

    try:
        asyncio.run(main())
    except Exception as exc:  # pragma: no cover - defensive child guard
        results.put((os.getpid(), -1))
        print(f"worker failed: {exc!r}", file=sys.stderr)


class Command(BaseCommand):
    help = "Cross-process acceptance: N workers must each receive all M group messages."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--workers", type=int, default=4)
        parser.add_argument("--messages", type=int, default=200)

    def handle(self, *args: object, **options: str | int) -> None:
        n = int(options["workers"])
        m = int(options["messages"])
        if n < 1 or m < 1:
            raise CommandError("--workers and --messages must be >= 1")

        # Fresh region per run (unique prefix), so stale /dev/shm state from a
        # previous crashed run can never skew this one.
        prefix = f"chatdemo_{uuid.uuid4().hex[:10]}"
        group = f"demo_{uuid.uuid4().hex[:8]}"

        ctx = get_context("spawn")
        ready_flags = [ctx.Event() for _ in range(n)]
        results = ctx.Queue()
        procs = [
            ctx.Process(
                target=_worker,
                args=(prefix, group, m, ready_flags[i], results),
            )
            for i in range(n)
        ]
        for p in procs:
            p.start()
        for i, ev in enumerate(ready_flags):
            if not ev.wait(timeout=RECV_TIMEOUT_S):
                for p in procs:
                    p.terminate()
                raise CommandError(f"worker {i} never joined the group")

        async def send_all() -> None:
            layer = SharedMemoryChannelLayer(prefix=prefix, **LAYER_KWARGS)
            for seq in range(m):
                await layer.group_send(
                    group, {"type": "demo.seq", "seq": seq, "from_pid": os.getpid()}
                )
            # unlink while the region handle is alive (close() drops it), so
            # /dev/shm holds nothing after the run even if workers leaked.
            layer.unlink_shm()
            await layer.close()

        asyncio.run(send_all())

        for p in procs:
            p.join(timeout=RECV_TIMEOUT_S + 5)

        received = [results.get(timeout=5) for _ in range(n)]
        self.stdout.write(
            f"group {group!r} via /dev/shm/{prefix}: "
            f"sent {m} messages to {n} worker processes"
        )
        ok = True
        for pid, got in received:
            status = "PASS" if got == m else "FAIL"
            if got != m:
                ok = False
            self.stdout.write(f"  worker pid {pid:>7}: received {got}/{m}  {status}")

        if not ok:
            raise CommandError("broadcast acceptance FAILED")
        self.stdout.write(self.style.SUCCESS("broadcast acceptance PASSED"))
