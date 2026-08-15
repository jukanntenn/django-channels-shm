"""WebSocket consumer for the chat demo.

Every browser message is broadcast through the channel-layer group, and the
echo sent back to ALL clients is annotated with the PID of the worker process
that handled the send. Open the page in two tabs against two different worker
ports (`manage.py run_workers`) and watch messages hop processes through
/dev/shm — no Redis involved.
"""

from __future__ import annotations

import os

from channels.generic.websocket import AsyncJsonWebsocketConsumer

GROUP = "chat"


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self) -> None:
        await self.channel_layer.group_add(GROUP, self.channel_name)
        await self.accept()
        await self.send_json(
            {
                "kind": "welcome",
                "connected_to_pid": os.getpid(),
                "note": "messages are broadcast to every connected client "
                "through the shared-memory layer",
            }
        )

    async def disconnect(self, code: int) -> None:
        await self.channel_layer.group_discard(GROUP, self.channel_name)

    async def receive_json(self, content: dict, **kwargs: object) -> None:
        await self.channel_layer.group_send(
            GROUP,
            {
                "type": "chat.message",
                "text": str(content.get("text", "")),
                "handled_by_pid": os.getpid(),
            },
        )

    async def chat_message(self, event: dict) -> None:
        await self.send_json(
            {
                "kind": "message",
                "text": event["text"],
                "handled_by_pid": event["handled_by_pid"],
            }
        )
