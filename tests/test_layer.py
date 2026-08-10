"""Unit and integration tests for channels_shm.layer.

Maps to src/channels_shm/layer.py. Covers configuration validation, the
closed-layer contract, wakeup routing (targeted/broadcast/dead-process),
send/group_send branches, flush/compact robustness, orphan-socket cleanup,
the thread-binding model, concurrent-producer semantics, and the ASGI
spec-compliance behaviors (send/receive/group/flush round-trips; migrated
from tests/test_core.py).
"""

from __future__ import annotations

import asyncio
import errno
import os
import shutil
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from channels_shm import SharedMemoryChannelLayer
from channels_shm._native import (
    registry_get_valid,
    registry_mark_dead,
    registry_register,
)
from channels_shm.exceptions import ChannelFull, MessageTooLarge

if TYPE_CHECKING:
    from collections.abc import Callable

    from channels_shm.serializer import Message

# Layout offsets mirrored from crates/_channels_shm_native/src/layout.rs. These
# white-box tests fabricate corrupted shm states; the offsets are stable ABI
# constants and MUST be updated in lock-step with the Rust layout.
_HDR_CHANNEL_INDEX_OFF = 56  # layout.rs: HDR_CHANNEL_INDEX_OFF
_HDR_GROUP_INDEX_OFF = 64  # layout.rs: HDR_GROUP_INDEX_OFF
_CH_SLOT_NAME_LEN = 8  # layout.rs: CH_SLOT_NAME_LEN
_CH_SLOT_RING_OFFSET = 144  # layout.rs: CH_SLOT_RING_OFFSET
_CH_SLOT_VERSION = 160  # layout.rs: CH_SLOT_VERSION
_GRP_SLOT_NAME_LEN = 8  # layout.rs: GRP_SLOT_NAME_LEN (same offset as CH)
_GRP_SLOT_VERSION = 160  # layout.rs: GRP_SLOT_VERSION


def region_native(layer: SharedMemoryChannelLayer):
    """The native ShmRegion of a live layer (asserts it exists)."""
    region = layer._region
    assert region is not None
    return region.native


def _register_fake_process(
    layer: SharedMemoryChannelLayer, client_prefix: str, sock_path: str
) -> None:
    """Register a registry entry for a (possibly dead) fake process."""
    region = layer._region
    lock = layer._lock
    assert region is not None
    assert lock is not None
    with lock:
        slot_off = registry_register(
            region.native,
            client_prefix,
            sock_path,
            99999,
            0,
            layer.max_processes,
        )
    assert slot_off != 0


class TestPrefixValidation:
    """Configuration guard: AF_UNIX path length limits (§13.1)."""

    def test_prefix_too_long(self) -> None:
        with pytest.raises(ValueError, match="prefix too long"):
            _ = SharedMemoryChannelLayer(prefix="a" * 63)


