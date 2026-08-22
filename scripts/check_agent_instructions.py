#!/usr/bin/env python3
"""Gate: every AGENTS.md/CLAUDE.md pair and every skills-tree file pair
(scripts/agentlib.py) is byte-identical. Self-healing: a single-sided
edit is copied over the stale twin and the twin staged, so a commit can
never land with a pair split; only a both-sides-changed conflict or a
deletion fails, with instructions an agent can follow straight from the
error output. prek pre-commit hooks (agent-instructions-sync,
skills-sync) and CI.
"""

from __future__ import annotations

import sys

from agentlib import CONFLICT, DELETED, FIXED, all_pairs, apply_fix, pair_status, stage


def main() -> int:
    pairs = all_pairs()
    failures = 0
    for agents, claude in pairs:
        status = pair_status(agents, claude)
        if status.status == FIXED:
            apply_fix(status)
            if stage(status.older):
                print(
                    f"fixed: {status.detail} — copied {status.newer} -> {status.older} (staged)"
                )
            else:
                print(
                    f"ERROR: fixed {status.older} but staging it failed",
                    file=sys.stderr,
                )
                failures += 1
        elif status.status == CONFLICT:
            failures += 1
            print(f"ERROR: mirror conflict — {status.detail}.", file=sys.stderr)
            print(
                "Both sides carry edits; refusing to guess which to keep.",
                file=sys.stderr,
            )
            print(
                f"Reconcile manually: write the content you want to BOTH {agents} and {claude}, then re-run:",
                file=sys.stderr,
            )
            print(
                "  uv run python scripts/check_agent_instructions.py", file=sys.stderr
            )
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
