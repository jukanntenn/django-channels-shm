#!/usr/bin/env python3
"""prek hook: keep AGENTS.md and CLAUDE.md byte-identical.

The two files carry the same agent instructions for different tools; when they
drift, agents get contradictory guidance. Fix by copying one over the other
and committing both.

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    agents = (ROOT / "AGENTS.md").read_bytes()
    claude = (ROOT / "CLAUDE.md").read_bytes()

    if agents == claude:
        return 0

    print("AGENTS.md and CLAUDE.md differ — keep them byte-identical", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
