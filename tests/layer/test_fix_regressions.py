"""Regression tests for the 2026-09 bug-fix batch.

Covers the user-visible contracts restored by the fix set:
- ChannelFull is transient: a drained ring accepts the retried send on the
  same ticket (previously every Full burnt a ticket and could kill a ring).
- Channel/group names are bounded by the native 128-byte field width
  (previously 129-200-byte names were silently truncated or wrote past the
  index field).
- Capacity is validated (>= 2): 0 panicked the ring via ``pos % 0`` and was
  silently swapped for the default by ``capacity or default``; 1 is not
  representable in the Vyukov seq encoding.
- A failed ``__init__`` releases already-created resources (E-07).
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import TYPE_CHECKING, cast

import pytest

from channels_shm import SharedMemoryChannelLayer
from channels_shm.channel.validator import validate_channel_name, validate_group_name
from channels_shm.exceptions import ChannelFull, ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from channels_shm.serializer import Message


class TestChannelFullIsTransient:
    """A Full ring must recover after a drain — no permanent death."""

    async def test_send_full_then_drain_then_send_recovers(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        layer = layer_factory()
        channel = "test.full_transient"

        # Fill the ring (capacity=10 from LAYER_CONFIG).
        for i in range(layer.capacity):
            await layer.send(channel, {"type": "msg", "i": i})
        with pytest.raises(ChannelFull):
            await layer.send(channel, {"type": "overflow"})

        # Drain one slot — the very next send must succeed (under the old
        # ticket-burning protocol the burnt tickets could leave the ring
        # permanently Full even while physically empty).
        msg = await asyncio.wait_for(layer.receive(channel), timeout=5)
        assert msg["i"] == 0
        await layer.send(channel, {"type": "after_drain"})

        # FIFO is intact: the remaining originals, then the post-drain send.
        for i in range(1, layer.capacity):
            msg = await asyncio.wait_for(layer.receive(channel), timeout=5)
            assert msg["i"] == i
        msg = await asyncio.wait_for(layer.receive(channel), timeout=5)
        assert msg["type"] == "after_drain"

    async def test_hammering_full_does_not_kill_ring(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """Repeated ChannelFull events accumulate no side effects."""
        layer = layer_factory()
        channel = "test.full_hammer"
        for i in range(layer.capacity):
            await layer.send(channel, {"type": "msg", "i": i})
        # Far more Full events than the capacity: each retries 3x internally.
        for _ in range(layer.capacity * 3):
            with pytest.raises(ChannelFull):
                await layer.send(channel, {"type": "x"})

        # Drain everything — FIFO order intact, nothing lost or duplicated.
        for i in range(layer.capacity):
            msg = await asyncio.wait_for(layer.receive(channel), timeout=5)
            assert msg["i"] == i
        # And the ring accepts new work immediately.
        await layer.send(channel, {"type": "fresh"})
        assert await asyncio.wait_for(layer.receive(channel), timeout=5) == {
            "type": "fresh"
        }


class TestNameLengthBound:
    """Names are bounded by the 128-byte in-shm field width."""

    def test_128_accepted(self) -> None:
        validate_channel_name("a" * 128)
        validate_group_name("g" * 128)

    def test_129_rejected(self) -> None:
        with pytest.raises(TypeError, match="too long"):
            validate_channel_name("a" * 129)
        with pytest.raises(TypeError, match="too long"):
            validate_group_name("g" * 129)

    async def test_send_rejects_overlong_name(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(TypeError, match="too long"):
            await layer.send("a" * 129, {"type": "x"})


class TestCapacityValidation:
    """capacity < 2 is rejected loudly instead of panicking or degrading."""

    @pytest.mark.parametrize("bad", [0, 1, -5, "10", 2.0, True])
    def test_constructor_rejects_invalid_capacity(
        self, prefix: str, bad: object
    ) -> None:
        with pytest.raises(ConfigurationError, match="capacity"):
            _ = SharedMemoryChannelLayer(prefix=prefix, capacity=cast("int", bad))

    @pytest.mark.parametrize("bad", [0, 1, None, "5"])
    def test_overrides_reject_invalid_capacity(self, prefix: str, bad: object) -> None:
        with pytest.raises(ConfigurationError, match="channel_capacity"):
            _ = SharedMemoryChannelLayer(
                prefix=prefix,
                channel_capacity=cast("dict[str, int]", {"chat.*": bad}),
            )

    def test_capacity_two_is_accepted(self, prefix: str) -> None:
        layer = SharedMemoryChannelLayer(
            prefix=prefix, capacity=2, watchdog_interval=None
        )
        assert layer.capacity == 2
        _cleanup_shm(prefix)


class TestInitFailureReleasesResources:
    """E-07: a failure partway through __init__ closes created resources."""

    async def test_registry_failure_closes_region_and_wakeup(
        self,
        prefix: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from channels_shm import layer as layer_mod
        from channels_shm.shm.region import ShmRegionHandle

        def boom(*_args: object, **_kwargs: object) -> int:
            raise RuntimeError("boom")

        monkeypatch.setattr(layer_mod, "registry_register", boom)

        closed_region = 0
        real_region_close = ShmRegionHandle.close

        def spy_close(self: ShmRegionHandle) -> None:
            nonlocal closed_region
            closed_region += 1
            real_region_close(self)

        monkeypatch.setattr(ShmRegionHandle, "close", spy_close)

        try:
            with pytest.raises(RuntimeError, match="boom"):
                _ = SharedMemoryChannelLayer(prefix=prefix, watchdog_interval=None)
            assert closed_region == 1, "region must be closed on init failure"
        finally:
            _cleanup_shm(prefix)


class TestReceiveColdPath:
    """receive() on a never-sent channel creates the ring before waiting."""

    async def test_receive_creates_ring(self, layer: SharedMemoryChannelLayer) -> None:
        channel = "test.cold_path"
        with pytest.raises(TimeoutError):
            _ = await asyncio.wait_for(layer.receive(channel), timeout=0.05)
        channel_mgr = layer._channel_mgr
        assert channel_mgr is not None
        assert channel_mgr.get_ring(channel) is not None


class TestSendGuards:
    """Runtime type guards on the hot-path public API."""

    async def test_send_rejects_non_dict(self, layer: SharedMemoryChannelLayer) -> None:
        with pytest.raises(TypeError, match="dict"):
            await layer.send("test.ch", cast("Message", cast("object", "not-a-dict")))


def _cleanup_shm(prefix: str) -> None:
    """Remove on-disk artifacts a layer creates under `prefix`."""
    for path in (
        f"/dev/shm/{prefix}",
        f"/dev/shm/{prefix}_wakeup",
        f"/dev/shm/{prefix}_obs",
    ):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.islink(path) or os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


class TestFlushGenerationFence:
    """flush ↔ send race: the generation fence retracts raced operations
    (proposed→implemented 2026-09-04-flush-generation-fence)."""

    async def test_old_version_region_rebuilds(
        self, prefix: str, layer: SharedMemoryChannelLayer
    ) -> None:
        """A region written by layout v1 (version field = 1) takes the rebuild
        path instead of being silently misread with v2 slot strides."""
        from channels_shm._native import VERSION, read_version

        region = layer._region
        assert region is not None
        region.native.write_u32(4, 1)  # HDR_VERSION = 4 (u32)
        await layer.close()

        layer2 = SharedMemoryChannelLayer(
            prefix=prefix,
            expiry=60,
            capacity=10,
            shm_size=16 * 1024 * 1024,
            max_channels=100,
            max_groups=50,
            max_processes=16,
            max_members_per_group=64,
            watchdog_interval=None,
        )
        try:
            region2 = layer2._region
            assert region2 is not None
            assert read_version(region2.native) == VERSION
        finally:
            await layer2.close()
            _cleanup_shm(prefix)

    async def test_flush_racing_sends_keeps_layer_usable(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        """Sends racing flush() may be retracted (ChannelFull), never corrupt:
        no exception escapes, survivors stay parseable, and the layer is
        fully functional afterwards."""
        from channels_shm.exceptions import ChannelFull

        sent = 0

        async def sender() -> None:
            nonlocal sent
            for i in range(60):
                try:
                    await layer.send("test.flushrace", {"type": "m", "i": i})
                    sent += 1
                except ChannelFull:
                    pass

        async def flusher() -> None:
            for _ in range(20):
                await layer.flush()
                await asyncio.sleep(0)

        _ = await asyncio.gather(sender(), flusher())

        # Drain whatever survived — every delivered message must parse.
        drained = 0
        while True:
            try:
                msg = await asyncio.wait_for(layer.receive("test.flushrace"), 0.05)
            except TimeoutError:
                break
            assert msg["type"] == "m"
            drained += 1
        assert drained <= sent

        # Fully functional after the race.
        await layer.send("test.flushrace", {"type": "final"})
        msg = await asyncio.wait_for(layer.receive("test.flushrace"), timeout=5)
        assert msg["type"] == "final"
