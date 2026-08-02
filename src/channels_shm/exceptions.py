"""Exception types for the shared memory channel layer."""

from __future__ import annotations


class ChannelFull(Exception):
    """Raised when a channel's capacity is exceeded on send()."""


class MessageTooLarge(Exception):
    """Raised when a message exceeds the 1MB limit after serialization."""


class ConfigurationError(ValueError):
    """Raised when the channel layer configuration is invalid."""


class DeadProcessError(Exception):
    """Raised when a wakeup sendto indicates the target process is dead.

    Moved here from shm/wakeup.py (W-05): a general-purpose exception belongs
    with the other layer exceptions, not in the mechanism-implementation module.
    """

    socket_path: str

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        super().__init__(f"Target process dead: {socket_path}")
