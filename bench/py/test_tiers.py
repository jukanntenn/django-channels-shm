"""Message-tier invariants: tier names must stay truthful about storage class.

If serializer output or the inline cutoff changes so that a tier crosses the
inline/overflow boundary, every benchmark in this directory would silently
label a different code path. This test makes that failure loud.
"""

from __future__ import annotations

from bench.common import BENCH_CONFIG, packed_sizes


def test_tiers_stay_in_storage_class() -> None:
    """Each tier must encode to the size class its name promises."""
    sizes = packed_sizes()
    assert sizes["websocket_event"] < sizes["http_request_boundary"]
    assert sizes["http_request_boundary"] < BENCH_CONFIG["inline_size"]
    assert BENCH_CONFIG["inline_size"] < sizes["http_request_100kb"]
    assert sizes["http_request_100kb"] < sizes["http_request_1mb"]
