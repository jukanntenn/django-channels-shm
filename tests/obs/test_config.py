"""Unit tests for channels_shm._obs.config (observability file layout).

Maps to src/channels_shm/_obs/config.py. The default obs_dir is derived from
the layer prefix; every other path is a fixed subdirectory under it.
"""

from __future__ import annotations

from channels_shm._obs.config import ObservabilityConfig


def test_default_dir_derived_from_prefix() -> None:
    config = ObservabilityConfig("myprefix")
    assert config.obs_dir == "/dev/shm/myprefix_obs"


def test_obs_dir_override() -> None:
    config = ObservabilityConfig("myprefix", obs_dir="/tmp/custom_obs")
    assert config.obs_dir == "/tmp/custom_obs"


def test_logs_and_metrics_subdirs() -> None:
    config = ObservabilityConfig("myprefix", obs_dir="/tmp/obs")
    assert config.logs_dir == "/tmp/obs/logs"
    assert config.metrics_dir == "/tmp/obs/metrics"


def test_default_limits() -> None:
    config = ObservabilityConfig("myprefix")
    assert config.log_max_bytes == 10 * 1024 * 1024
    assert config.log_backup_count == 5
    assert config.metrics_flush_interval == 30
