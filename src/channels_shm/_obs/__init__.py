"""Observability infrastructure for channels_shm.

Two pillars (O1):
  - Structured logs (structlog + JSON lines to local file)
  - Lightweight metrics (custom Counter/Histogram to local JSON file)

Both pillars are gated by compile-time elimination (O3):
  - Rust side: #[cfg(feature = "metrics")] / #[cfg(feature = "tracing_events)]
  - Python side: if __debug__: blocks (eliminated by python -O)

Release build (python -O + Rust no features) = zero observability overhead.
"""

from channels_shm._obs.config import ObservabilityConfig
from channels_shm._obs.logging_setup import configure_logging
from channels_shm._obs.metrics import MetricsRegistry

# Counter/Histogram are intentionally NOT re-exported here (O-07): they are
# internal implementation details; instances should only be obtained via
# MetricsRegistry.counter()/histogram(). Keeping them out of __all__ keeps the
# public surface minimal (AGENTS.md).
__all__ = [
    "MetricsRegistry",
    "ObservabilityConfig",
    "configure_logging",
]
