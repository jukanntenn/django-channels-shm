#!/usr/bin/env python3
"""Fixer for the agent-instruction mirrors: copies the newer side of
each pair over the older (direction detection against git HEAD in
scripts/agentlib.py) across the AGENTS.md/CLAUDE.md pairs and the
skills trees. Refuses both-sides-changed conflicts instead of guessing.
Run after editing either side of any pair. Checked by the prek
agent-instructions-sync / skills-sync hooks.
"""

from __future__ import annotations

import sys

from agentlib import CONFLICT, DELETED, FIXED, all_pairs, apply_fix, pair_status


def main() -> int:
    pairs = all_pairs()
    failures = 0
    for agents, claude in pairs:
        status = pair_status(agents, claude)
        if status.status == FIXED:
            apply_fix(status)
            print(f"fixed: {status.detail} — copied {status.newer} -> {status.older}")
        elif status.status == CONFLICT:
            failures += 1
            print(f"ERROR: mirror conflict — {status.detail}.", file=sys.stderr)
            print("Refusing to guess which side to keep.", file=sys.stderr)
            print(
                f"Reconcile manually: write the content you want to BOTH {agents} and {claude}, then re-run:",
                file=sys.stderr,
            )
            print("  uv run python scripts/sync_agent_instructions.py", file=sys.stderr)
        elif status.status == DELETED:
            failures += 1
            print(f"ERROR: {status.detail}.", file=sys.stderr)
            print(
                "Restore the deleted file, or remove the pair entirely by updating MIRROR_PAIRS/skills trees",
                file=sys.stderr,
            )
            print(
                "in scripts/agentlib.py and scripts/doc_budgets.manifest.json in the same change.",
                file=sys.stderr,
            )
    if failures:
        return 1
    print(f"agent instructions in sync: {len(pairs)} pair(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
