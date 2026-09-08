# Exceptions

The layer raises its own exception types, defined in `channels_shm.exceptions`:

```python
from channels_shm.exceptions import ChannelFull, ConfigurationError, DeadProcessError, MessageTooLarge
```

Usage in brief:

- `ChannelFull` — raised by `send`/`group_send` when the target channel's ring has no room after a short retry; treat it like the same-named channels exception (drop, back off, or use a bigger capacity).
- `MessageTooLarge` — the serialized message exceeds the 1 MiB transport limit.
- `ConfigurationError` — a `CONFIG` value is invalid (a subclass of `ValueError`).
- `DeadProcessError` — a wakeup send targeted a process that is already gone; the owning process-specific channel is being reclaimed.

The full definitions, rendered from source docstrings:

::: channels_shm.exceptions
    options:
      show_root_heading: true
      members_order: source
      show_if_no_docstring: true
