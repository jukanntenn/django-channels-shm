#!/usr/bin/env python3
"""Thin adapter: PostToolUse → prek format+lint on the edited file.

All formatter/linter logic lives in prek.toml (single source of truth);
this script only extracts the edited path from the tool payload and maps
prek's exit code to the hook contract. It never blocks the session:
exit 0 = clean, 1 = files auto-fixed or issues found (output goes to stderr
so the agent sees diagnostics), >1 = prek itself failed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return

    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not isinstance(file_path, str):
        return

    try:
        result = subprocess.run(
            [
                "prek",
                "run",
                "--group",
                "format",
                "--group",
                "lint",
                "--files",
                file_path,
            ],
            cwd=str(ROOT),
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        print("[post-tool-use] prek not found on PATH; skipped", file=sys.stderr)
        return

    if result.returncode != 0:
        print(f"[post-tool-use] prek format+lint on {file_path}:", file=sys.stderr)
        for stream in (result.stdout, result.stderr):
            if stream.strip():
                print(stream, file=sys.stderr)


if __name__ == "__main__":
    main()
    sys.exit(0)
