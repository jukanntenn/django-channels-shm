"""PID-reuse detection via starttime comparison.

pid_dead must distinguish a live process from a NEW process that reused the
same PID: the same pid with a different starttime is a different process.
"""

from __future__ import annotations

import os

import pytest

from channels_shm._native import pid_dead, read_self_starttime

pytestmark = pytest.mark.slow


def test_pid_dead_self_alive() -> None:
    """Current process with correct starttime should NOT be dead."""
    pid = os.getpid()
    st = read_self_starttime()
    assert not pid_dead(pid, st)


def test_pid_dead_self_wrong_starttime() -> None:
    """Same pid with a wrong starttime is a reused PID -> dead."""
    pid = os.getpid()
    assert pid_dead(pid, 0)
