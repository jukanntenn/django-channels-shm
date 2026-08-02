"""Shared memory region management (shm_open/mmap)."""

from __future__ import annotations

import ctypes
import mmap
import os

from channels_shm._native import ShmRegion as NativeShmRegion


def _get_mmap_address(mm: mmap.mmap) -> int:
    """Get the memory address of an mmap object using ctypes.

    LIFETIME INVARIANT (R-08): the returned raw usize is handed to the Rust
    `NativeShmRegion(addr, size)`. Rust's `ShmRegion<'a>` lifetime is *not*
    enforced across this Python→Rust FFI boundary (a bare usize carries no
    lifetime). The caller MUST keep `mm` alive for as long as any Rust
    `ShmRegion`/manager holds the address. `ShmRegionHandle` satisfies this by
    storing `mm` as `self._mm` for its whole lifetime; after `close()`,
    `self._native` is set to None and no Rust code may dereference the address.
    """
    c_buf = (ctypes.c_char * 1).from_buffer(mm)
    return ctypes.addressof(c_buf)


class ShmRegionHandle:
    """Manages a shared memory region lifecycle (open/mmap/munmap/close).

    Wraps the native ShmRegion for atomic operations on the mapped memory.
    Supports use as a context manager (`with region: ...`) for exception-safe
    fd/mmap cleanup (E-07); `FlushLock` (the sibling class) also implements this.
    """

    _path: str
    _size: int
    _fd: int | None
    _mm: mmap.mmap | None
    _native: NativeShmRegion | None

    def __init__(self, path: str) -> None:
        self._path = path
        self._size = 0
        self._fd = None
        self._mm = None
        self._native = None

    def open(self, create: bool = False) -> None:
        """Open (or create) the shared memory file and mmap it."""
        flags = os.O_RDWR | os.O_CLOEXEC
        if create:
            flags |= os.O_CREAT
        self._fd = os.open(self._path, flags, 0o600)
        st = os.fstat(self._fd)
        if st.st_size > 0:
            self._size = st.st_size
            self._mm = mmap.mmap(self._fd, self._size, access=mmap.ACCESS_WRITE)
            self._native = NativeShmRegion(_get_mmap_address(self._mm), self._size)

    def ftruncate_and_remap(self, new_size: int) -> None:
        """Ftruncate the file and remap. Used by first-process init.

        WINDOW NOTE (R-07): between `self._mm.close()` (munmap) and the new
        `mmap.mmap(...)` assignment, `self._native` briefly points at the old,
        now-unmapped address. This is only called from `_init_shm` during
        `__init__`, before any coroutine can observe `region.native`, so the
        window is unreachable in practice — but the invariant (no concurrent
        access during remap) must be preserved by any future caller.
        """
        if self._fd is None:
            raise RuntimeError("ShmRegionHandle not opened")
        os.ftruncate(self._fd, new_size)
        if self._mm is not None:
            self._mm.close()
        self._size = new_size
        self._mm = mmap.mmap(self._fd, new_size, access=mmap.ACCESS_WRITE)
        self._native = NativeShmRegion(_get_mmap_address(self._mm), new_size)

    @property
    def native(self) -> NativeShmRegion:
        if self._native is None:
            raise RuntimeError("ShmRegionHandle not opened")
        return self._native

    @property
    def fd(self) -> int:
        if self._fd is None:
            raise RuntimeError("ShmRegionHandle not opened")
        return self._fd

    @property
    def size(self) -> int:
        return self._size

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        """Munmap and close the fd. Does NOT unlink the shm."""
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        self._native = None

    def unlink(self) -> None:
        """Unlink (delete) the shared memory file."""
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass

    def __enter__(self) -> ShmRegionHandle:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
