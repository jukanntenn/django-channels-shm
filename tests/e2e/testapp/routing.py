"""ASGI WebSocket routing for the e2e app."""

from __future__ import annotations

from django.urls import re_path

from tests.e2e.testapp import consumers

websocket_urlpatterns = [
    re_path(r"^ws/echo/(?P<room>[^/]+)/$", consumers.EchoConsumer.as_asgi()),
    re_path(r"^ws/broadcast/(?P<room>[^/]+)/$", consumers.BroadcastConsumer.as_asgi()),
]
