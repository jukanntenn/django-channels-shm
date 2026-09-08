# SharedMemoryChannelLayer

The public entry point is exported at the package top level:

```python
from channels_shm import SharedMemoryChannelLayer
```

It implements the channels `BaseChannelLayer` contract — `send`, `receive`, `new_channel`, and the `groups` extension (`group_add`, `group_discard`, `group_send`) plus `flush` — over one shared-memory region (see [Concepts](../concepts.md)). Instantiate it through `CHANNEL_LAYERS` (see [Quickstart](../quickstart.md)) or directly with the [configuration](../configuration.md) keyword arguments.

The pages below are rendered from the source docstrings. Configuration keyword arguments are documented in [Configuration](../configuration.md); behavioral detail lives in [Concepts](../concepts.md).

::: channels_shm.layer.SharedMemoryChannelLayer
    options:
      show_root_heading: true
      merge_init_into_class: true
      show_if_no_docstring: true
