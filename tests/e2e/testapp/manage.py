#!/usr/bin/env python
"""Django manage.py for the e2e app (rarely needed; daphne uses asgi directly)."""

from __future__ import annotations

import os
import sys


def main() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.e2e.testapp.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
