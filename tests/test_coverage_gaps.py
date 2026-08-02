"""Additional targeted tests to close remaining coverage gaps for §12.8.

Each test section covers specific uncovered code paths identified by the
coverage report that were not covered by test_coverage.py.
"""

from __future__ import annotations

import asyncio
import errno
import os
import socket as _socket
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from channels_shm.serializer import _normalize_value, normalize_message

if TYPE_CHECKING:
    from channels_shm import SharedMemoryChannelLayer


# ═══════════════════════════════════════════════════════════════════════
# Section 1: serializer.py — list branch in _normalize_value (line 84)
# ═══════════════════════════════════════════════════════════════════════


class TestSerializerListNormalize:
    """Cover the list branch of _normalize_value (line 84)."""

    def test_normalize_list_of_scalars(self) -> None:
        assert _normalize_value([1, "a", None]) == [1, "a", None]

    def test_normalize_nested_list(self) -> None:
        assert _normalize_value([[1, 2], [3]]) == [[1, 2], [3]]

    def test_normalize_list_of_dicts(self) -> None:
        val = [{"k": [1, 2]}, {"j": "v"}]
        assert _normalize_value(val) == [{"k": [1, 2]}, {"j": "v"}]  # pyright: ignore[reportArgumentType]

    def test_normalize_message_with_list(self) -> None:
        """normalize_message on a dict with list values."""
        msg = {"type": "test", "items": [1, 2, 3]}
        result = normalize_message(msg)  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
        assert result == msg


# ═══════════════════════════════════════════════════════════════════════
# Section 2: shm/region.py — open and ftruncate branches
# ═══════════════════════════════════════════════════════════════════════


class TestShmRegionBranches:
    """Cover shm/region.py branch gaps (lines 40->42, 55)."""

    def test_open_empty_file(self, tmp_path: str) -> None:
        """open() on a newly created empty file: st_size == 0 branch (40->42)."""
        from channels_shm.shm.region import ShmRegionHandle

        path = os.path.join(tmp_path, "test_empty")
        handle = ShmRegionHandle(path)
        handle.open(create=True)
        # st_size == 0: _mm and _native stay None
        assert handle.size == 0
        handle.close()
        os.unlink(path)

    def test_ftruncate_on_open_region(self, tmp_path: str) -> None:
        """ftruncate_and_remap when _mm is already mapped (line 55)."""
        from channels_shm.shm.region import ShmRegionHandle

        path = os.path.join(tmp_path, "test_remap")
        handle = ShmRegionHandle(path)
        handle.open(create=True)
        # First ftruncate: _mm is None, so the `if self._mm is not None` is False
        handle.ftruncate_and_remap(4096)
        assert handle.size == 4096
        # Second ftruncate: _mm is not None, so the close+remap path is taken (line 55)
        handle.ftruncate_and_remap(8192)
        assert handle.size == 8192
        handle.close()
        os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════
# Section 3: shm/wakeup.py — register, unregister, errno, drain branches
# ═══════════════════════════════════════════════════════════════════════


class TestWakeupRegisterBranches:
    """Cover wakeup.py register/unregister branch gaps."""

    def test_register_eventfd_none(self, tmp_path: str) -> None:
        """register_with_loop when eventfd is None (71->73)."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        # Only create the socket, not the eventfd — manually set up
        os.makedirs(str(tmp_path), exist_ok=True)
        wm.wakeup_sock = _socket.socket(
            _socket.AF_UNIX, _socket.SOCK_DGRAM | _socket.SOCK_NONBLOCK
        )
        wm.wakeup_sock.setblocking(False)
        sock_path = os.path.join(str(tmp_path), "test.sock")
        wm.wakeup_sock.bind(sock_path)

        loop = asyncio.new_event_loop()
        try:
            wm.register_with_loop(loop, lambda _: None)
            wm.unregister_from_loop(loop)
        finally:
            wm.close()
            loop.close()

    def test_register_sock_none(self, tmp_path: str) -> None:
        """register_with_loop when wakeup_sock is None (73->exit)."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        # Only create eventfd, not the socket
        wm.eventfd = os.eventfd(0, os.EFD_NONBLOCK | os.EFD_CLOEXEC)

        loop = asyncio.new_event_loop()
        try:
            wm.register_with_loop(loop, lambda _: None)
            wm.unregister_from_loop(loop)
        finally:
            wm.close()
            loop.close()

    def test_unregister_eventfd_exception(self, tmp_path: str) -> None:
        """unregister_from_loop: exception on remove_reader(eventfd) (83-84)."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        wm.create()

        loop = asyncio.new_event_loop()
        try:
            wm.register_with_loop(loop, lambda _: None)
            # Mock remove_reader to raise for eventfd
            original_remove = loop.remove_reader

            call_count = [0]

            def failing_remove(fd: int) -> bool:
                call_count[0] += 1
                if call_count[0] == 1:
                    raise RuntimeError("simulated failure")
                return original_remove(fd)

            loop.remove_reader = failing_remove  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[invalid-assignment]
            # Should not raise
            wm.unregister_from_loop(loop)
        finally:
            wm.close()
            loop.close()

    def test_unregister_sock_exception(self, tmp_path: str) -> None:
        """unregister_from_loop: exception on remove_reader(sock) (88-89)."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        wm.create()

        loop = asyncio.new_event_loop()
        try:
            wm.register_with_loop(loop, lambda _: None)
            # Mock remove_reader to raise for the socket fd (second call)
            original_remove = loop.remove_reader
            call_count = [0]

            def failing_remove(fd: int) -> bool:
                call_count[0] += 1
                if call_count[0] == 2:
                    raise RuntimeError("simulated failure")
                return original_remove(fd)

            loop.remove_reader = failing_remove  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[invalid-assignment]
            # Should not raise
            wm.unregister_from_loop(loop)
        finally:
            wm.close()
            loop.close()


