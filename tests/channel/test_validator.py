"""Unit and property tests for channels_shm.channel.validator.

Maps to src/channels_shm/channel/validator.py. Covers every validation error
path plus property tests proving the *complement* (all valid names) is
accepted. Note: the `receive=True` branch is not used by layer.receive (a
process-specific owner's own channel does not end in '!'), so it is exercised
here directly.
"""

from __future__ import annotations

import pytest
from hypothesis import given

from channels_shm.channel.validator import validate_channel_name, validate_group_name
from tests.strategies import (
    st_channel_names,
    st_group_names,
    st_process_specific_channels,
    st_receive_channel_names,
)


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
def test_valid_channel_name_accepted(name: str) -> None:
    """Valid channel names should pass validation."""
    validate_channel_name(name)  # Should not raise


@given(name=st_process_specific_channels())
def test_valid_process_specific_name_accepted(name: str) -> None:
    """Valid process-specific names (one '!') pass validation."""
    validate_channel_name(name)  # Should not raise


@given(name=st_receive_channel_names())
def test_valid_receive_name_accepted(name: str) -> None:
    """A name ending in '!' passes the receive-side validation."""
    validate_channel_name(name, receive=True)  # Should not raise


@given(name=st_group_names())
def test_valid_group_name_accepted(name: str) -> None:
    """Valid group names should pass validation."""
    validate_group_name(name)  # Should not raise
