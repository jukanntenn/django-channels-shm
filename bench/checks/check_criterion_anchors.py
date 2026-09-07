#!/usr/bin/env python3
"""Assert absolute latency anchors for the criterion Rust microbenchmarks.

Fails when a measured mean exceeds its anchor. Anchors are absolute guards for
the native hot path (enqueue/dequeue must stay in the hundreds-of-nanoseconds
range); the regression check above covers relative drift.

Run: python -m bench.checks.check_criterion_anchors
"""

from __future__ import annotations

import sys

from bench.checks.common import CRITERION_DIR, read_criterion_mean

# bench name -> (mean upper bound in seconds, why it matters)
ANCHORS: dict[str, tuple[float, str]] = {
    "ring_enqueue_uncontended": (
        100e-9,
        "single-producer ring enqueue must stay under 100ns",
    ),
    "ring_dequeue_uncontended": (
        100e-9,
        "single-consumer ring dequeue must stay under 100ns",
    ),
}


def main() -> int:
    failures: list[tuple[str, float, float, str]] = []
    for name, (threshold, why) in ANCHORS.items():
        path = CRITERION_DIR / name / "new" / "estimates.json"
        if not path.exists():
            print(f"SKIP {name}: {path} not found")
            continue
        value = read_criterion_mean(path)
        if value > threshold:
            failures.append((name, value, threshold, why))
        else:
            print(f"PASS {name}: {value:.2e} <= {threshold:.2e} ({why})")
    if failures:
        print("\nFAIL: anchor violations:")
        for name, value, threshold, why in failures:
            print(f"  {name}: {value:.2e} > {threshold:.2e} ({why})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
