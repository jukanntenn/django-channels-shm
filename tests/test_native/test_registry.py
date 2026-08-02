"""Integration tests for the wakeup registry functions exposed via PyO3."""

from __future__ import annotations

from typing import TYPE_CHECKING

from channels_shm._native import (
    registry_get_valid,
    registry_mark_dead,
    registry_register,
)

if TYPE_CHECKING:
    from tests.test_native._types import ShmLayout


def test_register_then_get_valid(layout: ShmLayout) -> None:
    """A registered process should appear in registry_get_valid."""
    slot = registry_register(
        layout.region,
        "abc123",
        "/tmp/wakeup_abc123.sock",
        pid=12345,
        start_time=99999,
        max_processes=layout.max_processes,
    )
    assert slot != 0
    entries = registry_get_valid(layout.region, layout.max_processes)
    assert len(entries) == 1
    out_slot, path = entries[0]
    assert out_slot == slot
    assert path == b"/tmp/wakeup_abc123.sock"


def test_register_multiple_distinct(layout: ShmLayout) -> None:
    """Multiple registrations should each appear in registry_get_valid."""
    s1 = registry_register(
        layout.region, "p1", "/tmp/p1.sock", 100, 1000, layout.max_processes
    )
    s2 = registry_register(
        layout.region, "p2", "/tmp/p2.sock", 200, 2000, layout.max_processes
    )
    s3 = registry_register(
        layout.region, "p3", "/tmp/p3.sock", 300, 3000, layout.max_processes
    )
    assert len({s1, s2, s3}) == 3
    entries = registry_get_valid(layout.region, layout.max_processes)
    assert len(entries) == 3
    paths = sorted(p for _, p in entries)
    assert paths == [b"/tmp/p1.sock", b"/tmp/p2.sock", b"/tmp/p3.sock"]


def test_mark_dead_removes_from_valid(layout: ShmLayout) -> None:
    """registry_mark_dead should remove a slot from registry_get_valid."""
    slot = registry_register(
        layout.region, "x", "/tmp/x.sock", 1, 1, layout.max_processes
    )
    assert slot != 0
    assert len(registry_get_valid(layout.region, layout.max_processes)) == 1
    registry_mark_dead(layout.region, slot)
    assert len(registry_get_valid(layout.region, layout.max_processes)) == 0


def test_register_reuses_dead_slot(layout: ShmLayout) -> None:
    """After a slot is marked dead, a new registration should reuse it."""
    slot1 = registry_register(
        layout.region, "first", "/tmp/first.sock", 1, 1, layout.max_processes
    )
    registry_mark_dead(layout.region, slot1)
    slot2 = registry_register(
        layout.region, "second", "/tmp/second.sock", 2, 2, layout.max_processes
    )
    assert slot1 == slot2
    entries = registry_get_valid(layout.region, layout.max_processes)
    assert len(entries) == 1
    _, path = entries[0]
    assert path == b"/tmp/second.sock"


def test_register_full_returns_zero() -> None:
    """When all slots are taken, register should return 0 (no slot)."""
    # Use a layout with very few process slots.
    from tests.test_native._types import ShmLayout as Layout

    small = Layout(max_processes=2)
    try:
        s1 = registry_register(
            small.region, "a", "/tmp/a.sock", 1, 1, small.max_processes
        )
        s2 = registry_register(
            small.region, "b", "/tmp/b.sock", 2, 2, small.max_processes
        )
        assert s1 != 0
        assert s2 != 0
        # Third registration — no slot available.
        s3 = registry_register(
            small.region, "c", "/tmp/c.sock", 3, 3, small.max_processes
        )
        assert s3 == 0
    finally:
        small.close()
