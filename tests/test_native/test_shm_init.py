"""Integration tests for shm_init / check_magic / validate_config via PyO3."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from channels_shm._native import (
    MAGIC,
    VERSION,
    check_magic,
    pid_dead,
    read_self_starttime,
    read_version,
    validate_config,
)

if TYPE_CHECKING:
    from tests.test_native._types import NativeRegion, ShmLayout


def test_check_magic_true_after_init(layout: ShmLayout) -> None:
    """After shm_init, check_magic should return True."""
    assert check_magic(layout.region) is True


def test_read_version_after_init(layout: ShmLayout) -> None:
    """read_version should return the spec VERSION constant after init."""
    assert read_version(layout.region) == VERSION


def test_validate_config_matches(layout: ShmLayout) -> None:
    """validate_config should return True for the exact config used at init."""
    assert (
        validate_config(
            layout.region,
            inline_size=layout.inline_size,
            default_capacity=layout.default_capacity,
            max_channels=layout.max_channels,
            max_groups=layout.max_groups,
            max_members_per_group=layout.max_members_per_group,
            max_processes=layout.max_processes,
        )
        is True
    )


def test_validate_config_mismatch_on_inline_size(layout: ShmLayout) -> None:
    """validate_config should return False when inline_size differs."""
    assert (
        validate_config(
            layout.region,
            inline_size=layout.inline_size + 1,
            default_capacity=layout.default_capacity,
            max_channels=layout.max_channels,
            max_groups=layout.max_groups,
            max_members_per_group=layout.max_members_per_group,
            max_processes=layout.max_processes,
        )
        is False
    )


def test_validate_config_mismatch_on_max_channels(layout: ShmLayout) -> None:
    """validate_config should return False when max_channels differs."""
    assert (
        validate_config(
            layout.region,
            inline_size=layout.inline_size,
            default_capacity=layout.default_capacity,
            max_channels=layout.max_channels + 1,
            max_groups=layout.max_groups,
            max_members_per_group=layout.max_members_per_group,
            max_processes=layout.max_processes,
        )
        is False
    )


def test_check_magic_false_on_uninit_region(region: NativeRegion) -> None:
    """On a zeroed (uninitialized) region, check_magic should return False."""
    assert check_magic(region.region) is False


def test_layout_exposes_magic_and_version_constants() -> None:
    """Sanity: the MAGIC/VERSION constants are exposed to Python."""
    assert MAGIC == 0x4348_5348
    assert VERSION == 1


def test_pid_dead_zero_is_never_dead() -> None:
    """pid_dead should return False for pid=0 (sentinel for 'no owner')."""
    assert pid_dead(0, 0) is False


def test_pid_dead_nonexistent_pid() -> None:
    """A PID that does not exist should be reported as dead."""
    # Use a very large PID that is essentially never in use on Linux.
    assert pid_dead(2**31 - 1, 0) is True


def test_pid_dead_current_process_live() -> None:
    """pid_dead should return False for the current live process."""
    st = read_self_starttime()
    assert st > 0
    assert pid_dead(os.getpid(), st) is False


def test_pid_dead_pid_reuse_detected() -> None:
    """pid_dead should return True when starttime doesn't match (PID reused)."""
    # Same PID but a wildly different starttime → treated as a recycled PID.
    assert pid_dead(os.getpid(), 1) is True


def test_validate_config_is_deterministic(layout: ShmLayout) -> None:
    """Calling validate_config multiple times should be deterministic."""
    for _ in range(3):
        assert validate_config(
            layout.region,
            inline_size=layout.inline_size,
            default_capacity=layout.default_capacity,
            max_channels=layout.max_channels,
            max_groups=layout.max_groups,
            max_members_per_group=layout.max_members_per_group,
            max_processes=layout.max_processes,
        )


def test_full_layout_round_trip_smoke(layout: ShmLayout) -> None:
    """End-to-end smoke: init → magic check → config validate → version read."""
    assert check_magic(layout.region)
    assert read_version(layout.region) == VERSION
    assert validate_config(
        layout.region,
        layout.inline_size,
        layout.default_capacity,
        layout.max_channels,
        layout.max_groups,
        layout.max_members_per_group,
        layout.max_processes,
    )
