"""Fixtures local to the layer/ tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from channels_shm import SharedMemoryChannelLayer


@pytest.fixture
async def closed_layer(layer: SharedMemoryChannelLayer) -> SharedMemoryChannelLayer:
    """A layer whose public API must reject further use (except new_channel)."""
    await layer.close()
    return layer
