"""Shared fixtures for native-module integration tests.

The backing buffer classes live in ``_types.py`` so test modules can import
them by name for type annotations; this module only registers pytest fixtures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.native._types import NativeRegion, ShmLayout

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def region() -> Iterator[NativeRegion]:
    """A small (8 KiB) zeroed region for low-level atomic tests."""
    r = NativeRegion(8 * 1024)
    yield r
    r.close()


@pytest.fixture
def region_64k() -> Iterator[NativeRegion]:
    """A 64 KiB zeroed region for slab/ring tests."""
    r = NativeRegion(64 * 1024)
    yield r
    r.close()


@pytest.fixture
def layout() -> Iterator[ShmLayout]:
    """A fully-initialized shm layout for testing index/registry/group ops."""
    lay = ShmLayout()
    yield lay
    lay.close()
