#!/usr/bin/env python3
"""Documentation gate aggregator: runs every Markdown verification gate
in sequence and fails if any gate fails. Each gate stays independently
runnable. With file arguments, gates restrict themselves to those
paths (the prek doc-check hook passes staged files); with none, every
gate runs over its full scope — what CI runs through
``prek run --all-files``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

GATES = [
    "verify_md_links.py",
    "verify_md_wrap.py",
    "verify_md_current.py",
    "verify_dcs_rfc_format.py",
    "verify_doc_pairs.py",
    "verify_doc_budgets.py",
]


def main(argv: list[str]) -> int:
    args = argv[1:]

    failed: list[str] = []
    for gate in GATES:
        result = subprocess.run(  # noqa: S603 — fixed argv: our own gate scripts plus caller-passed paths
            [sys.executable, str(SCRIPTS_DIR / gate), *args],
            cwd=SCRIPTS_DIR.parent,
            check=False,
        )
        if result.returncode != 0:
            failed.append(gate)

    if failed:
        print(
            f"doc_sync: {len(failed)}/{len(GATES)} gate(s) failed: {', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    print(f"doc_sync: all {len(GATES)} documentation gates passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
