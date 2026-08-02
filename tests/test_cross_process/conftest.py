"""Cross-process integration test fixtures (Layer A).

These tests spawn real child processes via multiprocessing(spawn) and verify
inter-process communication through shared memory. Linux-only: the layer relies
on MAP_SHARED + AF_UNIX sockets.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import shutil
import sys
import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

# Linux-only guard: skip the whole directory on non-Linux.
pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="cross-process tests require Linux MAP_SHARED + AF_UNIX",
)


@pytest.fixture
def xproc_prefix() -> str:
    """Unique shm prefix shared by all processes in one test."""
    return f"xproc_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def xproc_ctx() -> Iterator[mp.context.BaseContext]:
    """multiprocessing spawn context (clean child state, no inherited fds)."""
    ctx = mp.get_context("spawn")
    yield ctx  # noqa: PT022
    # Context itself holds no resources; cleanup happens per-test via xproc_cleanup.


@pytest.fixture
def xproc_cleanup(xproc_prefix: str) -> Iterator[None]:
    """Remove shm artifacts left by the test."""
    yield
    for path in (
        f"/dev/shm/{xproc_prefix}",
        f"/dev/shm/{xproc_prefix}_wakeup",
        f"/dev/shm/{xproc_prefix}_obs",
    ):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.islink(path) or os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