class _FakeSock:
    """Minimal socket-like object whose sendto raises a configurable OSError."""

    def __init__(self, errno_val: int) -> None:
        self._errno: int = errno_val

    def sendto(self, _data: bytes, _addr: str) -> int:
        raise OSError(self._errno, os.strerror(self._errno))

    def fileno(self) -> int:
        return -1

    def close(self) -> None:
        pass


class TestWakeupErrnoBranches:
    """Cover wakeup.py errno branches (114-117)."""

    def test_wakeup_remote_eagain(self, tmp_path: str) -> None:
        """wakeup_remote with EAGAIN errno (transient, line 116)."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        wm.create()
        original_sock = wm.wakeup_sock
        wm.wakeup_sock = _FakeSock(errno.EAGAIN)  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[invalid-assignment]
        try:
            # Should not raise — transient errors return silently
            wm.wakeup_remote("/tmp/any.sock")
        finally:
            wm.wakeup_sock = original_sock  # type: ignore[assignment]
            wm.close()

    def test_wakeup_remote_unexpected_errno(self, tmp_path: str) -> None:
        """wakeup_remote with unexpected errno (line 117-121, warning path)."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        wm.create()
        original_sock = wm.wakeup_sock
        wm.wakeup_sock = _FakeSock(errno.EPERM)  # type: ignore[assignment]  # pyright: ignore[reportAttributeAccessIssue]  # ty: ignore[invalid-assignment]
        try:
            # Should not raise — unexpected errno just logs a warning
            wm.wakeup_remote("/tmp/any.sock")
        finally:
            wm.wakeup_sock = original_sock  # type: ignore[assignment]
            wm.close()


class TestWakeupDrainNone:
    """Cover wakeup.py drain branches when fds are None (125->exit, 134)."""

    def test_drain_eventfd_none(self, tmp_path: str) -> None:
        """drain_eventfd when eventfd is None (125->exit)."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        # Don't call create() — eventfd is None
        wm.drain_eventfd()  # should not raise

    def test_drain_socket_none(self, tmp_path: str) -> None:
        """drain_socket when wakeup_sock is None (line 134)."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        # Don't call create() — wakeup_sock is None
        wm.drain_socket()  # should not raise


# ═══════════════════════════════════════════════════════════════════════
# Section 4: channel/manager.py — index full (114-116)
# ═══════════════════════════════════════════════════════════════════════


