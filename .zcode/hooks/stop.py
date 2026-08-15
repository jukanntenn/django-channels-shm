#!/usr/bin/env python3
"""Thin adapter: Stop → prek lint gate over all files.

Same contract as .claude/hooks/stop.py (see that file); only the payload
key casing differs (ZCode sends camelCase keys). Blocks via decision/reason
JSON when the prek lint group fails; ZCode caps Stop continuations natively.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REASON = """prek lint gate found errors it could not auto-fix. Resolve them before finishing.

<prek_output>
{output}
</prek_output>

The gate is defined in prek.toml (workspace root + sub-project configs).
Reproduce it yourself with:
  prek run --group lint --all-files

Required:
1. Fix every diagnostic above with a real code change. Do not silence them:
   - Python: no `# noqa`, no inline rule disables, no `type: ignore` — only treat a diagnostic as a false positive if you can justify why.
   - Rust:   no `#[allow(...)]` without an inline comment justifying it.
2. Verify `prek run --group lint --all-files` exits 0 with no findings before finishing.

This enforcement fires once per turn — the stop hook will not block a second time. If you stop again with lint errors remaining, they will slip through to CI. Verify before you finish."""


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return

    if payload.get("stopHookActive"):
        return

    try:
        result = subprocess.run(
            ["prek", "run", "--group", "lint", "--all-files"],
            cwd=str(ROOT),
            capture_output=True,
            check=False,
            text=True,
        )
    except FileNotFoundError:
        print("[stop] prek not found on PATH; lint gate skipped", file=sys.stderr)
        return

    if result.returncode == 0:
        return

    output = "\n".join(
        line for line in (result.stdout + result.stderr).splitlines() if line.strip()
    )
    print(json.dumps({"decision": "block", "reason": REASON.format(output=output)}))


if __name__ == "__main__":
    main()
    sys.exit(0)
