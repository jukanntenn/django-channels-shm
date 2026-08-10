"""Unit tests for channels_shm.shm.region.

Maps to src/channels_shm/shm/region.py. Covers ShmRegionHandle lifecycle:
open/ftruncate/mmap branches, close idempotency, unlink, and context-manager
use (E-07 exception-safety contract).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from channels_shm.shm.region import ShmRegionHandle

if TYPE_CHECKING:
    from pathlib import Path


class TestUnopenedHandle:
    """Accessing resources before open() raises RuntimeError."""

    def test_native_not_opened(self) -> None:
        handle = ShmRegionHandle("/dev/shm/test_nonexistent")
        with pytest.raises(RuntimeError, match="not opened"):
            _ = handle.native

    def test_fd_not_opened(self) -> None:
        handle = ShmRegionHandle("/dev/shm/test_nonexistent")
        with pytest.raises(RuntimeError, match="not opened"):
            _ = handle.fd

    def test_ftruncate_not_opened(self) -> None:
        handle = ShmRegionHandle("/dev/shm/test_nonexistent")
        with pytest.raises(RuntimeError, match="not opened"):
            handle.ftruncate_and_remap(1024)


class TestHandleProperties:
    """Static properties before any lifecycle call."""

    def test_path_property(self) -> None:
        assert ShmRegionHandle("/dev/shm/test_path").path == "/dev/shm/test_path"

    def test_size_property_unopened(self) -> None:
        assert ShmRegionHandle("/dev/shm/test_size").size == 0


class TestOpenAndRemap:
    """open() / ftruncate_and_remap() branch coverage."""

    def test_open_empty_file(self, tmp_path: Path) -> None:
        """open() on a newly created empty file: st_size == 0 branch."""
        path = tmp_path / "test_empty"
        handle = ShmRegionHandle(str(path))
        handle.open(create=True)
        assert handle.size == 0
        handle.close()

    def test_open_existing(self, tmp_path: Path) -> None:
        """open() on an existing non-empty shm should mmap it."""
        path = tmp_path / "test_shm"
        handle1 = ShmRegionHandle(str(path))
        handle1.open(create=True)
        handle1.ftruncate_and_remap(4096)
        handle1.close()

        handle2 = ShmRegionHandle(str(path))
        handle2.open(create=True)
        assert handle2.size == 4096
        handle2.close()

    def test_open_no_create(self, tmp_path: Path) -> None:
        """open(create=False) on an existing file maps it without O_CREAT."""
        path = tmp_path / "test_no_create"
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.ftruncate(fd, 4096)
        os.close(fd)
        handle = ShmRegionHandle(str(path))
        handle.open(create=False)
        assert handle.size == 4096
        handle.close()

    def test_ftruncate_on_open_region(self, tmp_path: Path) -> None:
        """ftruncate_and_remap twice: the second call remaps over the old mmap."""
        path = tmp_path / "test_remap"
        handle = ShmRegionHandle(str(path))
        handle.open(create=True)
        handle.ftruncate_and_remap(4096)
        assert handle.size == 4096
        handle.ftruncate_and_remap(8192)
        assert handle.size == 8192
        handle.close()


class TestCloseAndUnlink:
    """Cleanup paths."""

    def test_close_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "test_close"
        handle = ShmRegionHandle(str(path))
        handle.open(create=True)
        handle.close()
        handle.close()  # should not raise

    def test_close_via_context_manager(self, tmp_path: Path) -> None:
        """`with ShmRegionHandle(...)` must close the fd (E-07)."""
        path = tmp_path / "test_ctx"
        with ShmRegionHandle(str(path)) as handle:
            handle.open(create=True)
            assert handle.fd >= 0
        with pytest.raises(RuntimeError, match="not opened"):
            _ = handle.fd

    def test_unlink_nonexistent(self) -> None:
        """unlink() on a non-existent file should not raise."""
        ShmRegionHandle("/dev/shm/test_unlink_nonexistent_12345").unlink()
