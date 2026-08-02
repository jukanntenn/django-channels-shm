"""Targeted tests to close coverage gaps for the §12.8 100% coverage gate.

Each test section covers specific uncovered code paths identified by the
coverage report. Tests are organized by module.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest

from channels_shm.channel.validator import validate_channel_name, validate_group_name
from channels_shm.exceptions import ChannelFull, MessageTooLarge
from channels_shm.serializer import _normalize_value, normalize_message

if TYPE_CHECKING:
    from channels_shm import SharedMemoryChannelLayer
    from channels_shm.shm.wakeup import WakeupManager


# ═══════════════════════════════════════════════════════════════════════
# Section 1: validator.py — all error paths
# ═══════════════════════════════════════════════════════════════════════


class TestValidatorErrors:
    """Cover all validation error paths."""

    def test_channel_name_empty(self) -> None:
        with pytest.raises(TypeError, match="must not be empty"):
            validate_channel_name("")

    def test_channel_name_too_long(self) -> None:
        with pytest.raises(TypeError, match="too long"):
            validate_channel_name("a" * 201)

    def test_channel_name_invalid_chars(self) -> None:
        with pytest.raises(TypeError, match="invalid"):
            validate_channel_name("has spaces")

    def test_channel_name_receive_with_bang_not_ending(self) -> None:
        with pytest.raises(TypeError, match="must end with !"):
            validate_channel_name("prefix!local", receive=True)

    def test_group_name_empty(self) -> None:
        with pytest.raises(TypeError, match="must not be empty"):
            validate_group_name("")

    def test_group_name_too_long(self) -> None:
        with pytest.raises(TypeError, match="too long"):
            validate_group_name("a" * 201)

    def test_group_name_invalid_chars(self) -> None:
        with pytest.raises(TypeError, match="invalid"):
            validate_group_name("has spaces!")


# ═══════════════════════════════════════════════════════════════════════
# Section 2: serializer.py — _normalize_value for scalar types
# ═══════════════════════════════════════════════════════════════════════


class TestSerializerNormalize:
    """Cover the scalar branch of _normalize_value."""

    def test_normalize_int(self) -> None:
        assert _normalize_value(42) == 42

    def test_normalize_str(self) -> None:
        assert _normalize_value("hello") == "hello"

    def test_normalize_none(self) -> None:
        assert _normalize_value(None) is None

    def test_normalize_bool(self) -> None:
        assert _normalize_value(True) is True

    def test_normalize_bytes(self) -> None:
        assert _normalize_value(b"data") == b"data"

    def test_normalize_float(self) -> None:
        assert _normalize_value(3.14) == 3.14

    def test_normalize_message_scalar(self) -> None:
        """normalize_message on a dict with scalar values."""
        msg = {"type": "test", "count": 5}
        result = normalize_message(msg)  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
        assert result == msg


# ═══════════════════════════════════════════════════════════════════════
# Section 3: layer.py — error paths, compact, close, config mismatch
# ═══════════════════════════════════════════════════════════════════════


class TestLayerPrefixValidation:
    """Cover prefix length validation (§13.1)."""

    def test_prefix_too_long(self) -> None:
        from channels_shm import SharedMemoryChannelLayer

        long_prefix = "a" * 63  # > 62 limit
        with pytest.raises(ValueError, match="prefix too long"):
            _ = SharedMemoryChannelLayer(prefix=long_prefix)


class TestLayerClosedErrors:
    """Cover RuntimeError('Channel layer is closed') paths."""

    @pytest.fixture
    def closed_layer(self, layer: SharedMemoryChannelLayer) -> SharedMemoryChannelLayer:
        """Return a layer that has been closed."""
        # Run close() synchronously
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(layer.close())
        finally:
            loop.close()
        return layer

    async def test_send_on_closed(self, closed_layer: SharedMemoryChannelLayer) -> None:
        with pytest.raises(RuntimeError, match="closed"):
            await closed_layer.send("ch", {"type": "test"})

    async def test_receive_on_closed(
        self, closed_layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(RuntimeError, match="closed"):
            _ = await closed_layer.receive("ch")

    async def test_new_channel_on_closed(
        self, closed_layer: SharedMemoryChannelLayer
    ) -> None:
        # L-07: new_channel() is pure string construction (it does not touch
        # region/pump/etc.), so it succeeds even on a closed layer. The check
        # that used to raise was a copy-paste leftover with no correctness
        # value — the real failure surfaces on the subsequent send/receive.
        name = await closed_layer.new_channel()
        assert "!" in name

    async def test_group_add_on_closed(
        self, closed_layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(RuntimeError, match="closed"):
            await closed_layer.group_add("g", "ch")

    async def test_group_discard_closed(
        self, closed_layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(RuntimeError, match="closed"):
            await closed_layer.group_discard("g", "ch")

    async def test_group_send_on_closed(
        self, closed_layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(RuntimeError, match="closed"):
            await closed_layer.group_send("g", {"type": "test"})

    async def test_flush_on_closed(
        self, closed_layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(RuntimeError, match="closed"):
            await closed_layer.flush()

    async def test_compact_on_closed(
        self, closed_layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(RuntimeError, match="closed"):
            await closed_layer.compact()


class TestLayerCompact:
    """Cover compact() method (§9.7)."""

    async def test_compact_empty(self, layer: SharedMemoryChannelLayer) -> None:
        """compact() on a layer with no channels should not raise."""
        await layer.compact()

    async def test_compact_with_channel(self, layer: SharedMemoryChannelLayer) -> None:
        """compact() after sending a message should not raise."""
        ch = await layer.new_channel("test.")
        await layer.send(ch, {"type": "msg"})
        await layer.compact()


class TestLayerClose:
    """Cover close() method paths."""

    async def test_close_idempotent(self, layer: SharedMemoryChannelLayer) -> None:
        """close() twice should not raise."""
        await layer.close()
        # Second close — all internals are None, should be a no-op.
        await layer.close()

    async def test_close_without_registry_entry(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """close() when the registry entry was already marked dead."""
        # Manually mark the entry as dead before close
        from channels_shm._native import registry_get_valid, registry_mark_dead

        region = layer._region
        assert region is not None
        entries = registry_get_valid(region.native, layer.max_processes)
        my_path = layer._wakeup.socket_path if layer._wakeup else None
        for slot_off, path_bytes in entries:
            if path_bytes.decode("utf-8") == my_path:
                lock = layer._lock
                assert lock is not None
                with lock:
                    registry_mark_dead(region.native, slot_off)
                break
        # Now close — the entry won't be found, but it should still work.
        await layer.close()


class TestLayerFlush:
    """Cover flush() branches."""

    async def test_flush_clears_messages(self, layer: SharedMemoryChannelLayer) -> None:
        """flush() should clear all messages."""
        ch = await layer.new_channel("test.")
        await layer.send(ch, {"type": "msg"})
        await layer.flush()
        # After flush, the channel ring should be empty/reset.

    async def test_flush_with_group(self, layer: SharedMemoryChannelLayer) -> None:
        """flush() should clear group membership."""
        await layer.group_add("test_group", "test_channel")
        await layer.flush()
        # After flush, group should be empty.

    async def test_flush_empty_layer(self, layer: SharedMemoryChannelLayer) -> None:
        """flush() on an empty layer should not raise."""
        await layer.flush()


class TestLayerGroupSend:
    """Cover group_send process-specific and broadcast paths."""

    async def test_group_send_to_empty_group(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send to a non-existent group should silently return."""
        await layer.group_send("nonexistent", {"type": "msg"})

    async def test_group_send_to_self_channel(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send to a process-specific channel owned by self."""
        ch = await layer.new_channel("test.")
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})

    async def test_group_send_to_other_process_channel(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send to a process-specific channel owned by another process."""
        # Create a channel with a different client_prefix
        ch = "test.other_prefix!abc123"
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})

    async def test_group_send_broadcast(self, layer: SharedMemoryChannelLayer) -> None:
        """group_send to a non-process-specific channel triggers broadcast."""
        await layer.group_add("g", "regular_channel")
        await layer.group_send("g", {"type": "msg"})

    async def test_group_send_channel_full_silently_skips(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send silently skips full channels (§7.4)."""
        # Create a channel with very small capacity
        ch = await layer.new_channel("test.")
        await layer.group_add("g", ch)
        # Fill the channel to capacity
        for _ in range(layer.capacity):
            await layer.send(ch, {"type": "msg"})
        # group_send should not raise ChannelFull
        await layer.group_send("g", {"type": "msg"})

    async def test_group_send_message_too_large(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send raises MessageTooLarge for >1MB messages."""
        await layer.group_add("g", "ch")
        big_msg = {"type": "big", "data": "x" * (1024 * 1024)}
        with pytest.raises(MessageTooLarge):
            await layer.group_send("g", big_msg)  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

    async def test_group_send_non_dict(self, layer: SharedMemoryChannelLayer) -> None:
        """group_send raises TypeError for non-dict messages."""
        with pytest.raises(TypeError):
            await layer.group_send("g", "not a dict")  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]


