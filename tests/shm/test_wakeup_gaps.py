"""WakeupManager coverage gaps: transient-errno silence and the context manager."""

from __future__ import annotations

import errno
import os
from typing import TYPE_CHECKING

from channels_shm.shm.wakeup import WakeupManager

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _FakeSock:
    """Stands in for wakeup_sock.sendto raising a transient errno."""

    _err: int

    def __init__(self, err: int) -> None:
        self._err = err

    def sendto(self, _buf: bytes, _target: str) -> int:
        raise OSError(self._err, os.strerror(self._err))

    def close(self) -> None:
        pass


class TestWakeupRemoteTransient:
    """EAGAIN/ENOBUFS from sendto is a live-but-busy target: silent return."""

    def test_transient_errno_is_silent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wakeup = WakeupManager("prefix", os.fspath(tmp_path))
        wakeup.create()
        try:
            monkeypatch.setattr(wakeup, "wakeup_sock", _FakeSock(errno.EAGAIN))
            # Must not raise (transient) and must not raise DeadProcessError.
            wakeup.wakeup_remote("/nonexistent/target.sock")
        finally:
            wakeup.close()


class TestWakeupContextManager:
    def test_context_manager_closes(self, tmp_path: Path) -> None:
        with WakeupManager("prefix", os.fspath(tmp_path)) as wakeup:
            wakeup.create()
            assert wakeup.eventfd is not None
        assert wakeup.eventfd is None
        assert wakeup.wakeup_sock is None
