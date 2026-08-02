#!/usr/bin/env python3
"""O4: inspect CLI — read observability files (logs/metrics) from local fs.

Usage:
  python -m channels_shm.inspect logs [--pid PID] [--level LEVEL] [--grep PATTERN] [--prefix PREFIX]
  python -m channels_shm.inspect metrics [--aggregate] [--prefix PREFIX]
  python -m channels_shm.inspect status [--prefix PREFIX]

Zero dependencies (stdlib argparse/json/os only). Reads /dev/shm/{prefix}_obs/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _obs_dir(prefix: str) -> Path:
    return Path(f"/dev/shm/{prefix}_obs")


def _alive_pids(logs_dir: Path) -> set[int]:
    """Determine alive pids from {pid}.jsonl files."""
    alive = set()
    for f in logs_dir.glob("*.jsonl"):
        try:
            pid = int(f.stem)
        except ValueError:
            continue
        try:
            os.kill(pid, 0)
            alive.add(pid)
        except (ProcessLookupError, PermissionError):
            pass
    return alive


def cmd_logs(args: argparse.Namespace) -> int:
    logs_dir = _obs_dir(args.prefix) / "logs"
    if not logs_dir.exists():
        sys.stderr.write(f"No logs dir: {logs_dir}\n")
        return 1
    for f in sorted(logs_dir.glob("*.jsonl*")):
        if args.pid and f.stem != str(args.pid):
            continue
        with Path(f).open() as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if args.level and rec.get("level", "").upper() < args.level.upper():
                    continue
                if args.grep and args.grep not in line:
                    continue
                sys.stdout.write(line + "\n")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    metrics_dir = _obs_dir(args.prefix) / "metrics"
    if not metrics_dir.exists():
        sys.stderr.write(f"No metrics dir: {metrics_dir}\n")
        return 1

    if args.aggregate:
        agg: dict[str, float] = {}
        for f in metrics_dir.glob("*.json"):
            data = json.loads(f.read_text())
            for c in data.get("counters", []):
                name = c["name"]
                total = sum(v["value"] for v in c["values"])
                agg[name] = agg.get(name, 0) + total
        sys.stdout.write(json.dumps({"aggregated_counters": agg}, indent=2) + "\n")
    else:
        for f in sorted(metrics_dir.glob("*.json")):
            sys.stdout.write(f"=== {f.name} ===\n")
            sys.stdout.write(f.read_text())
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    obs = _obs_dir(args.prefix)
    logs_dir = obs / "logs"
    metrics_dir = obs / "metrics"
    alive = _alive_pids(logs_dir) if logs_dir.exists() else set()
    sys.stdout.write(f"prefix: {args.prefix}\n")
    sys.stdout.write(f"obs_dir: {obs}\n")
    sys.stdout.write(f"alive_pids: {sorted(alive) if alive else '(none)'}\n")
    if metrics_dir.exists():
        for f in sorted(metrics_dir.glob("*.json")):
            pid = int(f.stem) if f.stem.isdigit() else 0
            marker = "(alive)" if pid in alive else "(dead)"
            data = json.loads(f.read_text())
            counter_names = [c["name"] for c in data.get("counters", [])]
            sys.stdout.write(f"  pid {pid} {marker}: counters={counter_names}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="channels_shm.inspect")
    parser.add_argument(
        "--prefix", default="channels_shm", help="shm prefix (default: channels_shm)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_logs = sub.add_parser("logs", help="show structured logs (JSON lines)")
    p_logs.add_argument("--pid", type=int)
    p_logs.add_argument("--level", default=None)
    p_logs.add_argument("--grep", default=None)
    p_logs.set_defaults(func=cmd_logs)

    p_metrics = sub.add_parser("metrics", help="show metrics snapshots")
    p_metrics.add_argument("--aggregate", action="store_true")
    p_metrics.set_defaults(func=cmd_metrics)

    p_status = sub.add_parser("status", help="show overview")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
