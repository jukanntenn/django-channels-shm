"""Test fixtures for channels-shm."""

from __future__ import annotations

import os
import shutil
import uuid
from typing import TYPE_CHECKING

import pytest

from channels_shm import SharedMemoryChannelLayer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator


@pytest.fixture
def prefix() -> str:
    """Generate a unique shm prefix for test isolation."""
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def layer(prefix: str) -> Iterator[SharedMemoryChannelLayer]:
    """Create a SharedMemoryChannelLayer for testing."""
    layer = SharedMemoryChannelLayer(
        expiry=60,
        capacity=10,
        prefix=prefix,
        shm_size=16 * 1024 * 1024,  # 16MB for tests
        max_channels=100,
        max_groups=50,
        max_processes=16,
        max_members_per_group=64,
        watchdog_interval=None,  # Disable for tests
    )
    yield layer
    # Cleanup
    try:
        import asyncio

        loop = asyncio.get_event_loop()
        if loop.is_running():
            _ = loop.create_task(layer.close())
        else:
            loop.run_until_complete(layer.close())
    except Exception:
        pass
    try:
        layer.unlink_shm()
    except Exception:
        pass
    # Cleanup wakeup directory
    wakeup_dir = f"/dev/shm/{prefix}_wakeup"
    if os.path.isdir(wakeup_dir):
        shutil.rmtree(wakeup_dir, ignore_errors=True)
    try:
        os.unlink(f"/dev/shm/{prefix}")
    except FileNotFoundError:
        pass


@pytest.fixture
def layer_factory(
    prefix: str,
) -> Iterator[Callable[..., SharedMemoryChannelLayer]]:
    """Factory for creating multiple layers with the same prefix."""
    layers: list[SharedMemoryChannelLayer] = []

    def _create(
        *,
        expiry: int = 60,
        capacity: int = 10,
        shm_size: int = 16 * 1024 * 1024,
        max_channels: int = 100,
        max_groups: int = 50,
        max_processes: int = 16,
        max_members_per_group: int = 64,
        watchdog_interval: int | None = None,
    ) -> SharedMemoryChannelLayer:
        layer = SharedMemoryChannelLayer(
            expiry=expiry,
            capacity=capacity,
            prefix=prefix,
            shm_size=shm_size,
            max_channels=max_channels,
            max_groups=max_groups,
            max_processes=max_processes,
            max_members_per_group=max_members_per_group,
            watchdog_interval=watchdog_interval,
        )
        layers.append(layer)
        return layer

    yield _create

    for layer in layers:
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                _ = loop.create_task(layer.close())
            else:
                loop.run_until_complete(layer.close())
        except Exception:
            pass
        try:
            layer.unlink_shm()
        except Exception:
            pass
