#!/usr/bin/env python3
"""Thin adapter: PostToolUse(apply_patch) → prek format+lint on edited files.

Same contract as .claude/hooks/post_tool_use.py (see that file); Codex's
apply_patch tool reports edits as a patch text, so the edited paths are
parsed out of it first, then handed to a single prek invocation. Never
blocks.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MOVE_TO_PREFIX = "*** Move to: "
PATCH_FILE_PREFIXES = (
    "*** Update File: ",
    "*** Add File: ",
)


def extract_edited_paths(command: str) -> list[str]:
    paths: list[str] = []
    pending_update: str | None = None
    for raw in command.splitlines():
        line = raw.strip()
        if pending_update is not None and line.startswith(MOVE_TO_PREFIX):
            paths.append(line[len(MOVE_TO_PREFIX) :].strip())
            pending_update = None
            continue
        if pending_update is not None:
            paths.append(pending_update)
            pending_update = None
        if line.startswith(MOVE_TO_PREFIX):
            continue
        for prefix in PATCH_FILE_PREFIXES:
            if line.startswith(prefix):
                pending_update = line[len(prefix) :].strip()
                break
    if pending_update is not None:
        paths.append(pending_update)
    return paths


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return

    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return

    paths = extract_edited_paths(command)
    if not paths:
        return

    try:
        result = subprocess.run(
            ["prek", "run", "--group", "format", "--group", "lint", "--files", *paths],
            cwd=str(ROOT),
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        print("[codex-post-tool-use] prek not found on PATH; skipped", file=sys.stderr)
        return

    if result.returncode != 0:
        print(
            f"[codex-post-tool-use] prek format+lint on {len(paths)} file(s):",
            file=sys.stderr,
        )
        for stream in (result.stdout, result.stderr):
            if stream.strip():
                print(stream, file=sys.stderr)


if __name__ == "__main__":
    main()
    sys.exit(0)
