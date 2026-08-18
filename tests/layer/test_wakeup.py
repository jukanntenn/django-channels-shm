"""Wakeup routing, orphan-socket cleanup, and the ChannelFull retry path.

Maps to src/channels_shm/layer.py wakeup helpers: targeted unicast vs
broadcast, dead-process marking, and the bounded emergency-drain retry on a
full ring.
"""

from __future__ import annotations

import errno
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from channels_shm._native import registry_get_valid
from channels_shm.exceptions import ChannelFull
from tests.layer._helpers import register_fake_process
from tests.layout_helpers import region_native

if TYPE_CHECKING:
    from channels_shm import SharedMemoryChannelLayer


class TestWakeupRouting:
    """Targeted / broadcast wakeup, including dead-process marking."""

    async def test_wakeup_by_prefix_other_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Sending to another process's private channel wakes its registered owner.

        The owner is a real bound AF_UNIX socket, so the targeted unicast
        succeeds and the registry entry stays valid.
        """
        sock_path = "/tmp/channels_shm_test_alive_owner.sock"
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
        owner = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        owner.bind(sock_path)
        try:
            register_fake_process(layer, "OTHERPREFIX", sock_path)
            ch = "specific.OTHERPREFIX!abc123"
            await layer.send(ch, {"type": "msg"})
            entries = registry_get_valid(region_native(layer), layer.max_processes)
            paths = [pb.decode("utf-8") for _, pb in entries]
            assert sock_path in paths, "live owner must not be marked dead"
        finally:
            owner.close()
            try:
                os.unlink(sock_path)
            except FileNotFoundError:
                pass

    async def test_wakeup_broadcast_dead_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Broadcast marks dead sockets dead in the registry."""
        register_fake_process(
            layer, "dead_prefix", "/tmp/nonexistent_dead_socket_12345.sock"
        )
        layer._wakeup_broadcast()
        entries = registry_get_valid(region_native(layer), layer.max_processes)
        paths = [pb.decode("utf-8") for _, pb in entries]
        assert "/tmp/nonexistent_dead_socket_12345.sock" not in paths

    async def test_wakeup_by_prefix_dead_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Targeted wakeup of a dead owner marks its slot dead."""
        target_client_prefix = "dead_prefix2"
        register_fake_process(
            layer, target_client_prefix, "/tmp/nonexistent_dead_socket_67890.sock"
        )
        layer._wakeup_by_prefix(target_client_prefix)
        entries = registry_get_valid(region_native(layer), layer.max_processes)
        paths = [pb.decode("utf-8") for _, pb in entries]
        assert "/tmp/nonexistent_dead_socket_67890.sock" not in paths

    async def test_registry_mark_dead_not_found(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Marking a non-registered path is a silent no-op."""
        layer._registry_mark_dead_by_path("/tmp/nonexistent_path_not_in_registry.sock")


