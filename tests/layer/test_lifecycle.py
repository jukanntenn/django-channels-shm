"""Lifecycle tests: closed-layer contract, close idempotency, compact/flush
robustness, and the event-loop binding model.

Maps to src/channels_shm/layer.py lifecycle + the loop-binding guard (E-03).
The flush/compact robustness tests fabricate corrupted shm states via
tests/layout_helpers (offsets derived from exposed ABI constants, not magic
numbers).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tests.layout_helpers import (
    CH_SLOT_NAME_LEN_OFF,
    CH_SLOT_RING_OFFSET_OFF,
    CH_SLOT_VERSION_OFF,
    GRP_SLOT_NAME_LEN_OFF,
    GRP_SLOT_VERSION_OFF,
    channel_index_off,
    group_index_off,
    region_native,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from channels_shm import SharedMemoryChannelLayer


class TestClosedLayer:
    """Public API contract after close(): RuntimeError, except new_channel."""

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


class TestFlushRobustness:
    """flush() must tolerate corrupted slot states."""

    async def test_flush_with_ring_off_zero(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """A slot marked occupied but with no ring is reset, not crashed on."""
        native = region_native(layer)
        slot_off = channel_index_off(layer) + 0
        native.write_u16(slot_off + CH_SLOT_NAME_LEN_OFF, 5)  # occupied
        native.store_u64(slot_off + CH_SLOT_RING_OFFSET_OFF, 0)  # no ring (corrupt)
        await layer.flush()
        assert native.read_u16(slot_off + CH_SLOT_NAME_LEN_OFF) == 0
        assert native.load_u64(slot_off + CH_SLOT_RING_OFFSET_OFF) == 0

    async def test_flush_odd_version(self, layer: SharedMemoryChannelLayer) -> None:
        """Stale-odd seqlock versions are reset to a clean 0 baseline."""
        native = region_native(layer)
        slot_off = channel_index_off(layer) + 0
        native.write_u16(slot_off + CH_SLOT_NAME_LEN_OFF, 5)  # occupied
        native.store_u64(slot_off + CH_SLOT_VERSION_OFF, 3)  # stale odd
        grp_off = group_index_off(layer)
        gslot_off = grp_off + 0
        native.write_u16(gslot_off + GRP_SLOT_NAME_LEN_OFF, 5)
        native.store_u64(gslot_off + GRP_SLOT_VERSION_OFF, 5)
        await layer.flush()
        assert native.load_u64(slot_off + CH_SLOT_VERSION_OFF) == 0
        assert native.load_u64(gslot_off + GRP_SLOT_VERSION_OFF) == 0


class TestCompactRobustness:
    """compact() is non-destructive: it repairs rings, never slot fields."""

    async def test_compact_ring_off_zero(self, layer: SharedMemoryChannelLayer) -> None:
        """A slot marked occupied but with no ring is skipped untouched."""
        native = region_native(layer)
        slot_off = channel_index_off(layer) + 0
        native.write_u16(slot_off + CH_SLOT_NAME_LEN_OFF, 5)  # occupied
        native.store_u64(slot_off + CH_SLOT_RING_OFFSET_OFF, 0)  # no ring
        await layer.compact()  # should not raise
        assert native.read_u16(slot_off + CH_SLOT_NAME_LEN_OFF) == 5  # untouched
        assert native.load_u64(slot_off + CH_SLOT_RING_OFFSET_OFF) == 0


class TestEnsureLoop:
    """Event-loop binding model (single loop, single thread)."""

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
        """A second thread binding must raise RuntimeError."""
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
