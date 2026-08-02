#!/usr/bin/env python3
"""P3 layer B: criterion regression check.

Parses target/criterion/<bench>/change/estimates.json and fails if mean
regressed > 10% vs baseline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

THRESHOLD = 0.10  # 10% mean regression threshold


def check(criterion_dir: Path, baseline: str = "main") -> int:
    failures = []
    for change_file in criterion_dir.glob("*/change/estimates.json"):
        bench_name = change_file.parent.parent.name
        base_file = criterion_dir / bench_name / baseline / "estimates.json"
        if not base_file.exists():
            base_file = criterion_dir / bench_name / "base" / "estimates.json"
        if not base_file.exists():
            continue
        data = json.loads(change_file.read_text())
        base_data = json.loads(base_file.read_text())
        new_mean = data["mean"]["point"]
        base_mean = base_data["mean"]["point"]
        regression = (new_mean - base_mean) / base_mean
        if regression > THRESHOLD:
            failures.append((bench_name, base_mean, new_mean, regression))
    if failures:
        print("FAIL: criterion regression > 10%:")
        for name, base, new, reg in failures:
            print(f"  {name}: {base:.2e} -> {new:.2e} (+{reg:.1%})")
        return 1
    print("PASS: no criterion regression > 10%")
    return 0


def main() -> int:
    criterion_dir = Path("target/criterion")
    if not criterion_dir.exists():
        print("SKIP: target/criterion not found. Run cargo bench first.")
        return 0
    return check(criterion_dir, baseline="main")


if __name__ == "__main__":
    sys.exit(main())
