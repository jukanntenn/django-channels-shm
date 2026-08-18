"""Shared fixtures for the channels-shm test suite.

The layer config is defined once here (``LAYER_CONFIG``) so unit tests,
cross-process tests and recovery tests don't drift apart on the magic numbers.
The ``layer`` / ``layer_factory`` fixtures are async generator fixtures:
pytest-asyncio (auto mode) runs their teardown on the test's event loop, so we
never touch ``asyncio.get_event_loop()`` — the previous pattern was fragile and
non-idiomatic.
"""

from __future__ import annotations

import os
import shutil
import uuid
from typing import TYPE_CHECKING, TypedDict

import pytest

from channels_shm import SharedMemoryChannelLayer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class LayerConfig(TypedDict):
    """Layer constructor kwargs used by every test (typed so ``**LAYER_CONFIG``
    spreads with exact key/value checking)."""

    expiry: int
    capacity: int
    shm_size: int
    max_channels: int
    max_groups: int
    max_processes: int
    max_members_per_group: int
    watchdog_interval: int | None


# Every test layer uses the same config; small enough to be cheap, large enough
# to exercise real capacity/index behavior. watchdog disabled: tests control
# draining explicitly and a periodic task would only add timing noise.
LAYER_CONFIG: LayerConfig = {
    "expiry": 60,
    "capacity": 10,
    "shm_size": 16 * 1024 * 1024,
    "max_channels": 100,
    "max_groups": 50,
    "max_processes": 16,
    "max_members_per_group": 64,
    "watchdog_interval": None,
}


@pytest.fixture
def prefix() -> str:
    """A unique shm prefix so parallel test runs never share /dev/shm files."""
    return f"test_{uuid.uuid4().hex[:8]}"


async def _close_layer(layer: SharedMemoryChannelLayer) -> None:
    """Close a layer, tolerating an already-closed one (close is idempotent)."""
    try:
        await layer.close()
    except Exception:
        pass


def _cleanup_shm(prefix: str) -> None:
    """Remove every on-disk artifact a layer creates under `prefix`.

    close() unmaps/frees fds but does not unlink the shm file; the observability
    dir (/dev/shm/{prefix}_obs) is created by every __init__ under __debug__ and
    was previously leaked.
    """
    for path in (
        f"/dev/shm/{prefix}",
        f"/dev/shm/{prefix}_wakeup",
        f"/dev/shm/{prefix}_obs",
    ):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.islink(path) or os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


@pytest.fixture
async def layer(prefix: str) -> AsyncIterator[SharedMemoryChannelLayer]:
    """A SharedMemoryChannelLayer for testing, torn down on the test loop."""
    layer = SharedMemoryChannelLayer(prefix=prefix, **LAYER_CONFIG)
    yield layer
    await _close_layer(layer)
    _cleanup_shm(prefix)


@pytest.fixture
async def layer_factory(
    prefix: str,
) -> AsyncIterator[Callable[..., SharedMemoryChannelLayer]]:
    """Create multiple layers sharing one prefix; all are closed on teardown.

    Used wherever a test needs two layers on the same shm (config mismatch,
    registry exhaustion, cross-layer wakeup). Overrides are explicit keyword
    params so the constructor call stays statically typed.
    """
    layers: list[SharedMemoryChannelLayer] = []

    def _create(
        *,
        expiry: int = LAYER_CONFIG["expiry"],
        capacity: int = LAYER_CONFIG["capacity"],
        shm_size: int = LAYER_CONFIG["shm_size"],
        max_channels: int = LAYER_CONFIG["max_channels"],
        max_groups: int = LAYER_CONFIG["max_groups"],
        max_processes: int = LAYER_CONFIG["max_processes"],
        max_members_per_group: int = LAYER_CONFIG["max_members_per_group"],
        watchdog_interval: int | None = LAYER_CONFIG["watchdog_interval"],
        channel_capacity: dict[str, int] | None = None,
    ) -> SharedMemoryChannelLayer:
        layer = SharedMemoryChannelLayer(
            prefix=prefix,
            expiry=expiry,
            capacity=capacity,
            shm_size=shm_size,
            max_channels=max_channels,
            max_groups=max_groups,
            max_processes=max_processes,
            max_members_per_group=max_members_per_group,
            watchdog_interval=watchdog_interval,
            channel_capacity=channel_capacity,
        )
        layers.append(layer)
        return layer

    yield _create

    for layer in layers:
        await _close_layer(layer)
    _cleanup_shm(prefix)