class TestClosedLayer:
    """Public API contract after close(): RuntimeError, except new_channel."""

    @pytest.fixture
    def closed_layer(self, layer: SharedMemoryChannelLayer) -> SharedMemoryChannelLayer:
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
        """new_channel is pure string construction and stays usable."""
        name = await closed_layer.new_channel()
        assert "!" in name

    async def test_group_add_on_closed(
        self, closed_layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(RuntimeError, match="closed"):
            await closed_layer.group_add("g", "ch")

    async def test_group_discard_on_closed(
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


class TestClose:
    """close() idempotency and registry cleanup."""

    async def test_close_idempotent(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.close()
        await layer.close()  # no-op

    async def test_close_without_registry_entry(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """close() still succeeds when its registry entry was already marked dead."""
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
        await layer.close()

    async def test_close_with_multiple_registry_entries(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """close() finds its OWN registry entry among several."""
        layer1 = layer_factory()
        layer2 = layer_factory()
        await layer2.close()
        await layer1.close()


class TestCompact:
    """compact() smoke behavior on healthy layers."""

    async def test_compact_empty(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.compact()

    async def test_compact_with_channel(self, layer: SharedMemoryChannelLayer) -> None:
        ch = await layer.new_channel("test.")
        await layer.send(ch, {"type": "msg"})
        await layer.compact()


class TestNewChannel:
    """new_channel() naming contract."""

    async def test_returns_unique(self, layer: SharedMemoryChannelLayer) -> None:
        """100 consecutive names must be unique."""
        channels: set[str] = set()
        for _ in range(100):
            ch = await layer.new_channel("test.")
            assert ch not in channels
            channels.add(ch)

    async def test_format(self, layer: SharedMemoryChannelLayer) -> None:
        """Names carry the prefix and the process-specific '!' marker."""
        ch = await layer.new_channel("http.request")
        assert ch.startswith("http.request.")
        assert "!" in ch

    async def test_prefix_without_dot(self, layer: SharedMemoryChannelLayer) -> None:
        """A '.' is appended when the prefix doesn't end with one."""
        ch = await layer.new_channel("custom")
        assert ch.startswith("custom.")

    async def test_prefix_with_bang_rejected(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """'!' would corrupt the process-specific ownership marker (L-07)."""
        with pytest.raises(ValueError, match=r"[!?]"):
            _ = await layer.new_channel("bad!prefix")

    async def test_prefix_with_question_mark_rejected(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(ValueError, match=r"[!?]"):
            _ = await layer.new_channel("bad?prefix")


class TestApiContractGuards:
    """Reserved-key and ownership guards."""

    async def test_send_rejects_reserved_key(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """__asgi_channel__ must not be smuggled into messages (L-18)."""
        ch = await layer.new_channel("test.")
        msg: Message = {"type": "test", "__asgi_channel__": "x"}
        with pytest.raises(ValueError, match="__asgi_channel__"):
            await layer.send(ch, msg)

    async def test_group_send_rejects_reserved_key(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        await layer.group_add("g", "ch")
        msg: Message = {"type": "test", "__asgi_channel__": "x"}
        with pytest.raises(ValueError, match="__asgi_channel__"):
            await layer.group_send("g", msg)

    async def test_receive_other_process_channel(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Receiving on another process's private channel is rejected (L-02)."""
        ch = "specific.OTHERPREFIX!abc123"
        with pytest.raises(ValueError, match="owned by another process"):
            _ = await layer.receive(ch)


class TestEnsureLoop:
    """Event-loop binding model (E-03)."""

    async def test_loop_switch_same_thread(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """A new loop on the same thread stops and restarts the pump."""
        loop1 = asyncio.get_running_loop()
        _ = layer._ensure_loop()
        assert layer._loop is loop1
        pump = layer._pump
        assert pump is not None
        old_loop = asyncio.new_event_loop()
        layer._loop = old_loop
        try:
            result = layer._ensure_loop()
            assert result is loop1
        finally:
            old_loop.close()
            pump.stop()

    async def test_cross_thread_raises(self, layer: SharedMemoryChannelLayer) -> None:
        """A second thread binding must raise RuntimeError (§6.5.3)."""
        _ = layer._ensure_loop()
        errors: list[RuntimeError] = []

        async def _call() -> None:
            _ = layer._ensure_loop()

        def run_in_thread() -> None:
            other_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(other_loop)
                other_loop.run_until_complete(_call())
            except RuntimeError as e:
                errors.append(e)
            finally:
                asyncio.set_event_loop(None)
                other_loop.close()

        await asyncio.to_thread(run_in_thread)
        assert len(errors) == 1
        assert "single thread" in str(errors[0])


class TestWakeupRouting:
    """Targeted / broadcast wakeup, including dead-process marking."""

    async def test_wakeup_by_prefix_other_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Sending to another process's private channel wakes by prefix."""
        ch = "specific.OTHERPREFIX!abc123"
        await layer.send(ch, {"type": "msg"})

    async def test_wakeup_broadcast_dead_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Broadcast marks dead sockets dead in the registry."""
        _register_fake_process(
            layer, "dead_prefix", "/tmp/nonexistent_dead_socket_12345.sock"
        )
        layer._wakeup_broadcast()
        entries = registry_get_valid(region_native(layer), layer.max_processes)
        paths = [pb.decode("utf-8") for _, pb in entries]
        assert "/tmp/nonexistent_dead_socket_12345.sock" not in paths

    async def test_wakeup_by_prefix_dead_process(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Targeted wakeup of a dead owner marks its slot dead (L-03)."""
        target_client_prefix = "dead_prefix2"
        _register_fake_process(
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
        """send() must not silently lose wakeups on a torn-down layer (E-04)."""
        original_wakeup = layer._wakeup
        layer._wakeup = None
        try:
            with pytest.raises(RuntimeError, match="Channel layer is closed"):
                await layer.send("test_retry_no_wakeup", {"type": "overflow"})
        finally:
            layer._wakeup = original_wakeup


class TestGroupSend:
    """group_send routing: empty, self, other-prefix, broadcast, full, errors."""

    async def test_to_empty_group(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.group_send("nonexistent", {"type": "msg"})

    async def test_to_self_channel(self, layer: SharedMemoryChannelLayer) -> None:
        ch = await layer.new_channel("test.")
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})

    async def test_to_other_process_channel(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        ch = "test.other_prefix!abc123"
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})

    async def test_broadcast(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.group_add("g", "regular_channel")
        await layer.group_send("g", {"type": "msg"})

    async def test_broadcast_multiple_channels(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        await layer.group_add("g", "ch1")
        await layer.group_add("g", "ch2")
        await layer.group_send("g", {"type": "msg"})

    async def test_multiple_same_other_prefix(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Multiple channels of the same other prefix wake that owner once."""
        await layer.group_add("g", "specific.SAMEPREFIX!abc")
        await layer.group_add("g", "specific.SAMEPREFIX!def")
        await layer.group_send("g", {"type": "msg"})

    async def test_channel_full_silently_skips(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send never raises ChannelFull for a full member (§7.4)."""
        ch = await layer.new_channel("test.")
        await layer.group_add("g", ch)
        for _ in range(layer.capacity):
            await layer.send(ch, {"type": "msg"})
        await layer.group_send("g", {"type": "msg"})

    async def test_message_too_large(self, layer: SharedMemoryChannelLayer) -> None:
        await layer.group_add("g", "ch")
        big_msg: Message = {"type": "big", "data": "x" * (1024 * 1024)}
        with pytest.raises(MessageTooLarge):
            await layer.group_send("g", big_msg)

    async def test_non_dict_message(self, layer: SharedMemoryChannelLayer) -> None:
        with pytest.raises(TypeError):
            await layer.group_send("g", "not a dict")  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

    async def test_lock_none_raises(self, layer: SharedMemoryChannelLayer) -> None:
        """A torn-down lock must not be silently dereferenced."""
        await layer.group_add("g", "ch1")
        original_lock = layer._lock
        layer._lock = None
        try:
            with pytest.raises(RuntimeError, match="closed"):
                await layer.group_send("g", {"type": "msg"})
        finally:
            layer._lock = original_lock

    async def test_dead_process_member(self, layer: SharedMemoryChannelLayer) -> None:
        """group_send to a group containing a dead owner's channel."""
        _register_fake_process(
            layer, "dead_prefix3", "/tmp/nonexistent_dead_socket_54321.sock"
        )
        ch = "specific.DEADPREFIX3!abc"
        await layer.group_add("g", ch)
        await layer.group_send("g", {"type": "msg"})


class TestConfigMismatch:
    """First-process init: config mismatch and corrupted shm handling."""

    async def test_config_mismatch_reinit(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """A second layer with a different capacity triggers a rebuild."""
        layer1 = layer_factory(capacity=10)
        layer2 = layer_factory(capacity=20)
        assert layer2.capacity == 20
        await layer1.close()
        await layer2.close()

    async def test_corrupted_shm_no_magic(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """A shm file with data but no magic is reinitialized, not crashed on."""
        _ = layer_factory  # fixture used for setup/teardown only
        prefix = f"corrupt_{uuid.uuid4().hex[:8]}"
        shm_path = f"/dev/shm/{prefix}"
        fd = os.open(shm_path, os.O_RDWR | os.O_CREAT, 0o600)
        os.ftruncate(fd, 4096)
        os.close(fd)
        try:
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
            wakeup_dir = f"/dev/shm/{prefix}_wakeup"
            if os.path.isdir(wakeup_dir):
                shutil.rmtree(wakeup_dir, ignore_errors=True)


class TestRegistryFull:
    """max_processes exhaustion degrades to a warning, not a crash."""

    async def test_registry_full(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        layer1 = layer_factory(max_processes=1)
        layer2 = layer_factory(max_processes=1)
        await layer1.close()
        await layer2.close()


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
        """OSError during probe-socket creation is swallowed (L-14/J-1)."""
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


class TestFlushRobustness:
    """flush() must tolerate corrupted slot states (B-1)."""

    async def test_flush_with_ring_off_zero(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """A slot marked occupied but with no ring is reset, not crashed on."""
        native = region_native(layer)
        slot_off = native.load_u64(_HDR_CHANNEL_INDEX_OFF) + 0
        native.write_u16(slot_off + _CH_SLOT_NAME_LEN, 5)  # occupied
        native.store_u64(slot_off + _CH_SLOT_RING_OFFSET, 0)  # no ring (corrupt)
        await layer.flush()
        assert native.read_u16(slot_off + _CH_SLOT_NAME_LEN) == 0
        assert native.load_u64(slot_off + _CH_SLOT_RING_OFFSET) == 0

    async def test_flush_odd_version(self, layer: SharedMemoryChannelLayer) -> None:
        """Stale-odd seqlock versions are reset to a clean 0 baseline (§9.6)."""
        native = region_native(layer)
        ch_off = native.load_u64(_HDR_CHANNEL_INDEX_OFF)
        slot_off = ch_off + 0
        native.write_u16(slot_off + _CH_SLOT_NAME_LEN, 5)  # occupied
        native.store_u64(slot_off + _CH_SLOT_VERSION, 3)  # stale odd
        grp_off = native.load_u64(_HDR_GROUP_INDEX_OFF)
        gslot_off = grp_off + 0
        native.write_u16(gslot_off + _GRP_SLOT_NAME_LEN, 5)
        native.store_u64(gslot_off + _GRP_SLOT_VERSION, 5)
        await layer.flush()
        assert native.load_u64(slot_off + _CH_SLOT_VERSION) == 0
        assert native.load_u64(gslot_off + _GRP_SLOT_VERSION) == 0


class TestCompactRobustness:
    """compact() is non-destructive: it repairs rings, never slot fields."""

    async def test_compact_ring_off_zero(self, layer: SharedMemoryChannelLayer) -> None:
        """A slot marked occupied but with no ring is skipped untouched."""
        native = region_native(layer)
        slot_off = native.load_u64(_HDR_CHANNEL_INDEX_OFF) + 0
        native.write_u16(slot_off + _CH_SLOT_NAME_LEN, 5)  # occupied
        native.store_u64(slot_off + _CH_SLOT_RING_OFFSET, 0)  # no ring
        await layer.compact()  # should not raise
        assert native.read_u16(slot_off + _CH_SLOT_NAME_LEN) == 5  # untouched
        assert native.load_u64(slot_off + _CH_SLOT_RING_OFFSET) == 0


class TestSpecCompliance:
    """ASGI channel-layer spec behaviors (migrated from test_core.py)."""

    async def test_send_receive_basic(self, layer: SharedMemoryChannelLayer) -> None:
        """Basic send and receive should work."""
        channel = await layer.new_channel("test.")
        await layer.send(channel, {"type": "hello", "data": "world"})
        msg = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
        assert msg == {"type": "hello", "data": "world"}

    async def test_send_receive_fifo(self, layer: SharedMemoryChannelLayer) -> None:
        """Messages should be received in FIFO order."""
        channel = await layer.new_channel("test.")
        messages: list[Message] = [{"type": "msg", "seq": i} for i in range(5)]
        for msg in messages:
            await layer.send(channel, msg)

        received: list[Message] = []
        for _ in range(5):
            msg = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
            received.append(msg)

        assert received == messages

    async def test_send_dict_required(self, layer: SharedMemoryChannelLayer) -> None:
        """send() must raise TypeError for non-dict messages."""
        channel = await layer.new_channel("test.")
        with pytest.raises(TypeError):
            await layer.send(channel, "not a dict")  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

    async def test_send_message_too_large(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """send() must raise MessageTooLarge for messages > 1MB."""
        channel = await layer.new_channel("test.")
        big_msg: Message = {"type": "big", "data": "x" * (1024 * 1024)}
        with pytest.raises(MessageTooLarge):
            await layer.send(channel, big_msg)

    async def test_send_channel_name_validation(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """send() must reject invalid channel names."""
        with pytest.raises(TypeError):
            await layer.send("", {"type": "test"})
        with pytest.raises(TypeError):
            await layer.send("invalid name with spaces", {"type": "test"})

    async def test_receive_channel_name_validation(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """receive() must reject invalid channel names."""
        with pytest.raises(TypeError):
            _ = await layer.receive("")

    async def test_message_types(self, layer: SharedMemoryChannelLayer) -> None:
        """All spec-required types should round-trip correctly."""
        channel = await layer.new_channel("test.")
        msg: Message = {
            "type": "test",
            "none_val": None,
            "bool_val": True,
            "int_val": 42,
            "float_val": 3.14,
            "str_val": "hello",
            "bytes_val": b"binary",
            "list_val": [1, 2, 3],
            "dict_val": {"nested": True},
        }
        await layer.send(channel, msg)
        received = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
        assert received["type"] == "test"
        assert received["none_val"] is None
        assert received["bool_val"] is True
        assert received["int_val"] == 42
        assert received["float_val"] == pytest.approx(3.14)
        assert received["str_val"] == "hello"
        assert received["bytes_val"] == b"binary"
        assert received["list_val"] == [1, 2, 3]
        assert received["dict_val"] == {"nested": True}

    async def test_tuple_becomes_list(self, layer: SharedMemoryChannelLayer) -> None:
        """Tuples should be encoded as lists (msgpack behavior, §8.4)."""
        channel = await layer.new_channel("test.")
        await layer.send(
            channel,
            {"type": "test", "data": (1, 2, 3)},  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
        )
        received = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
        assert received["data"] == [1, 2, 3]

    async def test_int_overflow(self, layer: SharedMemoryChannelLayer) -> None:
        """Integers outside 64-bit signed range should raise OverflowError."""
        channel = await layer.new_channel("test.")
        with pytest.raises(OverflowError):
            await layer.send(channel, {"type": "test", "val": 2**64})

    async def test_flush_resets_layer(self, layer: SharedMemoryChannelLayer) -> None:
        """flush() resets the layer; sending still works afterwards."""
        channel = "test_flush"
        await layer.send(channel, {"type": "before_flush"})
        await layer.flush()
        await layer.send(channel, {"type": "after_flush"})

    async def test_group_add_send_discard(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Group operations should work correctly."""
        channel = await layer.new_channel("test.")
        await layer.group_add("test_group", channel)
        await layer.group_send("test_group", {"type": "group_msg", "data": "hello"})
        msg = await asyncio.wait_for(layer.receive(channel), timeout=5.0)
        assert msg == {"type": "group_msg", "data": "hello"}
        await layer.group_discard("test_group", channel)

    async def test_group_send_multiple_channels(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """group_send() should deliver to all group members."""
        channels: list[str] = []
        for _ in range(3):
            ch = await layer.new_channel("test.")
            await layer.group_add("multi_group", ch)
            channels.append(ch)

        await layer.group_send("multi_group", {"type": "broadcast"})

        for ch in channels:
            msg = await asyncio.wait_for(layer.receive(ch), timeout=5.0)
            assert msg == {"type": "broadcast"}

    def test_group_expiry_attribute(self, layer: SharedMemoryChannelLayer) -> None:
        """group_expiry must be exposed as an attribute."""
        assert hasattr(layer, "group_expiry")
        assert isinstance(layer.group_expiry, int)

    def test_extensions_attribute(self, layer: SharedMemoryChannelLayer) -> None:
        """extensions must include 'groups' and 'flush'."""
        assert "groups" in layer.extensions
        assert "flush" in layer.extensions
