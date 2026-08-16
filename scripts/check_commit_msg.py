#!/usr/bin/env python3
"""prek commit-msg hook: enforce Conventional Commits (AGENTS.md § Git workflow).

Receives the commit-message file path as its only argument. Merge commits
(``Merge ...``) and reverts applied by git itself (``Revert "..."``) pass
through because their format is git's, not ours.

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# AGENTS.md lists feat/fix/test/docs/refactor/chore/ci; build/style/perf/revert
# are the remaining Conventional Commits types, allowed for future use.
CONVENTIONAL = (
    r"^(feat|fix|test|docs|refactor|chore|ci|build|style|perf|revert)"
    r"(\([^)]+\))?"
    r"!?: .+"
)
PASSTHROUGH = re.compile(r"^(Merge .*|Revert \".*\")")
COMMIT_MSG = re.compile(CONVENTIONAL)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <commit-msg-file>", file=sys.stderr)
        return 1

    # Only the subject line is checked; the body is free-form.
    subject = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()[0]

    if PASSTHROUGH.match(subject) or COMMIT_MSG.match(subject):
        return 0

    message = f"commit message not Conventional Commits: {subject!r}\nexpected: type[(scope)][!]: subject   (e.g. 'feat(layer): add group_send batching')"
    print(message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
