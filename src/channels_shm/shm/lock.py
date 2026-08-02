"""Cross-process flush lock based on fcntl.flock."""

from __future__ import annotations

import fcntl


class FlushLock:
    """Cross-process cold-path lock using fcntl.flock on the shm fd.

    Despite the legacy name, this is the **general cold-path lock** (spec §4.3
    "FlushLock: fcntl.flock cold-path lock"): it protects ALL structural shm
    mutations — channel/group index create/remove, slab alloc/free, recover,
    compact, flush, and first-process init (17 call sites, of which flush is
    just one). The class name is historical; the docstring and spec §4.3 title
    both describe it as a cold-path lock. Hot-path operations NEVER take it.

    The kernel automatically releases flock when the process exits,
    eliminating crash-induced deadlocks.
    """

    _fd: int

    def __init__(self, shm_fd: int) -> None:
        self._fd = shm_fd

    def acquire(self) -> None:
        """Acquire an exclusive blocking lock."""
        fcntl.flock(self._fd, fcntl.LOCK_EX)

    def release(self) -> None:
        """Release the lock."""
        fcntl.flock(self._fd, fcntl.LOCK_UN)

    def __enter__(self) -> FlushLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
