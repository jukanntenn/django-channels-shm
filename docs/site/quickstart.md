# Quickstart

## 1. Configure the layer

Point `CHANNEL_LAYERS` at the backend. `CONFIG` keys are optional; the defaults suit small deployments:

```python
# settings.py
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_shm.SharedMemoryChannelLayer",
        "CONFIG": {
            "capacity": 100,
            "shm_size": 256 * 1024 * 1024,
        },
    },
}
```

Every ASGI worker on the same machine that uses the same `prefix` (default `"channels_shm"`) attaches to the same shared region — there is no server to start. The region and wakeup sockets are created lazily in `/dev/shm`.

## 2. Send and receive

Channel layers are async. Inside a consumer you already have the layer via `self.channel_layer`:

```python
from channels.generic.websocket import AsyncWebsocketConsumer


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.accept()
        await self.channel_layer.group_add("lobby", self.channel_name)
        await self.channel_layer.group_send(
            "lobby",
            {"type": "chat.message", "text": f"{self.channel_name} joined"},
        )

    async def chat_message(self, event):
        await self.send(text_data=event["text"])

    async def receive(self, text_data):
        await self.channel_layer.group_send(
            "lobby",
            {"type": "chat.message", "text": text_data},
        )
```

The layer API is plain asyncio — no sync wrapper needed in async code. In synchronous code use `asgiref.sync.async_to_sync`, exactly like other channel layers.

## 3. Run it

```bash
uv run uvicorn myproject.asgi:application --workers 3
```

Start several tabs against the same port: the kernel spreads connections over the worker processes, and every message crosses them through `/dev/shm`. Hover a message to see which worker PID delivered it.

!!! tip "Sizing and limits"
    `capacity` is the per-channel ring capacity in messages; `shm_size` caps the whole region. See [Configuration](configuration.md) before going beyond toy traffic.

!!! tip "Full messages"
    A `send` to a full channel raises `ChannelFull` after a short retry — handle it like any channels layer (for example, drop or back off). See [Exceptions](reference/exceptions.md).

Next: [Concepts](concepts.md) or the [API reference](reference/layer.md).