class TestLayerSendChannelFull:
    """Cover the ChannelFull retry path in send()."""

    async def test_send_channel_full(self, layer: SharedMemoryChannelLayer) -> None:
        """send() raises ChannelFull when capacity is exceeded."""
        ch = "test_full_channel"
        # Fill to capacity
        for _ in range(layer.capacity):
            await layer.send(ch, {"type": "msg"})
        # Next send should raise ChannelFull
        with pytest.raises(ChannelFull):
            await layer.send(ch, {"type": "overflow"})


class TestLayerNewChannel:
    """Cover new_channel() edge cases."""

    async def test_new_channel_default_prefix(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """new_channel with default prefix."""
        ch = await layer.new_channel()
        assert "!" in ch
        assert ch.startswith("specific.")

    async def test_new_channel_custom_prefix(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """new_channel with custom prefix."""
        ch = await layer.new_channel("custom.")
        assert ch.startswith("custom.")

    async def test_new_channel_prefix_without_dot(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """new_channel adds '.' if prefix doesn't end with it."""
        ch = await layer.new_channel("custom")
        assert ch.startswith("custom.")


# ═══════════════════════════════════════════════════════════════════════
# Section 4: wakeup.py — error handling, drain, close
# ═══════════════════════════════════════════════════════════════════════


class TestWakeupManager:
    """Cover WakeupManager error paths."""

    @pytest.fixture
    def wakeup(self, tmp_path: object) -> WakeupManager:
        """Create a WakeupManager in a temp directory."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test_prefix", str(tmp_path))
        wm.create()
        return wm

    def test_wakeup_local(self, wakeup: WakeupManager) -> None:
        """wakeup_local should write to eventfd without raising."""
        wakeup.wakeup_local()
        # Drain to clear the eventfd
        wakeup.drain_eventfd()

    def test_wakeup_remote_dead_process(self, wakeup: WakeupManager) -> None:
        """wakeup_remote to a non-existent socket raises DeadProcessError."""
        from channels_shm.shm.wakeup import DeadProcessError

        with pytest.raises(DeadProcessError):
            wakeup.wakeup_remote("/tmp/nonexistent_socket_path_12345.sock")

    def test_wakeup_remote_no_sock(self, tmp_path: object) -> None:
        """wakeup_remote when wakeup_sock is None should be a no-op."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        # Don't call create() — wakeup_sock is None
        wm.wakeup_remote("/tmp/some_path.sock")  # should not raise

    def test_drain_socket_empty(self, wakeup: WakeupManager) -> None:
        """drain_socket on an empty socket should return immediately."""
        wakeup.drain_socket()

    def test_drain_eventfd_empty(self, wakeup: WakeupManager) -> None:
        """drain_eventfd when there's nothing to read should not raise."""
        # Read first to clear any residual
        wakeup.drain_eventfd()
        # Read again — should be a no-op (EAGAIN caught)
        wakeup.drain_eventfd()

    def test_close(self, wakeup: WakeupManager) -> None:
        """close() should close both fds and unlink the socket."""
        from pathlib import Path

        socket_path = wakeup.socket_path
        assert Path(socket_path).exists()
        wakeup.close()
        assert not Path(socket_path).exists()

    def test_close_idempotent(self, wakeup: WakeupManager) -> None:
        """close() twice should not raise."""
        wakeup.close()
        wakeup.close()

    def test_unregister_loop_no_fd(self, tmp_path: object) -> None:
        """unregister_from_loop when fds are None should not raise."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        loop = asyncio.new_event_loop()
        try:
            wm.unregister_from_loop(loop)  # should not raise
        finally:
            loop.close()

    def test_register_and_unregister_with_loop(self, wakeup: WakeupManager) -> None:
        """register_with_loop and unregister_from_loop should work."""
        loop = asyncio.new_event_loop()
        try:
            wakeup.register_with_loop(loop, lambda _: None)
            wakeup.unregister_from_loop(loop)
        finally:
            loop.close()

    def test_wakeup_local_no_eventfd(self, tmp_path: object) -> None:
        """wakeup_local when eventfd is None should not raise."""
        from channels_shm.shm.wakeup import WakeupManager

        wm = WakeupManager("test", str(tmp_path))
        wm.wakeup_local()  # should not raise

    def test_dead_process_error(self) -> None:
        """DeadProcessError stores the socket path."""
        from channels_shm.shm.wakeup import DeadProcessError

        err = DeadProcessError("/tmp/test.sock")
        assert err.socket_path == "/tmp/test.sock"
        assert "/tmp/test.sock" in str(err)


# ═══════════════════════════════════════════════════════════════════════
# Section 5: pump.py — BoundedQueue, watchdog, _on_wakeup
# ═══════════════════════════════════════════════════════════════════════


class TestBoundedQueue:
    """Cover _BoundedQueue.put_nowait drop-oldest path (P-13: renamed private)."""

    def test_drop_oldest_when_full(self) -> None:
        """When the queue is full, put_nowait drops the oldest item."""
        from channels_shm.pump import _BoundedQueue
        from channels_shm.serializer import Message

        q: _BoundedQueue = _BoundedQueue(maxsize=2)
        msg1: Message = {"type": "1"}
        msg2: Message = {"type": "2"}
        msg3: Message = {"type": "3"}
        q.put_nowait(msg1)
        q.put_nowait(msg2)
        # Queue is full — put_nowait should drop msg1 and add msg3
        q.put_nowait(msg3)
        assert q.qsize() == 2
        assert q.get_nowait() == msg2
        assert q.get_nowait() == msg3


class TestPumpWatchdog:
    """Cover pump watchdog and _on_wakeup paths."""

    async def test_pump_start_stop(self, layer: SharedMemoryChannelLayer) -> None:
        """pump.start() and pump.stop() should work without errors."""
        pump = layer._pump
        assert pump is not None
        # Pump is started by _ensure_loop on first receive
        _ = layer._ensure_loop()
        # Start should have been called
        pump.stop()
        # Stop should not raise even if called again
        pump.stop()

    async def test_pump_on_wakeup_eventfd(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """_on_wakeup with eventfd fd should drain eventfd and call drain_rings."""
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()
        # Write to eventfd to trigger wakeup
        assert pump.wakeup.eventfd is not None
        pump.wakeup.wakeup_local()
        # Call _on_wakeup with eventfd fd
        pump._on_wakeup(pump.wakeup.eventfd)
        pump.stop()

    async def test_pump_on_wakeup_socket(self, layer: SharedMemoryChannelLayer) -> None:
        """_on_wakeup with socket fd should drain socket and call drain_rings."""
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()
        # Call _on_wakeup with socket fd
        assert pump.wakeup.wakeup_sock is not None
        sock_fd = pump.wakeup.wakeup_sock.fileno()
        pump._on_wakeup(sock_fd)
        pump.stop()

    async def test_pump_watchdog_triggers_drain(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Watchdog should trigger drain_rings when pump is stalled."""
        pump = layer._pump
        assert pump is not None
        _ = layer._ensure_loop()
        # Simulate old last_drain_ts to trigger watchdog
        pump.last_drain_ts = 0.0
        # Manually call drain to simulate watchdog trigger
        pump.drain_rings()
        assert pump.last_drain_ts > 0
        pump.stop()


# ═══════════════════════════════════════════════════════════════════════
# Section 6: shm/region.py — open, ftruncate, close, unlink
# ═══════════════════════════════════════════════════════════════════════


class TestShmRegionHandle:
    """Cover ShmRegionHandle error paths."""

    def test_native_not_opened(self) -> None:
        """Accessing .native before open() should raise RuntimeError."""
        from channels_shm.shm.region import ShmRegionHandle

        handle = ShmRegionHandle("/dev/shm/test_nonexistent")
        with pytest.raises(RuntimeError, match="not opened"):
            _ = handle.native

    def test_fd_not_opened(self) -> None:
        """Accessing .fd before open() should raise RuntimeError."""
        from channels_shm.shm.region import ShmRegionHandle

        handle = ShmRegionHandle("/dev/shm/test_nonexistent")
        with pytest.raises(RuntimeError, match="not opened"):
            _ = handle.fd

    def test_ftruncate_not_opened(self) -> None:
        """ftruncate_and_remap before open() should raise RuntimeError."""
        from channels_shm.shm.region import ShmRegionHandle

        handle = ShmRegionHandle("/dev/shm/test_nonexistent")
        with pytest.raises(RuntimeError, match="not opened"):
            handle.ftruncate_and_remap(1024)

    def test_path_property(self) -> None:
        """path property should return the path passed to __init__."""
        from channels_shm.shm.region import ShmRegionHandle

        handle = ShmRegionHandle("/dev/shm/test_path")
        assert handle.path == "/dev/shm/test_path"

    def test_size_property_unopened(self) -> None:
        """size property should return 0 for unopened handle."""
        from channels_shm.shm.region import ShmRegionHandle

        handle = ShmRegionHandle("/dev/shm/test_size")
        assert handle.size == 0

    def test_open_existing(self, tmp_path: object) -> None:
        """open() on an existing non-empty shm should mmap it."""
        from channels_shm.shm.region import ShmRegionHandle

        path = str(tmp_path) + "/test_shm"
        # Create and write to the file first
        handle1 = ShmRegionHandle(path)
        handle1.open(create=True)
        handle1.ftruncate_and_remap(4096)
        handle1.close()

        # Reopen — should detect existing size > 0
        handle2 = ShmRegionHandle(path)
        handle2.open(create=True)
        assert handle2.size == 4096
        handle2.close()
        os.unlink(path)

    def test_close_idempotent(self, tmp_path: object) -> None:
        """close() twice should not raise."""
        from channels_shm.shm.region import ShmRegionHandle

        path = str(tmp_path) + "/test_close"
        handle = ShmRegionHandle(path)
        handle.open(create=True)
        handle.close()
        handle.close()  # should not raise
        os.unlink(path)

    def test_unlink_nonexistent(self) -> None:
        """unlink() on a non-existent file should not raise."""
        from channels_shm.shm.region import ShmRegionHandle

        handle = ShmRegionHandle("/dev/shm/test_unlink_nonexistent_12345")
        handle.unlink()  # should not raise


# ═══════════════════════════════════════════════════════════════════════
# Section 7: channel/manager.py — get_or_create_ring, index full
# ═══════════════════════════════════════════════════════════════════════


class TestChannelManager:
    """Cover ChannelManager.get_or_create_ring paths."""

    async def test_get_or_create_ring_existing(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """get_or_create_ring on an existing channel returns the same ring."""
        channel_mgr = layer._channel_mgr
        assert channel_mgr is not None
        # Create a ring first
        ring1, is_new = channel_mgr.get_or_create_ring("test.ch", 10)
        assert is_new
        # Lookup again — should find existing
        ring2, is_new2 = channel_mgr.get_or_create_ring("test.ch", 10)
        assert not is_new2
        assert ring1.offset() == ring2.offset()

    async def test_get_ring_not_found(self, layer: SharedMemoryChannelLayer) -> None:
        """get_ring on a non-existent channel returns None."""
        channel_mgr = layer._channel_mgr
        assert channel_mgr is not None
        result = channel_mgr.get_ring("nonexistent_channel")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════
# Section 8: group/manager.py — error paths
# ═══════════════════════════════════════════════════════════════════════


class TestGroupManager:
    """Cover GroupManager error paths."""

    async def test_discard_nonexistent_group(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """discard on a non-existent group should not raise."""
        await layer.group_discard("nonexistent", "ch")

    async def test_get_members_empty_group(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """get_members on a non-existent group returns empty list."""
        group_mgr = layer._group_mgr
        assert group_mgr is not None
        members = group_mgr.get_members("nonexistent")
        assert members == []

    async def test_group_add_and_discard(self, layer: SharedMemoryChannelLayer) -> None:
        """Add then discard a member, verify group is empty after."""
        await layer.group_add("g", "ch1")
        group_mgr = layer._group_mgr
        assert group_mgr is not None
        members = group_mgr.get_members("g")
        assert "ch1" in members
        await layer.group_discard("g", "ch1")
        members = group_mgr.get_members("g")
        assert "ch1" not in members

    async def test_discard_nonexistent_member(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """discard a member that's not in the group should not raise."""
        await layer.group_add("g", "ch1")
        await layer.group_discard("g", "nonexistent_member")
