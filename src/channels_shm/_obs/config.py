"""Observability configuration (O4: file paths + rotation)."""

from __future__ import annotations

from typing import final


@final
class ObservabilityConfig:
    """Configuration for observability data landing (O4).

    All paths default under /dev/shm/{prefix}_obs/ (tmpfs, aligns with
    existing layer.py:142-143 /dev/shm/{prefix} convention). Multi-process
    safety via per-pid files (no QueueHandler daemon thread, per O3 zero-overhead).
    """

    __slots__ = (
        "log_backup_count",
        "log_max_bytes",
        "metrics_flush_interval",
        "obs_dir",
    )

    def __init__(
        self,
        prefix: str,
        *,
        obs_dir: str | None = None,
        log_max_bytes: int = 10 * 1024 * 1024,
        log_backup_count: int = 5,
        metrics_flush_interval: int = 30,
    ) -> None:
        self.obs_dir = obs_dir if obs_dir is not None else f"/dev/shm/{prefix}_obs"
        self.log_max_bytes = log_max_bytes
        self.log_backup_count = log_backup_count
        self.metrics_flush_interval = metrics_flush_interval

    @property
    def logs_dir(self) -> str:
        return f"{self.obs_dir}/logs"

    @property
    def metrics_dir(self) -> str:
        return f"{self.obs_dir}/metrics"
