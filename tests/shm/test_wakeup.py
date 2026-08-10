"""Unit tests for channels_shm.shm.wakeup.

Maps to src/channels_shm/shm/wakeup.py. Covers WakeupManager lifecycle,
loop registration, errno handling in wakeup_remote, drain paths, and a real
sendto-success delivery between two managers.
"""

from __future__ import annotations

import asyncio
import errno
import os
import socket as _socket
from pathlib import Path

import pytest

from channels_shm.exceptions import DeadProcessError
from channels_shm.shm.wakeup import WakeupManager

_WAKEUP_PREFIX = "test_wakeup"


@pytest.fixture
def wakeup(tmp_path: Path) -> WakeupManager:
    """Create a WakeupManager bound in a temp directory."""
    wm = WakeupManager(_WAKEUP_PREFIX, str(tmp_path))
    wm.create()
    return wm


class TestWakeupBasic:
    """Core wakeup/drain/close behavior."""

    def test_wakeup_local(self, wakeup: WakeupManager) -> None:
        """wakeup_local writes to the eventfd without raising."""
        wakeup.wakeup_local()
        wakeup.drain_eventfd()

    def test_wakeup_local_no_eventfd(self, tmp_path: Path) -> None:
        """wakeup_local when eventfd is None should not raise."""
        wm = WakeupManager("test", str(tmp_path))
        wm.wakeup_local()

    def test_wakeup_remote_dead_process(self, wakeup: WakeupManager) -> None:
        """wakeup_remote to a non-existent socket raises DeadProcessError."""
        with pytest.raises(DeadProcessError):
            wakeup.wakeup_remote("/tmp/nonexistent_socket_path_12345.sock")

    def test_wakeup_remote_no_sock(self, tmp_path: Path) -> None:
        """wakeup_remote when wakeup_sock is None should be a no-op."""
        wm = WakeupManager("test", str(tmp_path))
        wm.wakeup_remote("/tmp/some_path.sock")

    def test_wakeup_remote_success(self, tmp_path: Path) -> None:
        """wakeup_remote delivers a datagram to the target's socket.

        Exercises the sendto success branch and the drain_socket receive loop
        against a real bound AF_UNIX socket pair: after sendto + drain the
        socket buffer must be empty (EAGAIN on a direct read).
        """
        target = WakeupManager("target", str(tmp_path))
        target.create()
        sender = WakeupManager("sender", str(tmp_path))
        sender.create()
        try:
            sender.wakeup_remote(target.socket_path)  # should not raise
            target.drain_socket()
            assert target.wakeup_sock is not None
            with pytest.raises(BlockingIOError):
                _ = target.wakeup_sock.recvfrom(4096)
        finally:
            sender.close()
            target.close()

    def test_drain_socket_empty(self, wakeup: WakeupManager) -> None:
        """drain_socket on an empty socket should return immediately."""
        wakeup.drain_socket()

    def test_drain_eventfd_empty(self, wakeup: WakeupManager) -> None:
        """drain_eventfd when nothing is pending should not raise."""
        wakeup.drain_eventfd()
        wakeup.drain_eventfd()

    def test_close(self, wakeup: WakeupManager) -> None:
        """close() closes both fds and unlinks the socket file."""
        socket_path = wakeup.socket_path
        assert Path(socket_path).exists()
        wakeup.close()
        assert not Path(socket_path).exists()

    def test_close_idempotent(self, wakeup: WakeupManager) -> None:
        """close() twice should not raise."""
        wakeup.close()
        wakeup.close()

    def test_dead_process_error(self) -> None:
        """DeadProcessError stores the socket path."""
        err = DeadProcessError("/tmp/test.sock")
        assert err.socket_path == "/tmp/test.sock"
        assert "/tmp/test.sock" in str(err)


