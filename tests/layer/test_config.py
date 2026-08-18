"""Configuration tests: prefix limits, config-mismatch rebuild, registry limits,
and per-channel capacity overrides.

Maps to the configuration surface of src/channels_shm/layer.py plus the
inherited BaseChannelLayer capacity contract.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from typing import TYPE_CHECKING

import pytest

from channels_shm import SharedMemoryChannelLayer
from channels_shm.exceptions import ChannelFull

if TYPE_CHECKING:
    from collections.abc import Callable


class TestPrefixValidation:
    """AF_UNIX socket-path length guard (prefix_len + 54 <= 107 bytes)."""

    def test_prefix_too_long(self) -> None:
        with pytest.raises(ValueError, match="prefix too long"):
            _ = SharedMemoryChannelLayer(prefix="a" * 54)

    def test_prefix_at_limit(self) -> None:
        """The longest prefix whose wakeup socket path fits in sun_path binds."""
        prefix = "a" * 53
        layer = SharedMemoryChannelLayer(
            prefix=prefix,
            shm_size=1024 * 1024,
            max_channels=4,
            max_groups=2,
            max_processes=4,
            max_members_per_group=2,
            watchdog_interval=None,
        )
        layer.unlink_shm()
        # The layer was never pump-bound; drop the wakeup/obs dirs it created.
        for path in (f"/dev/shm/{prefix}_wakeup", f"/dev/shm/{prefix}_obs"):
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)


class TestConfigMismatch:
    """First-process init: a config mismatch with existing shm triggers rebuild."""

    async def test_config_mismatch_reinit(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
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


class TestChannelCapacity:
    """Per-channel capacity overrides (inherited BaseChannelLayer contract).

    get_capacity returns the first matching override's capacity, falling back
    to the layer default. This sizes ring allocations, so a wrong match changes
    throughput and memory — worth locking down.
    """

    async def test_default_capacity(self, layer: SharedMemoryChannelLayer) -> None:
        assert layer.get_capacity("plain.channel") == layer.capacity

    async def test_glob_override_matches(
        self, layer_factory: Callable[..., SharedMemoryChannelLayer]
    ) -> None:
        """A glob pattern is compiled to a regex and matches by prefix."""
        layer = layer_factory(channel_capacity={"chat.*": 3})
        assert layer.get_capacity("chat.room1") == 3
        assert layer.get_capacity("other.channel") == layer.capacity

    async def test_precompiled_regex_override(
        self, layer_factory: Callable[..., SharedMemoryChannelLayer]
    ) -> None:
        layer = layer_factory(channel_capacity={re.compile("^private\\."): 7})
        assert layer.get_capacity("private.x") == 7
        assert layer.get_capacity("public.x") == layer.capacity

    async def test_first_match_wins(
        self, layer_factory: Callable[..., SharedMemoryChannelLayer]
    ) -> None:
        """Ordered overrides: the first matching pattern decides."""
        layer = layer_factory(channel_capacity={"chat.*": 3, "chat.special.*": 9})
        # dict preserves insertion order; "chat.*" is tried first.
        assert layer.get_capacity("chat.special.room") == 3

    async def test_override_sizes_the_ring(
        self,
        layer_factory: Callable[..., SharedMemoryChannelLayer],
    ) -> None:
        """A channel matching an override gets a ring of that capacity, not the default.

        Fill one past the override capacity: exactly `override` messages are
        accepted, proving the ring was sized by get_capacity.
        """
        layer = layer_factory(channel_capacity={"chat.*": 3})
        channel = "chat.ring"
        for _ in range(3):
            await layer.send(channel, {"type": "msg"})
        with pytest.raises(ChannelFull, match="full"):
            await layer.send(channel, {"type": "overflow"})
