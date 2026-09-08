"""Exception types for the shared memory channel layer."""

from __future__ import annotations


class ChannelFull(Exception):
    """Raised when a channel's ring stays full on ``send``.

    Raised only by ``send`` after a bounded emergency-drain retry;
    ``group_send`` never raises it (full members are silently skipped).
    """


class MessageTooLarge(Exception):
    """Raised when a serialized message exceeds the 1 MiB transport limit."""


class ConfigurationError(ValueError):
    """Raised when the channel layer configuration is invalid.

    A subclass of ``ValueError``. Raised by the constructor for invalid
    configuration values, for example an over-long ``prefix`` or a ``capacity``
    that is not an integer >= 2.
    """


class DeadProcessError(Exception):
    """Raised when a wakeup send targets a process that is already gone.

    Attributes:
        socket_path: The dead process's wakeup socket path.
    """

    socket_path: str

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        super().__init__(f"Target process dead: {socket_path}")