class TestSendRetry:
    """ChannelFull emergency-drain retry path."""

    async def test_send_retry_succeeds(self, layer: SharedMemoryChannelLayer) -> None:
        """A full ring frees up during the bounded retry loop."""
        ch = "test_retry_success"
        _ = layer._ensure_loop()
        pump = layer._pump
        assert pump is not None
        _ = pump.watch_channel(ch)
        for i in range(layer.capacity):
            await layer.send(ch, {"type": "msg", "seq": i})
        await layer.send(ch, {"type": "overflow"})  # retry succeeds after pump drains
        pump.stop()

    async def test_send_channel_full(self, layer: SharedMemoryChannelLayer) -> None:
        """send() raises ChannelFull when capacity stays exceeded."""
        ch = "test_full_channel"
        for _ in range(layer.capacity):
            await layer.send(ch, {"type": "msg"})
        with pytest.raises(ChannelFull):
            await layer.send(ch, {"type": "overflow"})

    async def test_send_without_wakeup_raises(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """send() must not silently lose wakeups on a torn-down layer."""
        original_wakeup = layer._wakeup
        layer._wakeup = None
        try:
            with pytest.raises(RuntimeError, match="Channel layer is closed"):
                await layer.send("test_retry_no_wakeup", {"type": "overflow"})
        finally:
            layer._wakeup = original_wakeup


class TestOrphanSocketCleanup:
    """_cleanup_orphan_sockets: stale .sock files are probed and removed."""

    async def test_no_dir(self, layer: SharedMemoryChannelLayer) -> None:
        original_dir = layer._wakeup_dir
        layer._wakeup_dir = "/tmp/nonexistent_wakeup_dir_12345"
        try:
            layer._cleanup_orphan_sockets()  # should not raise
        finally:
            layer._wakeup_dir = original_dir

    async def test_skips_non_sock_files(self, layer: SharedMemoryChannelLayer) -> None:
        wakeup_dir = layer._wakeup_dir
        non_sock = os.path.join(wakeup_dir, "not_a_socket.txt")
        with Path(non_sock).open("w") as f:
            _ = f.write("")
        try:
            layer._cleanup_orphan_sockets()
            assert Path(non_sock).exists()
        finally:
            try:
                os.unlink(non_sock)
            except FileNotFoundError:
                pass

    async def test_removes_dead_socket(self, layer: SharedMemoryChannelLayer) -> None:
        wakeup_dir = layer._wakeup_dir
        dead_sock = os.path.join(wakeup_dir, "dead_prefix.sock")
        with Path(dead_sock).open("w") as f:
            _ = f.write("")
        try:
            layer._cleanup_orphan_sockets()
            assert not Path(dead_sock).exists(), "dead socket should be removed"
        finally:
            try:
                os.unlink(dead_sock)
            except FileNotFoundError:
                pass

    async def test_probe_creation_failure_swallowed(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """OSError during probe-socket creation is swallowed."""
        wakeup_dir = layer._wakeup_dir
        dead_sock = os.path.join(wakeup_dir, "exception_prefix.sock")
        with Path(dead_sock).open("w") as f:
            _ = f.write("")
        try:
            with patch(
                "channels_shm.layer._socket.socket",
                side_effect=OSError("simulated"),
            ):
                layer._cleanup_orphan_sockets()
        finally:
            try:
                os.unlink(dead_sock)
            except FileNotFoundError:
                pass

    async def test_unexpected_errno_keeps_file(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """An unexpected errno (EAGAIN) does not delete the socket file."""
        wakeup_dir = layer._wakeup_dir
        dead_sock = os.path.join(wakeup_dir, "unexpected_errno.sock")
        with Path(dead_sock).open("w") as f:
            _ = f.write("")
        try:

            class _ErrnoProbe:
                def __init__(self, *args: object, **kwargs: object) -> None:
                    pass

                def sendto(self, _data: bytes, _addr: str) -> int:
                    raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

                def close(self) -> None:
                    pass

            with patch("channels_shm.layer._socket.socket", _ErrnoProbe):
                layer._cleanup_orphan_sockets()
            assert Path(dead_sock).exists()
        finally:
            try:
                os.unlink(dead_sock)
            except FileNotFoundError:
                pass

    async def test_unlink_race_swallowed(self, layer: SharedMemoryChannelLayer) -> None:
        """FileNotFoundError from unlink (concurrent removal) is fine."""
        wakeup_dir = layer._wakeup_dir
        dead_sock = os.path.join(wakeup_dir, "race_condition.sock")
        with Path(dead_sock).open("w") as f:
            _ = f.write("")
        try:
            original_unlink = os.unlink

            def failing_unlink(path: str) -> None:
                if path == dead_sock:
                    raise FileNotFoundError("simulated race condition")
                original_unlink(path)

            with patch("os.unlink", failing_unlink):
                layer._cleanup_orphan_sockets()
        finally:
            try:
                os.unlink(dead_sock)
            except FileNotFoundError:
                pass
