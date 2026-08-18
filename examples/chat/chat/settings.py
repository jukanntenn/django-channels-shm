"""Django settings for the channels-shm chat demo.

Deliberately minimal: the whole point of channels-shm is that a multi-process
ASGI deployment needs no broker at all — no Redis, no database, just the
shared-memory layer. Hence `DATABASES = {}`.
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "demo-only-not-secret-do-not-use-in-prod"
DEBUG = True
ALLOWED_HOSTS = ["*"]

# uvicorn is the ASGI server for both single-process dev (`--reload`) and the
# multi-process demo (`--workers N`): one port, N Django processes sharing the
# channel layer below. daphne is intentionally not used.
INSTALLED_APPS = [
    "channels",
    "chat",
]

MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "chat.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

ASGI_APPLICATION = "chat.asgi.application"

# The entire demo: a channel layer backed by /dev/shm + AF_UNIX. Every uvicorn
# worker process mmaps the same region and exchanges presence, private and
# group messages through it — no Redis, no database, no broker.
#
# max_members_per_group is the app's group-size cap (500): when a group chat
# reaches it, layer.group_add raises and the consumer reports "group full".
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_shm.SharedMemoryChannelLayer",
        "CONFIG": {
            "prefix": "chat_demo",
            "capacity": 200,
            "shm_size": 64 * 1024 * 1024,
            "max_channels": 1024,
            # one group per online nickname (presence + PM routing) + per chat group
            "max_groups": 512,
            "max_processes": 64,
            "max_members_per_group": 500,
        },
    }
}

DATABASES = {}

USE_TZ = True

STATIC_URL = "static/"
# Served by chat.urls in DEBUG via django.views.static.serve (uvicorn does not
# run Django's staticfiles machinery, so the route is explicit).
STATICFILES_DIRS = [BASE_DIR / "chat" / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
