"""Shared Memory Channel Layer for Django Channels."""

import logging

from channels_shm.layer import SharedMemoryChannelLayer

# O4: Library installs NullHandler (stdlib convention, __init__.py:2300).
# Application decides where logs go; we stay silent by default.
# Observability handler attached only if __debug__ (release python -O = silent).
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = ["SharedMemoryChannelLayer"]
