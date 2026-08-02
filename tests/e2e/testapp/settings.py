"""Minimal Django settings for the e2e Django/channels stack."""

from __future__ import annotations

SECRET_KEY = "e2e-only-not-secret-do-not-use-in-prod"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "channels",
    "tests.e2e.testapp",
]

ASGI_APPLICATION = "tests.e2e.testapp.asgi.application"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_shm.SharedMemoryChannelLayer",
        "CONFIG": {
            "prefix": "e2e_shared",
            "capacity": 1000,
            "shm_size": 64 * 1024 * 1024,
            "max_channels": 100,
            "max_groups": 50,
            "max_processes": 64,
            "max_members_per_group": 64,
        },
    }
}

DATABASES = {}
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
