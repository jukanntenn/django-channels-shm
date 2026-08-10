"""Unit tests for channels_shm.channel.validator.

Maps to src/channels_shm/channel/validator.py. Covers all validation error
paths plus a property test for valid-name acceptance (migrated from
tests/test_properties.py).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings

from channels_shm.channel.validator import validate_channel_name, validate_group_name
from tests.strategies import st_channel_names


class TestChannelNameErrors:
    """All channel-name validation error paths."""

    def test_empty(self) -> None:
        with pytest.raises(TypeError, match="must not be empty"):
            validate_channel_name("")

    def test_too_long(self) -> None:
        with pytest.raises(TypeError, match="too long"):
            validate_channel_name("a" * 201)

    def test_invalid_chars(self) -> None:
        with pytest.raises(TypeError, match="invalid"):
            validate_channel_name("has spaces")

    def test_bang_not_ending_when_receive(self) -> None:
        """A receive-side name containing '!' must end with '!'."""
        with pytest.raises(TypeError, match="must end with !"):
            validate_channel_name("prefix!local", receive=True)


class TestGroupNameErrors:
    """All group-name validation error paths."""

    def test_empty(self) -> None:
        with pytest.raises(TypeError, match="must not be empty"):
            validate_group_name("")

    def test_too_long(self) -> None:
        with pytest.raises(TypeError, match="too long"):
            validate_group_name("a" * 201)

    def test_invalid_chars(self) -> None:
        with pytest.raises(TypeError, match="invalid"):
            validate_group_name("has spaces!")


@given(name=st_channel_names())
@settings(max_examples=50)
def test_valid_channel_name_accepted(name: str) -> None:
    """Valid channel names should pass validation."""
    validate_channel_name(name)  # Should not raise