class TestLoopRegistration:
    """register_with_loop / unregister_from_loop paths."""

    def test_register_and_unregister(self, wakeup: WakeupManager) -> None:
        loop = asyncio.new_event_loop()
        try:
            wakeup.register_with_loop(loop, lambda _: None)
            wakeup.unregister_from_loop(loop)
        finally:
            loop.close()

    def test_unregister_loop_no_fds(self, tmp_path: Path) -> None:
        """unregister_from_loop when fds are None should not raise."""
        wm = WakeupManager("test", str(tmp_path))
        loop = asyncio.new_event_loop()
        try:
            wm.unregister_from_loop(loop)
        finally:
            loop.close()

    def test_register_eventfd_none(self, tmp_path: Path) -> None:
        """register_with_loop when eventfd is None still registers the sock."""
        wm = WakeupManager("test", str(tmp_path))
        os.makedirs(str(tmp_path), exist_ok=True)
        wm.wakeup_sock = _socket.socket(
            _socket.AF_UNIX, _socket.SOCK_DGRAM | _socket.SOCK_NONBLOCK
        )
        wm.wakeup_sock.setblocking(False)
        sock_path = os.path.join(str(tmp_path), "test.sock")
        wm.wakeup_sock.bind(sock_path)
        loop = asyncio.new_event_loop()
        try:
            wm.register_with_loop(loop, lambda _: None)
            wm.unregister_from_loop(loop)
        finally:
            wm.close()
            loop.close()

    def test_register_sock_none(self, tmp_path: Path) -> None:
        """register_with_loop when wakeup_sock is None still registers eventfd."""
        wm = WakeupManager("test", str(tmp_path))
        wm.eventfd = os.eventfd(0, os.EFD_NONBLOCK | os.EFD_CLOEXEC)
        loop = asyncio.new_event_loop()
        try:
            wm.register_with_loop(loop, lambda _: None)
            wm.unregister_from_loop(loop)
        finally:
            wm.close()
            loop.close()

    def test_unregister_eventfd_exception(self, wakeup: WakeupManager) -> None:
        """unregister_from_loop swallows remove_reader errors (eventfd)."""
        loop = asyncio.new_event_loop()
        try:
            wakeup.register_with_loop(loop, lambda _: None)
            original_remove = loop.remove_reader
            call_count = [0]

            def failing_remove(fd: int) -> bool:
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("simulated failure")
                return original_remove(fd)

            loop.remove_reader = failing_remove  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[invalid-assignment]
            wakeup.unregister_from_loop(loop)
        finally:
            wakeup.close()
            loop.close()

    def test_unregister_sock_exception(self, wakeup: WakeupManager) -> None:
        """unregister_from_loop swallows remove_reader errors (sock)."""
        loop = asyncio.new_event_loop()
        try:
            wakeup.register_with_loop(loop, lambda _: None)
            original_remove = loop.remove_reader
            call_count = [0]

            def failing_remove(fd: int) -> bool:
                call_count[0] += 1
                if call_count[0] == 2:
                    raise RuntimeError("simulated failure")
                return original_remove(fd)

            loop.remove_reader = failing_remove  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[invalid-assignment]
            wakeup.unregister_from_loop(loop)
        finally:
            wakeup.close()
            loop.close()


class _FakeSock:
    """Minimal socket-like object whose sendto raises a configurable OSError."""

    def __init__(self, errno_val: int) -> None:
        self._errno: int = errno_val

    def sendto(self, _data: bytes, _addr: str) -> int:
        raise OSError(self._errno, os.strerror(self._errno))

    def fileno(self) -> int:
        return -1

    def close(self) -> None:
        pass


class TestWakeupErrnoBranches:
    """wakeup_remote errno classification (dead / transient / unexpected)."""

    @pytest.fixture
    def wm(self, tmp_path: Path) -> WakeupManager:
        wm = WakeupManager("test", str(tmp_path))
        wm.create()
        return wm

    def test_dead_errnos(
        self,
        wm: WakeupManager,
    ) -> None:
        """ENOENT/ECONNREFUSED/ENOTCONN raise DeadProcessError."""
        for errno_val in (errno.ENOENT, errno.ECONNREFUSED, errno.ENOTCONN):
            original_sock = wm.wakeup_sock
            wm.wakeup_sock = _FakeSock(errno_val)  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[invalid-assignment]
            try:
                with pytest.raises(DeadProcessError):
                    wm.wakeup_remote("/tmp/any.sock")
            finally:
                wm.wakeup_sock = original_sock  # type: ignore[assignment]

    def test_transient_errno(self, wm: WakeupManager) -> None:
        """EAGAIN/EWOULDBLOCK/ENOBUFS return silently (transient)."""
        for errno_val in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS):
            original_sock = wm.wakeup_sock
            wm.wakeup_sock = _FakeSock(errno_val)  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[invalid-assignment]
            try:
                wm.wakeup_remote("/tmp/any.sock")  # should not raise
            finally:
                wm.wakeup_sock = original_sock  # type: ignore[assignment]

    def test_unexpected_errno(self, wm: WakeupManager) -> None:
        """Unexpected errno logs a warning and returns (no raise)."""
        original_sock = wm.wakeup_sock
        wm.wakeup_sock = _FakeSock(errno.EPERM)  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[invalid-assignment]
        try:
            wm.wakeup_remote("/tmp/any.sock")  # should not raise
        finally:
            wm.wakeup_sock = original_sock  # type: ignore[assignment]
            wm.close()


class TestDrainNone:
    """drain paths when fds are None (uncreated manager)."""

    def test_drain_eventfd_none(self, tmp_path: Path) -> None:
        wm = WakeupManager("test", str(tmp_path))
        wm.drain_eventfd()  # should not raise

    def test_drain_socket_none(self, tmp_path: Path) -> None:
        wm = WakeupManager("test", str(tmp_path))
        wm.drain_socket()  # should not raise
