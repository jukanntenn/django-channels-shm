#!/usr/bin/env python3
"""Assert absolute latency anchors for the pytest-benchmark suite.

Reads a pytest-benchmark --benchmark-json dump and bench/baselines/anchors.json
(fullname -> max median in seconds) and fails when a measured median exceeds
its anchor. Medians are used instead of means because these async benches are
inherently noisy (pytest-benchmark FAQ: prefer median/IQR for I/O-heavy code).

Run: python -m bench.checks.check_py_anchors --json bench/results/py_latest.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from bench.common import BENCH_CONFIG

ANCHORS_PATH = Path("bench/baselines/anchors.json")

Anchor = dict[str, object]


def load_anchors() -> dict[str, Anchor]:
    if not ANCHORS_PATH.exists():
        msg = f"no anchors file at {ANCHORS_PATH}; create it after a baseline run"
        raise FileNotFoundError(msg)
    return cast("dict[str, Anchor]", json.loads(ANCHORS_PATH.read_text()))


def check(json_path: Path, anchors: dict[str, Anchor]) -> int:
    data = cast(
        "dict[str, object]",
        json.loads(json_path.read_text()),
    )
    benchmarks: dict[str, dict[str, object]] = {}
    for bench in cast("list[object]", data["benchmarks"]):
        bench_dict = cast("dict[str, object]", bench)
        benchmarks[cast("str", bench_dict["fullname"])] = bench_dict

    failures: list[tuple[str, float, float, str]] = []
    for fullname, anchor in anchors.items():
        bench = benchmarks.get(fullname)
        if bench is None:
            print(f"SKIP {fullname}: not present in {json_path.name}")
            continue
        median = cast("float", cast("dict[str, object]", bench["stats"])["median"])
        limit = cast("float", anchor["max_median_s"])
        if median > limit:
            failures.append(
                (fullname, median, limit, cast("str", anchor.get("why", "")))
            )
        else:
            print(f"PASS {fullname}: median {median:.3e}s <= {limit:.3e}s")
    if failures:
        print("\nFAIL: pytest-benchmark anchor violations:")
        for fullname, median, limit, why in failures:
            print(f"  {fullname}: median {median:.3e}s > {limit:.3e}s ({why})")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--json", default="bench/results/py_latest.json")
    args = parser.parse_args()
    json_path = Path(cast("str", args.json))
    if not json_path.exists():
        print(f"SKIP: {json_path} not found. Run the py benchmark suite first.")
        return 0
    print(f"layer config used: {BENCH_CONFIG}")
    return check(json_path, load_anchors())


if __name__ == "__main__":
    sys.exit(main())
