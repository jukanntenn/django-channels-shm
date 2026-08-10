#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import PurePath


def commands_for(path: PurePath) -> list[list[str]]:
    match path.suffix:
        case ".py" | ".pyi":
            return [
                ["uv", "run", "ruff", "check", "--fix", str(path)],
                ["uv", "run", "ruff", "format", str(path)],
            ]
        case ".rs":
            # edition 2021 matches crates/_channels_shm_native/Cargo.toml; rustfmt is
            # single-file (PostToolUse gets one path), cargo fmt only works crate-wide.
            return [["rustfmt", "--edition", "2021", str(path)]]
        case _:
            return []


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return

    raw_path = (payload.get("toolInput") or {}).get("file_path")
    if not isinstance(raw_path, str):
        return

    for cmd in commands_for(PurePath(raw_path)):
        try:
            result = subprocess.run(cmd, capture_output=True, check=False, text=True)
        except FileNotFoundError:
            print(
                f"[zcode-post-tool-use] {cmd[0]} not found on PATH; skipped {raw_path}",
                file=sys.stderr,
            )
            continue
        if result.returncode != 0:
            print(
                f"[zcode-post-tool-use] {cmd[0]} reported issues for {raw_path}:",
                file=sys.stderr,
            )
            if result.stdout:
                print(result.stdout, file=sys.stderr)
            if result.stderr:
                print(result.stderr, file=sys.stderr)


if __name__ == "__main__":
    main()
    sys.exit(0)
