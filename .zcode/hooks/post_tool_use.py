#!/usr/bin/env python3
"""Thin adapter: PostToolUse → prek format+lint on the edited file.

Same contract as .claude/hooks/post_tool_use.py (see that file); only the
payload key casing differs (ZCode sends camelCase keys). Never blocks.
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

    file_path = (payload.get("toolInput") or {}).get("file_path")
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
