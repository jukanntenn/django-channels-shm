"""Wakeup mechanism: eventfd (intra-process) + AF_UNIX socket (cross-process)."""

from __future__ import annotations

import errno
import logging
import os
import socket
from typing import TYPE_CHECKING, Any

from channels_shm.exceptions import DeadProcessError

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Upper bound on per-wakeup socket drains to prevent event-loop livelock under
# a malicious or burst sender that keeps the datagram queue non-empty (W-01).
# Excess datagrams are re-delivered by the level-triggered epoll on the next
# loop iteration (CPython selectors default to LT), so messages are not lost.
_SOCKET_DRAIN_MAX = 1024


class WakeupManager:
    """Manages wakeup fds (eventfd + AF_UNIX SOCK_DGRAM socket) for a process.

    Each process has one WakeupManager. It creates:
    - An eventfd for intra-process wakeup (send from same process)
    - An AF_UNIX SOCK_DGRAM socket for cross-process wakeup
    """

    client_prefix: str
    wakeup_dir: str
    socket_path: str
    eventfd: int | None
    wakeup_sock: socket.socket | None
    _on_wakeup: Callable[[int], None] | None
    _loop: asyncio.AbstractEventLoop | None
    _wakeup_sock_fd: int  # cached fd for _on_wakeup comparison (P-02/G-2)
    _metrics: Any  # MetricsRegistry or None; only set under __debug__

    def __init__(
        self,
        client_prefix: str,
        wakeup_dir: str,
        *,
        metrics: Any = None,
    ) -> None:
        self.client_prefix = client_prefix
        self.wakeup_dir = wakeup_dir
        self.socket_path = os.path.join(wakeup_dir, f"{client_prefix}.sock")

        self.eventfd = None
        self.wakeup_sock = None
        self._on_wakeup = None
        self._loop = None
        self._wakeup_sock_fd = -1
        self._metrics = metrics

    def create(self) -> None:
        """Create the eventfd and AF_UNIX socket."""
        # eventfd
        self.eventfd = os.eventfd(0, os.EFD_NONBLOCK | os.EFD_CLOEXEC)

        # AF_UNIX SOCK_DGRAM socket (non-blocking)
        os.makedirs(self.wakeup_dir, exist_ok=True)
        self.wakeup_sock = socket.socket(
            socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK
        )
        self.wakeup_sock.setblocking(False)
        # Unlink stale socket file if exists
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        self.wakeup_sock.bind(self.socket_path)
        self._wakeup_sock_fd = self.wakeup_sock.fileno()

    def register_with_loop(
        self,
        loop: asyncio.AbstractEventLoop,
        on_wakeup: Callable[[int], None],
    ) -> None:
        """Register both fds with the event loop."""
        self._on_wakeup = on_wakeup
        self._loop = loop  # recorded so close() can force-unregister (E-11)
        if self.eventfd is not None:
            loop.add_reader(self.eventfd, on_wakeup, self.eventfd)
        if self.wakeup_sock is not None:
            loop.add_reader(
                self.wakeup_sock.fileno(), on_wakeup, self.wakeup_sock.fileno()
            )

    def unregister_from_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Unregister both fds from the event loop.

        Narrow except to (RuntimeError, OSError): remove_reader raises
        RuntimeError if the loop is closed and OSError for invalid fds. Other
        exceptions indicate real bugs and must propagate (G-6 / J-1).
        """
        if self.eventfd is not None:
            try:
                _ = loop.remove_reader(self.eventfd)
            except (RuntimeError, OSError):
                pass
        if self.wakeup_sock is not None:
            try:
                _ = loop.remove_reader(self.wakeup_sock.fileno())
            except (RuntimeError, OSError):
                pass

    @property
    def socket_fd(self) -> int:
        """Cached fileno of the AF_UNIX wakeup socket (-1 if not bound).

        Public accessor for the cached fd so callers (ReceivePump._on_wakeup)
        don't reach into the private `_wakeup_sock_fd` (P-02 / SLF001).
        """
        return self._wakeup_sock_fd

    def wakeup_local(self) -> None:
        """Send a wakeup signal to this process's pump (intra-process)."""
        if self.eventfd is not None:
            os.eventfd_write(self.eventfd, 1)
            if __debug__ and self._metrics is not None:
                self._metrics.counter("eventfd_write_total").inc()

    def wakeup_remote(self, target_socket_path: str) -> None:
        """Send a wakeup signal to a remote process via AF_UNIX socket.

        Returns without raising on transient errors (EAGAIN, ENOBUFS).
        Returns True if the target is dead (mark its registry entry dead).
        """
        if self.wakeup_sock is None:
            return
        try:
            _ = self.wakeup_sock.sendto(b"\x01", target_socket_path)
            if __debug__ and self._metrics is not None:
                self._metrics.counter("sendto_success_total").inc()
        except OSError as e:
            if e.errno in (
                errno.ENOENT,
                errno.ECONNREFUSED,
                errno.ENOTCONN,
            ):
                # Target is dead
                if __debug__ and self._metrics is not None:
                    self._metrics.counter("sendto_dead_errno_total").inc(
                        errno=str(e.errno)
                    )
                raise DeadProcessError(target_socket_path) from e
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOBUFS):
                # Transient: target alive but buffer full
                if __debug__ and self._metrics is not None:
                    self._metrics.counter("sendto_transient_errno_total").inc(
                        errno=str(e.errno)
                    )
                return
            logger.warning(
                "wakeup sendto unexpected errno=%d to %s",
                e.errno,
                target_socket_path,
            )

    def drain_eventfd(self) -> None:
        """Read and clear the eventfd counter."""
        if self.eventfd is not None:
            try:
                _ = os.eventfd_read(self.eventfd)
            except OSError:
                pass

    def drain_socket(self) -> None:
        """Drain pending wakeup bytes from the socket buffer.

        Bounded by `_SOCKET_DRAIN_MAX` to prevent event-loop livelock under a
        burst/malicious sender that keeps the datagram queue non-empty (W-01).
        Any backlog beyond the cap is re-delivered by the level-triggered epoll
        on the next loop iteration, so wakeups (and thus messages) are not lost.
        """
        if self.wakeup_sock is None:
            return
        for _ in range(_SOCKET_DRAIN_MAX):
            try:
                _ = self.wakeup_sock.recvfrom(4096)
            except BlockingIOError:
                break  # EAGAIN: buffer empty

    def close(self) -> None:
        """Close and clean up both fds and the socket file.

        Force-unregisters from the loop first (E-11): callers that bypass
        pump.stop() and call close() directly would otherwise leave dangling
        add_reader entries pointing at closed fds.
        """
        if self._loop is not None:
            self.unregister_from_loop(self._loop)
            self._loop = None
        if self.eventfd is not None:
            os.close(self.eventfd)
            self.eventfd = None
        if self.wakeup_sock is not None:
            self.wakeup_sock.close()
            self.wakeup_sock = None
        self._wakeup_sock_fd = -1
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

    def __enter__(self) -> WakeupManager:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
