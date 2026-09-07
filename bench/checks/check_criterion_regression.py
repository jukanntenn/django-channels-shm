#!/usr/bin/env python3
"""Fail if any criterion benchmark regressed > 10% (mean) vs its saved baseline.

Parses target/criterion/<bench>/change/estimates.json and compares the mean
against the stored baseline (main, or base as fallback).

Run: python -m bench.checks.check_criterion_regression
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from bench.checks.common import CRITERION_DIR, read_criterion_mean

if TYPE_CHECKING:
    from pathlib import Path

THRESHOLD = 0.10  # 10% mean regression threshold


def check(criterion_dir: Path, baseline: str = "main") -> int:
    failures: list[tuple[str, float, float, float]] = []
    for change_file in criterion_dir.glob("*/change/estimates.json"):
        bench_name = change_file.parent.parent.name
        base_file = criterion_dir / bench_name / baseline / "estimates.json"
        if not base_file.exists():
            base_file = criterion_dir / bench_name / "base" / "estimates.json"
        if not base_file.exists():
            continue
        new_mean = read_criterion_mean(change_file)
        base_mean = read_criterion_mean(base_file)
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
    if not CRITERION_DIR.exists():
        print(f"SKIP: {CRITERION_DIR} not found. Run cargo bench first.")
        return 0
    return check(CRITERION_DIR)


if __name__ == "__main__":
    sys.exit(main())
