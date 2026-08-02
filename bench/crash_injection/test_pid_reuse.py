"""X1: PID reuse detection via starttime comparison.

Tests that pid_dead correctly distinguishes between a dead process
and a new process that reused the same PID.
"""

from __future__ import annotations

import os

from channels_shm._native import pid_dead, read_self_starttime


def test_pid_dead_self_alive() -> None:
    """Current process with correct starttime should NOT be dead."""
    pid = os.getpid()
    st = read_self_starttime()
    assert not pid_dead(pid, st)


def test_pid_dead_self_wrong_starttime() -> None:
    """Current process with wrong starttime should be detected as dead (PID reuse)."""
    pid = os.getpid()
    assert pid_dead(pid, 0)  # Wrong starttime -> PID reuse detection


def test_pid_dead_nonexistent() -> None:
    """Non-existent PID should be detected as dead."""
    assert pid_dead(999999, 12345)


def test_pid_dead_zero_pid() -> None:
    """PID 0 should never be considered dead."""
    assert not pid_dead(0, 0)
