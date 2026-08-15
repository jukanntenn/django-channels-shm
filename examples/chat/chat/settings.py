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

# "daphne" must come first so `runserver` is the ASGI (websocket-capable) one.
INSTALLED_APPS = [
    "daphne",
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

# The entire demo: a channel layer backed by /dev/shm + AF_UNIX. Every worker
# process (see `manage.py run_workers`) mmaps the same region and receives
# group broadcasts through it.
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_shm.SharedMemoryChannelLayer",
        "CONFIG": {
            "prefix": "chat_demo",
            "capacity": 1000,
            "shm_size": 64 * 1024 * 1024,
            "max_channels": 1000,
            "max_groups": 100,
            "max_processes": 64,
            "max_members_per_group": 128,
        },
    }
}

DATABASES = {}

USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
