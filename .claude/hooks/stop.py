#!/usr/bin/env python3

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# crates/_channels_shm_native is this repo's only Rust crate; Cargo.toml path is
# stable relative to the hook script (.{claude,codex,zcode}/hooks/stop.py -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CARGO_MANIFEST = str(_REPO_ROOT / "crates" / "_channels_shm_native" / "Cargo.toml")

REASON_TEMPLATE = """Lint gate found errors it could not auto-fix. Resolve them before finishing.

Diagnostics (Python / ruff):
<ruff_output>
{py_diagnostics}
</ruff_output>

Diagnostics (Rust / cargo fmt):
<rust_fmt_output>
{rust_fmt_diagnostics}
</rust_fmt_output>

Diagnostics (Rust / cargo clippy):
<rust_clippy_output>
{rust_clippy_diagnostics}
</rust_clippy_output>

Required:
1. Fix every diagnostic above with a real code change. Do not silence them:
   - Python: no `# noqa`, no inline rule disables, no `type: ignore` - only treat a diagnostic as a false positive if you can justify why.
   - Rust:   no `#[allow(...)]` without an inline comment justifying it.
2. After editing, verify yourself that all gates are clean:
   - `uv run ruff check`
   - `cargo fmt --check` (run inside crates/_channels_shm_native/)
   - `cargo clippy --all-targets --all-features -- -D warnings` (run inside crates/_channels_shm_native/)
3. Only attempt to finish again once every command above exits 0 with no output.

This enforcement fires once per turn - the stop hook will not block a second time. If you stop again with lint errors remaining, they will slip through to CI. Verify before you finish."""


def _collect(cmd: list[str]) -> tuple[int, str]:
    """Run cmd, return (returncode, combined non-empty stdout+stderr).

    Output is collected only on failure: cargo clippy prints `Finished ...` to
    stderr on success, which would otherwise look like a diagnostic. A clean gate
    is signaled by exit 0 and produces no output here.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, check=False, text=True)
    except FileNotFoundError:
        return 1, f"{cmd[0]} not found on PATH; could not run gate"
    if result.returncode == 0:
        return 0, ""
    out = "\n".join(
        line for line in (result.stdout + result.stderr).splitlines() if line.strip()
    )
    return result.returncode, out


def _run_silent(cmd: list[str]) -> None:
    """Run cmd for its side effects (auto-fix); ignore output and return code.

    --allow-dirty/--allow-no-vcs are required: the work tree is dirty by the time
    the stop hook runs (the agent just edited files), and clippy --fix refuses to
    run otherwise.
    """
    try:
        subprocess.run(cmd, capture_output=True, check=False, text=True)
    except FileNotFoundError:
        pass


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        return

    if payload.get("stop_hook_active"):
        return

    # ── auto-fix phase (mirror Python's `ruff check --fix`): apply fixes first ──
    py_rc, py_out = _collect(["uv", "run", "ruff", "check", "--fix"])
    _run_silent(["cargo", "fmt", "--manifest-path", _CARGO_MANIFEST])
    _run_silent(
        [
            "cargo",
            "clippy",
            "--fix",
            "--allow-dirty",
            "--allow-no-vcs",
            "--all-targets",
            "--all-features",
            "--manifest-path",
            _CARGO_MANIFEST,
        ]
    )
    # clippy --fix can produce rustfmt-noncompliant code; re-format before checking.
    _run_silent(["cargo", "fmt", "--manifest-path", _CARGO_MANIFEST])

    # ── gate phase: anything still unfixed blocks the stop ──
    fmt_rc, fmt_out = _collect(
        ["cargo", "fmt", "--check", "--manifest-path", _CARGO_MANIFEST]
    )
    clippy_rc, clippy_out = _collect(
        [
            "cargo",
            "clippy",
            "--all-targets",
            "--all-features",
            "--manifest-path",
            _CARGO_MANIFEST,
            "--",
            "-D",
            "warnings",
        ]
    )

    if py_rc == fmt_rc == clippy_rc == 0:
        return

    print(
        json.dumps(
            {
                "decision": "block",
                "reason": REASON_TEMPLATE.format(
                    py_diagnostics=py_out or "(clean)",
                    rust_fmt_diagnostics=fmt_out or "(clean)",
                    rust_clippy_diagnostics=clippy_out or "(clean)",
                ),
            }
        )
    )


if __name__ == "__main__":
    main()
    sys.exit(0)
