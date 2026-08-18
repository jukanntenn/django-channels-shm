"""new_channel() naming contract.

Maps to src/channels_shm/layer.py::new_channel. The name embeds this process's
client_prefix as the ownership marker, so the prefix must not contain '!' or '?'
or it would corrupt the marker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from channels_shm import SharedMemoryChannelLayer


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
        """'!' would corrupt the process-specific ownership marker."""
        with pytest.raises(ValueError, match=r"[!?]"):
            _ = await layer.new_channel("bad!prefix")

    async def test_prefix_with_question_mark_rejected(
        self, layer: SharedMemoryChannelLayer
    ) -> None:
        with pytest.raises(ValueError, match=r"[!?]"):
            _ = await layer.new_channel("bad?prefix")
