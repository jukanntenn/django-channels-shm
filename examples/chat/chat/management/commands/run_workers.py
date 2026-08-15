"""Run N daphne worker processes on consecutive ports.

`runserver` is single-process, which would hide the whole point of this demo:
messages crossing process boundaries. This command starts real ASGI worker
processes that share one channels-shm layer, so a message sent on port 8000
reaches clients connected to 8001. Ctrl+C stops all of them.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import time

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Start N daphne workers (one process each) on consecutive ports."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--workers", type=int, default=3)
        parser.add_argument("--port", type=int, default=8000)

    def handle(self, *args: object, **options: str | int) -> None:
        n = int(options["workers"])
        port = int(options["port"])
        if n < 1:
            raise CommandError("--workers must be >= 1")

        daphne = shutil.which("daphne")
        if daphne is None:
            raise CommandError(
                "daphne not on PATH — run inside the example env "
                "(uv run python manage.py …)"
            )

        procs = []
        for i in range(n):
            p = subprocess.Popen(
                [
                    daphne,
                    "-p",
                    str(port + i),
                    "-b",
                    "127.0.0.1",
                    "chat.asgi:application",
                ]
            )
            procs.append((port + i, p))

        self.stdout.write(self.style.SUCCESS(f"{n} daphne worker(s) started:"))
        for p, _ in procs:
            self.stdout.write(f"  http://127.0.0.1:{p}/  (ws at /ws/chat/)")
        self.stdout.write("Ctrl+C to stop all workers")

        try:
            while True:
                for pnum, p in procs:
                    if p.poll() is not None:
                        raise CommandError(
                            f"worker on port {pnum} exited with {p.returncode}"
                        )
                time.sleep(1)
        except KeyboardInterrupt:
            self.stdout.write("shutting down…")
        finally:
            for _, p in procs:
                if p.poll() is None:
                    p.send_signal(signal.SIGINT)
            for _, p in procs:
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.terminate()
        sys.exit(0)
