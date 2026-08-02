"""WebSocket consumers for the e2e cross-worker broadcast test."""

from __future__ import annotations

from channels.generic.websocket import AsyncWebsocketConsumer


class EchoConsumer(AsyncWebsocketConsumer):
    """Echoes any received text back to the sender."""

    async def connect(self) -> None:
        await self.accept()

    async def receive(
        self,
        text_data: str | None = None,
        bytes_data: bytes | None = None,  # noqa: ARG002
    ) -> None:
        if text_data is not None:
            await self.send(text_data=text_data)


class BroadcastConsumer(AsyncWebsocketConsumer):
    """On connect, joins group `room_<room>`. On receive, broadcasts to that group."""

    async def connect(self) -> None:
        self.room_name = self.scope["url_route"]["kwargs"]["room"]
        self.group_name = f"room_{self.room_name}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code: int) -> None:  # noqa: ARG002
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(
        self,
        text_data: str | None = None,
        bytes_data: bytes | None = None,  # noqa: ARG002
    ) -> None:
        if text_data is not None:
            await self.channel_layer.group_send(
                self.group_name,
                {"type": "broadcast.message", "text": text_data},
            )

    async def broadcast_message(self, event: dict) -> None:  # type: ignore[override]
        # Handler for the "broadcast.message" type emitted by group_send.
        await self.send(text_data=event["text"])
