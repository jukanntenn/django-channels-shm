"""e2e test fixtures (Layer B). Requires the docker-compose stack to be running.

The runner container runs these tests. WORKER_HOSTS env var lists worker endpoints.
"""

from __future__ import annotations

import os
import socket

import pytest

pytestmark = pytest.mark.e2e


def pytest_configure(config: pytest.Config) -> None:
    """Apply project-level pytest settings inside the runner container.

    The container runs pytest without pyproject.toml (it is not copied into
    the image), so asyncio_mode=auto and the e2e marker registration from
    [tool.pytest.ini_options] must be applied here.
    """
    if not config.option.asyncio_mode:
        config.option.asyncio_mode = "auto"
    markers = config.getini("markers")
    if not any(marker.startswith("e2e:") for marker in markers):
        config.addinivalue_line(
            "markers",
            "e2e: end-to-end Docker Django stack tests (requires docker compose)",
        )


def _worker_hosts() -> list[str]:
    raw = os.environ.get("WORKER_HOSTS", "worker:8000")
    return [h.strip() for h in raw.split(",") if h.strip()]


def _check_host(host_port: str) -> None:
    """Verify a single worker host is reachable; skip test if not."""
    host, _, port = host_port.partition(":")
    try:
        with socket.create_connection((host, int(port or 8000)), timeout=5):
            pass
    except OSError as exc:
        pytest.skip(f"worker {host_port} not reachable: {exc}")


@pytest.fixture
def worker_host() -> str:
    """Single worker host for tests that don't need cross-worker verification."""
    hosts = _worker_hosts()
    _check_host(hosts[0])
    return hosts[0]


@pytest.fixture
def worker_hosts() -> list[str]:
    hosts = _worker_hosts()
    if len(hosts) < 2:
        pytest.skip("e2e cross-worker test needs at least 2 worker hosts")
    for host_port in hosts:
        _check_host(host_port)
    return hosts