class TestChannelIndexFull:
    """Cover channel/manager.py:114-116 (index full RuntimeError)."""

    async def test_channel_index_full(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """get_or_create_ring raises RuntimeError when channel index is full."""

        # This fixture is layer_factory, but we need a specific config.
        # Use the factory to create a layer with max_channels=1
        layer = layer_factory(max_channels=1)
        channel_mgr = layer._channel_mgr
        assert channel_mgr is not None
        # First channel fills the index
        _, is_new = channel_mgr.get_or_create_ring("ch1", 10)
        assert is_new
        # Second channel should fail — index full
        with pytest.raises(RuntimeError, match="Channel index full"):
            _ = channel_mgr.get_or_create_ring("ch2", 10)


# ═══════════════════════════════════════════════════════════════════════
# Section 5: group/manager.py — index full, group full, expired member
# ═══════════════════════════════════════════════════════════════════════


class TestGroupManagerErrors:
    """Cover group/manager.py error paths (53-54, 67-68, 107)."""

    async def test_group_index_full(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """group_add raises RuntimeError when group index is full (53-54)."""
        layer = layer_factory(max_groups=1)
        # First group fills the index
        await layer.group_add("g1", "ch1")
        # Second group should fail — index full
        with pytest.raises(RuntimeError, match="Group index full"):
            await layer.group_add("g2", "ch1")

    async def test_group_members_full(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """group_add raises RuntimeError when group is full (67-68)."""
        layer = layer_factory(max_members_per_group=1)
        await layer.group_add("g", "ch1")
        # Second member should fail — group full
        with pytest.raises(RuntimeError, match="Group 'g' full"):
            await layer.group_add("g", "ch2")

    async def test_get_members_expired(self, layer: SharedMemoryChannelLayer) -> None:
        """get_members skips expired members (line 107)."""
        await layer.group_add("g", "ch1")
        group_mgr = layer._group_mgr
        assert group_mgr is not None
        # Mock time.time to return a future time so members appear expired
        with patch(
            "channels_shm.group.manager.time.time", return_value=time.time() + 100000
        ):
            members = group_mgr.get_members("g")
        assert members == [], "expired members should be skipped"


# ═══════════════════════════════════════════════════════════════════════
# Section 6: pump.py — watchdog loop, _on_wakeup branch
# ═══════════════════════════════════════════════════════════════════════


class TestPumpWatchdogLoop:
    """Cover pump.py watchdog loop (102, 109-110, 189-198)."""

    async def test_watchdog_creates_and_cancels_task(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """pump.start creates watchdog task, pump.stop cancels it (102, 109-110)."""
        layer = layer_factory(watchdog_interval=0)
        pump = layer._pump
        assert pump is not None
        loop = asyncio.get_running_loop()
        # watchdog_interval=0 means no task created, but we test the > 0 path
        # by creating a new pump with interval > 0
        from channels_shm.pump import ReceivePump

        region = layer._region
        slab = layer._slab
        channel_mgr = layer._channel_mgr
        wakeup = layer._wakeup
        assert region is not None
        assert slab is not None
        assert channel_mgr is not None
        assert wakeup is not None

        pump2 = ReceivePump(
            region=region.native,
            slab=slab,
            channel_mgr=channel_mgr,
            wakeup=wakeup,
            capacity=layer.capacity,
            expiry=layer.expiry,
            pid=layer.pid,
            start_time=layer.start_time,
            watchdog_interval=1,
        )
        pump2.start(loop)
        assert pump2._watchdog_task is not None
        # Stop should cancel the task
        pump2.stop()
        assert pump2._watchdog_task is None

    async def test_watchdog_loop_executes(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """Watchdog loop body executes and triggers drain (189-198)."""
        layer = layer_factory(watchdog_interval=0)
        pump = layer._pump
        assert pump is not None
        from channels_shm.pump import ReceivePump

        pump2 = ReceivePump(
            region=layer._region.native if layer._region else None,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            slab=layer._slab,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            channel_mgr=layer._channel_mgr,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            wakeup=layer._wakeup,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            capacity=layer.capacity,
            expiry=layer.expiry,
            pid=layer.pid,
            start_time=layer.start_time,
            watchdog_interval=1,
        )
        loop = asyncio.get_running_loop()
        pump2.start(loop)
        # Set old last_drain_ts to trigger watchdog drain
        pump2.last_drain_ts = 0.0
        # Wait for the watchdog to execute (interval=1s, so wait 1.5s)
        await asyncio.sleep(1.5)
        # last_drain_ts should be updated by drain_rings()
        assert pump2.last_drain_ts > 0.0
        pump2.stop()


class TestPumpOnWakeupBranch:
    """Cover pump.py _on_wakeup socket fd branch (179->185)."""

    async def test_on_wakeup_with_unknown_fd(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_on_wakeup with an unknown fd should still call drain_rings."""
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()
        # Call _on_wakeup with a fd that's neither eventfd nor socket
        pump._on_wakeup(99999)
        pump.stop()


# ═══════════════════════════════════════════════════════════════════════
# Section 7: layer.py — config mismatch, registry full, orphan cleanup
# ═══════════════════════════════════════════════════════════════════════


class TestLayerConfigMismatch:
    """Cover layer.py:181-199 (config mismatch reinit)."""

    async def test_config_mismatch_reinit(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """Second layer with different config triggers reinit (181-199)."""
        # First layer with capacity=10
        layer1 = layer_factory(capacity=10)
        # Second layer with different capacity triggers config mismatch
        layer2 = layer_factory(capacity=20)
        assert layer2.capacity == 20
        await layer1.close()
        await layer2.close()

    async def test_corrupted_shm_no_magic(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """shm file with data but no valid magic (200->204 branch)."""
        _ = layer_factory  # fixture used for setup/teardown only
        import uuid

        prefix = f"corrupt_{uuid.uuid4().hex[:8]}"
        shm_path = f"/dev/shm/{prefix}"
        # Pre-create the shm file with garbage data (size > 0, no magic)
        fd = os.open(shm_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.ftruncate(fd, 4096)
        os.close(fd)

        from channels_shm import SharedMemoryChannelLayer

        try:
            # Create layer — should detect size > 0 but no magic, fall through
            layer = SharedMemoryChannelLayer(
                prefix=prefix,
                shm_size=16 * 1024 * 1024,
                max_channels=10,
                max_groups=10,
                max_processes=10,
                max_members_per_group=10,
                watchdog_interval=None,
            )
            await layer.close()
            layer.unlink_shm()
        finally:
            try:
                os.unlink(shm_path)
            except FileNotFoundError:
                pass
            import shutil

            wakeup_dir = f"/dev/shm/{prefix}_wakeup"
            if os.path.isdir(wakeup_dir):
                shutil.rmtree(wakeup_dir, ignore_errors=True)


class TestLayerRegistryFull:
    """Cover layer.py:243 (registry full warning)."""

    async def test_registry_full(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """Second layer with max_processes=1 triggers registry full warning."""
        layer1 = layer_factory(max_processes=1)
        # Second layer should trigger registry full (slot_off == 0)
        layer2 = layer_factory(max_processes=1)
        await layer1.close()
        await layer2.close()


class TestLayerOrphanSocketCleanup:
    """Cover layer.py:289, 294, 299-314 (orphan socket cleanup)."""

    async def test_cleanup_orphan_sockets_no_dir(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_cleanup_orphan_sockets when wakeup dir doesn't exist (289)."""
        # Temporarily change wakeup_dir to a non-existent path
        original_dir = layer._wakeup_dir
        layer._wakeup_dir = "/tmp/nonexistent_wakeup_dir_12345"
        try:
            layer._cleanup_orphan_sockets()  # should not raise
        finally:
            layer._wakeup_dir = original_dir

    async def test_cleanup_orphan_sockets_non_sock_file(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_cleanup_orphan_sockets skips non-.sock files (294)."""
        wakeup_dir = layer._wakeup_dir
        # Create a non-.sock file
        non_sock = os.path.join(wakeup_dir, "not_a_socket.txt")
        with Path(non_sock).open("w") as f:
            _ = f.write("")
        try:
            layer._cleanup_orphan_sockets()
            # File should still exist (not removed)
            assert Path(non_sock).exists()
        finally:
            try:
                os.unlink(non_sock)
            except FileNotFoundError:
                pass

    async def test_cleanup_orphan_sockets_dead_socket(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_cleanup_orphan_sockets removes dead socket files (299-314)."""
        wakeup_dir = layer._wakeup_dir
        # Create a stale .sock file (not bound to any process)
        dead_sock = os.path.join(wakeup_dir, "dead_prefix.sock")
        # Create a regular file (not a real socket) — sendto will fail
        with Path(dead_sock).open("w") as f:
            _ = f.write("")
        try:
            layer._cleanup_orphan_sockets()
            # The dead socket file should be removed
            assert not Path(dead_sock).exists(), "dead socket should be removed"
        finally:
            try:
                os.unlink(dead_sock)
            except FileNotFoundError:
                pass

    async def test_cleanup_orphan_sockets_probe_exception(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_cleanup_orphan_sockets swallows OSError from probe creation (L-14/J-1).

        The except is narrowed to OSError; a generic Exception now propagates
        (deliberately, so logic bugs surface). Use OSError here to match the
        narrowed contract.
        """
        wakeup_dir = layer._wakeup_dir
        dead_sock = os.path.join(wakeup_dir, "exception_prefix.sock")
        with Path(dead_sock).open("w") as f:
            _ = f.write("")
        try:
            # Mock socket.socket to raise an OSError (probe-creation failure)
            with patch(
                "channels_shm.layer._socket.socket",
                side_effect=OSError("simulated"),
            ):
                layer._cleanup_orphan_sockets()
            # Should not raise; file may or may not be removed
        finally:
            try:
                os.unlink(dead_sock)
            except FileNotFoundError:
                pass


# ═══════════════════════════════════════════════════════════════════════
# Section 8: layer.py — _ensure_loop, _wakeup_targets, dead process
# ═══════════════════════════════════════════════════════════════════════


class TestLayerEnsureLoop:
    """Cover layer.py:326, 328->330 (_ensure_loop loop change)."""

    async def test_ensure_loop_changes(self, layer: SharedMemoryChannelLayer) -> None:
        """_ensure_loop handles loop changes with pump.stop() (326->327)."""
        # Start pump on the current loop
        loop1 = asyncio.get_running_loop()
        _ = layer._ensure_loop()
        assert layer._loop is loop1

        pump = layer._pump
        assert pump is not None
        # Simulate a loop change by setting _loop to a fake old loop
        old_loop = asyncio.new_event_loop()
        layer._loop = old_loop
        try:
            # _ensure_loop should detect loop change, call pump.stop(), then pump.start()
            result = layer._ensure_loop()
            assert result is loop1
        finally:
            old_loop.close()
            pump.stop()

    async def test_ensure_loop_pump_none(self, layer: SharedMemoryChannelLayer) -> None:
        """_ensure_loop when pump is None (328->330)."""
        # Close the layer to set _pump = None
        await layer.close()
        # Calling _ensure_loop on a closed layer should not crash
        # (it will try to get_running_loop, but _pump is None so start is skipped)
        try:
            _ = layer._ensure_loop()
        except RuntimeError:
            pass  # Expected if no event loop


class TestLayerWakeupTargetsNone:
    """Cover layer.py:341, 375 (_wakeup_targets/_wakeup_broadcast None checks)."""

    async def test_wakeup_targets_none(self, layer: SharedMemoryChannelLayer) -> None:
        """_wakeup_targets when wakeup/region is None (341)."""
        await layer.close()
        # Should not raise — early return
        layer._wakeup_targets("test_channel")  # type: ignore[unreachable]

    async def test_wakeup_broadcast_none(self, layer: SharedMemoryChannelLayer) -> None:
        """_wakeup_broadcast when region/wakeup is None (375)."""
        await layer.close()
        layer._wakeup_broadcast()  # type: ignore[unreachable]

    async def test_wakeup_by_prefix_none(self, layer: SharedMemoryChannelLayer) -> None:
        """_wakeup_by_prefix when region/wakeup is None (358-368)."""
        await layer.close()
        layer._wakeup_by_prefix("prefix!")  # type: ignore[unreachable]

    async def test_registry_mark_dead_by_path_none(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_registry_mark_dead_by_path when region/lock is None (390-399)."""
        await layer.close()
        layer._registry_mark_dead_by_path("/tmp/test.sock")  # type: ignore[unreachable]


class TestLayerWakeupByPrefix:
    """Cover layer.py:351, 358-368 (_wakeup_by_prefix)."""

    async def test_wakeup_by_prefix_other_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Sending to a process-specific channel with a different prefix (351)."""
        # Create a channel with a different client_prefix
        ch = "specific.OTHERPREFIX!abc123"
        await layer.send(ch, {"type": "msg"})
        # _wakeup_targets should have called _wakeup_by_prefix


class TestLayerDeadProcess:
    """Cover layer.py:383-386, 390-399 (dead process handling)."""

    async def test_wakeup_broadcast_dead_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_wakeup_broadcast with a dead process in registry (383-386, 390-399)."""
        from channels_shm._native import registry_get_valid, registry_register

        region = layer._region
        lock = layer._lock
        assert region is not None
        assert lock is not None
        # Register a fake dead process
        with lock:
            slot_off = registry_register(
                region.native,
                "dead_prefix",
                "/tmp/nonexistent_dead_socket_12345.sock",
                99999,
                0,
                layer.max_processes,
            )
        assert slot_off != 0
        # Call _wakeup_broadcast — should catch DeadProcessError and mark dead
        layer._wakeup_broadcast()
        # Verify the entry was marked dead (no longer in valid entries)
        entries = registry_get_valid(region.native, layer.max_processes)
        paths = [pb.decode("utf-8") for _, pb in entries]
        assert "/tmp/nonexistent_dead_socket_12345.sock" not in paths

    async def test_wakeup_by_prefix_dead_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_wakeup_by_prefix(target_prefix) with a dead owning process (L-03/C-01).

        After the C-01 fix, _wakeup_by_prefix takes the target's CLIENT_PREFIX
        (not the non_local_name) and does a targeted registry lookup; on
        DeadProcessError it marks the slot dead.
        """
        from channels_shm._native import registry_get_valid, registry_register

        region = layer._region
        lock = layer._lock
        assert region is not None
        assert lock is not None
        target_client_prefix = "dead_prefix2"
        with lock:
            slot_off = registry_register(
                region.native,
                target_client_prefix,
                "/tmp/nonexistent_dead_socket_67890.sock",
                99999,
                0,
                layer.max_processes,
            )
        assert slot_off != 0
        # Call _wakeup_by_prefix with the owning client_prefix — should look up
        # the dead socket, sendto fails, DeadProcessError marks the slot dead.
        layer._wakeup_by_prefix(target_client_prefix)
        entries = registry_get_valid(region.native, layer.max_processes)
        paths = [pb.decode("utf-8") for _, pb in entries]
        assert "/tmp/nonexistent_dead_socket_67890.sock" not in paths


# ═══════════════════════════════════════════════════════════════════════
# Section 9: layer.py — send retry, group_send branches
# ═══════════════════════════════════════════════════════════════════════


class TestLayerSendRetry:
    """Cover layer.py:455->457, 468 (send retry paths)."""

    async def test_send_retry_succeeds(self, layer: SharedMemoryChannelLayer) -> None:
        """Send retry succeeds after pump drains (468 break)."""
        ch = "test_retry_success"
        # Start the pump and watch the channel FIRST (initial drain is no-op)
        _ = layer._ensure_loop()
        pump = layer._pump
        assert pump is not None
        _ = pump.watch_channel(ch)
        # Fill to capacity (sends don't yield, so pump doesn't drain between sends)
        for i in range(layer.capacity):
            await layer.send(ch, {"type": "msg", "seq": i})
        # Now send overflow — first try fails, retry succeeds after pump drains
        await layer.send(ch, {"type": "overflow"})
        pump.stop()

    async def test_send_retry_no_wakeup(self, layer: SharedMemoryChannelLayer) -> None:
        """send() rejects a None wakeup (closed/partial state) — E-02/E-04.

        Previously send() omitted wakeup from its None checks, so a closed
        layer would enqueue-then-silently-not-wake. Now _resources() raises
        RuntimeError if any handle (incl. wakeup) is None.
        """
        ch = "test_retry_no_wakeup"
        # Manually set wakeup to None (simulating a partially closed state)
        original_wakeup = layer._wakeup
        layer._wakeup = None
        try:
            with pytest.raises(RuntimeError, match="Channel layer is closed"):
                await layer.send(ch, {"type": "overflow"})
        finally:
            layer._wakeup = original_wakeup


class TestLayerGroupSendBranches:
    """Cover layer.py:582-583, 625->593, 634, 639, 643-644 (group_send)."""

    async def test_group_send_lock_none(self, layer: SharedMemoryChannelLayer) -> None:
        """group_send when lock is None after initial check (582-583)."""
        # Add a member first
        await layer.group_add("g", "ch1")
        # Manually set lock to None (but keep other fields set)
        original_lock = layer._lock
        layer._lock = None
        try:
            with pytest.raises(RuntimeError, match="closed"):
                await layer.group_send("g", {"type": "msg"})
        finally:
            layer._lock = original_lock

    async def test_group_send_broadcast_multiple_channels(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send with multiple non-process-specific channels (625->593)."""
        # Add multiple non-process-specific channels to the group
        await layer.group_add("g", "ch1")
        await layer.group_add("g", "ch2")
        # group_send should wake broadcast once, then skip for the second channel
        await layer.group_send("g", {"type": "msg"})

    async def test_group_send_process_specific_other_prefix(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send with process-specific channel of another prefix (634)."""
        ch = "specific.OTHERPREFIX!abc"
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})

    async def test_group_send_multiple_same_prefix(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send with multiple channels of the same other prefix (639)."""
        ch1 = "specific.SAMEPREFIX!abc"
        ch2 = "specific.SAMEPREFIX!def"
        await layer.group_add("g", ch1)
        await layer.group_add("g", ch2)
        await layer.group_send("g", {"type": "msg"})

    async def test_group_send_dead_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send with dead process in registry (643-644)."""
        from channels_shm._native import registry_register

        region = layer._region
        lock = layer._lock
        assert region is not None
        assert lock is not None
        with lock:
            slot_off = registry_register(
                region.native,
                "dead_prefix3",
                "/tmp/nonexistent_dead_socket_54321.sock",
                99999,
                0,
                layer.max_processes,
            )
        assert slot_off != 0
        # Add a process-specific channel with a different prefix
        ch = "specific.DEADPREFIX3!abc"
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})


# ═══════════════════════════════════════════════════════════════════════
# Section 10: layer.py — flush and compact branches
# ═══════════════════════════════════════════════════════════════════════


class TestLayerFlushBranches:
    """Cover layer.py:674->678, 685, 696, 702->706, 706->exit (flush)."""

    async def test_flush_with_ring_off_zero(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """flush with a slot that has name_len > 0 but ring_off == 0 (674->678)."""
        region = layer._region
        assert region is not None
        native = region.native
        # Manually create a slot with name_len > 0 but ring_off == 0
        ch_off = native.load_u64(56)
        slot_off = ch_off + 0  # First slot
        native.write_u16(slot_off + 8, 5)  # name_len = 5
        native.store_u64(slot_off + 112, 0)  # ring_off = 0
        # flush should handle this gracefully
        await layer.flush()
        # Verify slot was reset
        assert native.read_u16(slot_off + 8) == 0

    async def test_flush_odd_version(self, layer: SharedMemoryChannelLayer) -> None:
        """flush resets stale-odd seqlock versions to a clean 0 baseline (§9.6).

        B-1: flush is now a single Rust call; it resets each slot's version to
        a clean even 0 (not v+1 as the old Python loop did). The point of the
        test — stale-odd slots get cleaned — still holds; the exact reset value
        is the new contract (0).
        """
        region = layer._region
        assert region is not None
        native = region.native
        # CH/GRP slot VERSION offsets (layout.rs, name field is now [u8;128]
        # so version sits at 160). Hardcoded here because the offset isn't in
        # the public native surface; update if the layout changes.
        ch_version_off = 160
        grp_version_off = 160
        # Set up a channel slot with odd version.
        ch_off = native.load_u64(56)  # HDR_CHANNEL_INDEX_OFF
        slot_off = ch_off + 0
        native.write_u16(slot_off + 8, 5)  # CH_SLOT_NAME_LEN > 0
        native.store_u64(slot_off + ch_version_off, 3)  # odd
        # Set up a group slot with odd version.
        grp_off = native.load_u64(64)  # HDR_GROUP_INDEX_OFF
        gslot_off = grp_off + 0
        native.write_u16(gslot_off + 8, 5)  # GRP_SLOT_NAME_LEN > 0
        native.store_u64(gslot_off + grp_version_off, 5)  # odd
        await layer.flush()
        # Rust flush resets versions to the clean 0 baseline.
        assert native.load_u64(slot_off + ch_version_off) == 0
        assert native.load_u64(gslot_off + grp_version_off) == 0

    async def test_flush_pump_none(self, layer: SharedMemoryChannelLayer) -> None:
        """flush with no pump: flush itself runs (the pump.clear is guarded).

        E-04: _resources() raises if any handle is None, so we can't NULL out
        the pump and still call flush. Instead this test verifies flush works
        normally here (the pump guard branch is covered by close()'s cleanup).
        """
        await layer.flush()  # should not raise

    async def test_flush_wakeup_none(self, layer: SharedMemoryChannelLayer) -> None:
        """flush with no wakeup: as above, _resources() now guards, so just
        confirm a normal flush succeeds (the wakeup-broadcast branch is covered
        elsewhere)."""
        await layer.flush()  # should not raise


class TestLayerCompactBranch:
    """Cover layer.py:731->726 (compact: ring_off == 0)."""

    async def test_compact_ring_off_zero(self, layer: SharedMemoryChannelLayer) -> None:
        """compact with a slot that has name_len > 0 but ring_off == 0."""
        region = layer._region
        assert region is not None
        native = region.native
        ch_off = native.load_u64(56)
        slot_off = ch_off + 0
        native.write_u16(slot_off + 8, 5)  # name_len > 0
        native.store_u64(slot_off + 112, 0)  # ring_off = 0
        await layer.compact()  # should not raise


class TestLayerCloseBranch:
    """Cover layer.py:748->747 (close: loop continues when path doesn't match)."""

    async def test_close_with_multiple_registry_entries(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """close() iterates past non-matching entries (749->748)."""
        # Create two layers with the same prefix (shared registry)
        layer1 = layer_factory()
        layer2 = layer_factory()
        # Close layer2 — should iterate past layer1's entry to find its own
        await layer2.close()
        await layer1.close()


class TestLayerRegistryMarkDeadNotFound:
    """Cover layer.py:396->exit (_registry_mark_dead_by_path: path not found)."""

    async def test_registry_mark_dead_not_found(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_registry_mark_dead_by_path with non-existent path (396->exit)."""
        # Should not raise — just returns without finding the path
        layer._registry_mark_dead_by_path("/tmp/nonexistent_path_not_in_registry.sock")


class TestLayerOrphanSocketErrno:
    """Cover layer.py:307->313, 310-311 (orphan cleanup errno branches)."""

    async def test_cleanup_orphan_sockets_unexpected_errno(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_cleanup_orphan_sockets with unexpected errno (307->313)."""
        wakeup_dir = layer._wakeup_dir
        dead_sock = os.path.join(wakeup_dir, "unexpected_errno.sock")
        with Path(dead_sock).open("w") as f:
            _ = f.write("")
        try:
            # Create a mock probe socket whose sendto raises EAGAIN
            class _ErrnoProbe:
                def __init__(self, *args: object, **kwargs: object) -> None:
                    pass

                def sendto(self, _data: bytes, _addr: str) -> int:
                    raise OSError(errno.EAGAIN, "Resource temporarily unavailable")

                def close(self) -> None:
                    pass

            with patch("channels_shm.layer._socket.socket", _ErrnoProbe):
                layer._cleanup_orphan_sockets()
            # File should still exist (errno didn't match dead-process list)
            assert Path(dead_sock).exists()
        finally:
            try:
                os.unlink(dead_sock)
            except FileNotFoundError:
                pass

    async def test_cleanup_orphan_sockets_unlink_not_found(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_cleanup_orphan_sockets: os.unlink raises FileNotFoundError (310-311)."""
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


# ═══════════════════════════════════════════════════════════════════════
# Section 11: layer.py — _wakeup_by_prefix_once None check
# ═══════════════════════════════════════════════════════════════════════


class TestLayerWakeupByPrefixOnceNone:
    """Cover _wakeup_by_prefix None-region/None-wakeup guard (C-01)."""

    async def test_wakeup_by_prefix_none(self, layer: SharedMemoryChannelLayer) -> None:
        """_wakeup_by_prefix is a no-op once the layer is closed (region/wakeup
        both None). The old _wakeup_by_prefix_once helper was removed (C-03);
        _wakeup_by_prefix now does the targeted lookup and guards internally."""
        await layer.close()
        # Should not raise even though region/wakeup are None.
        layer._wakeup_by_prefix("some_client_prefix")  # type: ignore[unreachable]


# ═══════════════════════════════════════════════════════════════════════
# Section 12: pump.py — watchdog loop no-stall branch (193->190)
# ═══════════════════════════════════════════════════════════════════════


class TestPumpWatchdogNoStall:
    """Cover pump.py:193->190 (watchdog loop continues when not stalled)."""

    async def test_watchdog_loop_no_stall(
        self,
        layer_factory: type[SharedMemoryChannelLayer],  # type: ignore[valid-type]
    ) -> None:
        """Watchdog loop when pump is not stalled (193->190)."""
        layer = layer_factory(watchdog_interval=0)
        pump = layer._pump
        assert pump is not None
        from channels_shm.pump import ReceivePump

        pump2 = ReceivePump(
            region=layer._region.native if layer._region else None,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            slab=layer._slab,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            channel_mgr=layer._channel_mgr,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            wakeup=layer._wakeup,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            capacity=layer.capacity,
            expiry=layer.expiry,
            pid=layer.pid,
            start_time=layer.start_time,
            watchdog_interval=1,
        )
        loop = asyncio.get_running_loop()
        pump2.start(loop)
        # Set last_drain_ts to current time so elapsed < 2 * interval
        pump2.last_drain_ts = time.time()
        # Wait for the watchdog to execute (interval=1s)
        await asyncio.sleep(1.2)
        # The watchdog should have run without triggering drain_rings
        # (last_drain_ts was recent, so elapsed < 2 * interval)
        pump2.stop()


# ═══════════════════════════════════════════════════════════════════════
# Section 13: shm/region.py — open(create=False) branch (40->42)
# ═══════════════════════════════════════════════════════════════════════


class TestShmRegionOpenNoCreate:
    """Cover shm/region.py:40->42 (open with create=False)."""

    def test_open_no_create(self, tmp_path: str) -> None:
        """open(create=False) on an existing file (40->42 branch)."""
        from channels_shm.shm.region import ShmRegionHandle

        path = os.path.join(str(tmp_path), "test_no_create")
        # Create the file first with some data
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.ftruncate(fd, 4096)
        os.close(fd)
        # Open without create=True
        handle = ShmRegionHandle(path)
        handle.open(create=False)
        assert handle.size == 4096
        handle.close()
        os.unlink(path)
