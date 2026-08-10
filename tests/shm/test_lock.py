"""Unit tests for channels_shm.shm.lock.

Maps to src/channels_shm/shm/lock.py. FlushLock is the general cold-path
cross-process lock (§4.3): fcntl.flock on the shm fd. The kernel auto-releases
flock when the owning process exits or its fd closes — the property that makes
crash recovery deadlock-free.
"""

from __future__ import annotations

import fcntl
import os
import time
from typing import TYPE_CHECKING

import pytest

from channels_shm.shm.lock import FlushLock

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def lock_file(tmp_path: Path) -> tuple[int, Path]:
    """An open fd plus its path, for lock tests."""
    path = tmp_path / "test.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
    return fd, path


class TestFlushLock:
    """Acquire/release semantics on a single fd description."""

    def test_acquire_release(self, lock_file: tuple[int, Path]) -> None:
        fd, _ = lock_file
        lock = FlushLock(fd)
        lock.acquire()
        lock.acquire()  # same fd description: flock re-enters, no deadlock
        lock.release()
        lock.release()

    def test_context_manager(self, lock_file: tuple[int, Path]) -> None:
        """__exit__ must release the lock so a second fd can acquire."""
        fd, path = lock_file
        fd2 = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        try:
            # Held inside the block: a different fd description blocks.
            with FlushLock(fd), pytest.raises(BlockingIOError):
                fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Released on __exit__.
            fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd2, fcntl.LOCK_UN)
        finally:
            os.close(fd2)


class TestFlushLockCrossFd:
    """Cross-fd mutual exclusion (the semantics another process sees)."""

    def test_mutual_exclusion_across_fds(self, lock_file: tuple[int, Path]) -> None:
        fd, path = lock_file
        fd2 = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        try:
            lock = FlushLock(fd)
            lock.acquire()
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)
            lock.release()
            fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)  # now free
            fcntl.flock(fd2, fcntl.LOCK_UN)
        finally:
            os.close(fd2)

    def test_close_releases_flock(self, lock_file: tuple[int, Path]) -> None:
        """Closing the fd releases the lock (kernel semantics)."""
        fd, path = lock_file
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.close(fd)
        fd2 = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)  # must not block
            fcntl.flock(fd2, fcntl.LOCK_UN)
        finally:
            os.close(fd2)


class TestFlushLockCrashRelease:
    """§4.3: flock is released by the kernel when the holder exits."""

    def test_flock_released_on_process_exit(self, tmp_path: Path) -> None:
        """A holder that dies WITHOUT releasing/unlocking frees the lock.

        The child acquires via FlushLock and exits without touching the fd;
        the parent then proves the lock is free via a non-blocking acquire.
        """
        path = tmp_path / "test_crash.lock"
        pid = os.fork()
        if pid == 0:
            holder_fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC)
            try:
                FlushLock(holder_fd).acquire()
            except Exception:
                os._exit(3)
            # Die without release() and without closing the fd — the kernel
            # must tear the flock down as part of process teardown.
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0
        fd2 = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        try:
            fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)  # must be free now
            fcntl.flock(fd2, fcntl.LOCK_UN)
        finally:
            os.close(fd2)

    def test_unlock_allows_competitor(self, tmp_path: Path) -> None:
        """A competitor blocked by the holder acquires once it unlocks."""
        path = tmp_path / "test_competitor.lock"
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        try:
            lock = FlushLock(fd)
            lock.acquire()

            pid = os.fork()
            if pid == 0:
                child_fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
                try:
                    for _ in range(100):
                        try:
                            fcntl.flock(child_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            fcntl.flock(child_fd, fcntl.LOCK_UN)
                            os.close(child_fd)
                            os._exit(0)
                        except BlockingIOError:
                            time.sleep(0.02)
                    os._exit(1)
                finally:
                    os.close(child_fd)

            # Give the child a chance to fail acquiring while we hold the lock.
            time.sleep(0.1)
            lock.release()
            _, status = os.waitpid(pid, 0)
            assert os.WEXITSTATUS(status) == 0
        finally:
            os.close(fd)
