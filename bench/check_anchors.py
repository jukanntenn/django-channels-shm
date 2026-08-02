#!/usr/bin/env python3
"""P3 layer C: spec absolute anchor assertions.

Reads criterion estimates.json, asserts spec performance anchors are met.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Spec anchor table (§5.4, §4.2)
ANCHORS = {
    "ring_enqueue_uncontended": (
        "target/criterion/ring_enqueue_uncontended/new/estimates.json",
        "mean.point",
        100e-9,
        "spec §5.4 ring enqueue < 100ns",
    ),
    "ring_dequeue_uncontended": (
        "target/criterion/ring_dequeue_uncontended/new/estimates.json",
        "mean.point",
        100e-9,
        "spec §5.4 ring dequeue < 100ns",
    ),
}


def read_nested(path: Path, dotted_key: str) -> float:
    data = json.loads(path.read_text())
    for k in dotted_key.split("."):
        data = data[k]
    return float(data)


def main() -> int:
    failures = []
    for name, (path_str, key, threshold, desc) in ANCHORS.items():
        path = Path(path_str)
        if not path.exists():
            print(f"SKIP {name}: {path} not found")
            continue
        value = read_nested(path, key)
        if value > threshold:
            failures.append((name, value, threshold, desc))
        else:
            print(f"PASS {name}: {value:.2e} <= {threshold:.2e} ({desc})")
    if failures:
        print("\nFAIL: spec anchor violations:")
        for name, val, the, desc in failures:
            print(f"  {name}: {val:.2e} > {the:.2e} ({desc})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
