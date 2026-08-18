"""Helpers shared by the layer/ tests (mirrors of src/channels_shm/layer.py).

Kept out of the test modules because several files exercise the same fake
registry process; duplicating it would drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from channels_shm._native import registry_register

if TYPE_CHECKING:
    from channels_shm import SharedMemoryChannelLayer


def register_fake_process(
    layer: SharedMemoryChannelLayer, client_prefix: str, sock_path: str
) -> None:
    """Register a (possibly dead) fake process in the wakeup registry."""
    region = layer._region
    lock = layer._lock
    assert region is not None
    assert lock is not None
    with lock:
        slot_off = registry_register(
            region.native,
            client_prefix,
            sock_path,
            99999,  # a pid that is never alive
            0,
            layer.max_processes,
        )
    assert slot_off != 0
