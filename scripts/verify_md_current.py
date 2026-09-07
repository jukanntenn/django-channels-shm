#!/usr/bin/env python3
"""The README and the operation guides under docs/ describe current
state: reject narration of removed schemes and change history
("previously", "已移除", "no longer", ...). Decision history belongs in
.agents/dcs-rfcs/, the CHANGELOG ledger is exempt by design, and
PRINCIPLES narrates its principles' rationale by design. Part of
doc_sync.py.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

from doclib import ROOT, collect, mask_source, restrict

if TYPE_CHECKING:
    from pathlib import Path

PATTERNS = [
    "README.md",
    "README.zh.md",
    "docs/*.md",
]

# Matched case-insensitively; Chinese phrases match literally. Product
# behavior words (e.g. 过期 for message expiry) are deliberately absent —
# the gate targets narration markers, not behavior vocabulary.
FORBIDDEN = [
    r"\bpreviously\b",
    r"\bno longer\b",
    r"\bused to\b",
    r"\bwas (?:removed|renamed|moved|replaced)\b",
    r"\bwere removed\b",
    r"\bhas been removed\b",
    r"\bdeprecat\w*\b",
    r"\blegacy\b",
    "已移除",
    "已废弃",
    "已弃用",
    "不再",
    "原先",
    "旧版",
    "曾经",
]

_COMPILED = [(phrase, re.compile(phrase, re.IGNORECASE)) for phrase in FORBIDDEN]


def find_violations(path: Path) -> list[tuple[int, str, str]]:
    masked = mask_source(path.read_text(encoding="utf-8"))
    out: list[tuple[int, str, str]] = []
    for number, line in enumerate(masked.split("\n"), start=1):
        visible = line.split("<!--", 1)[0] if "<!--" in line and "-->" in line else line
        for phrase, pattern in _COMPILED:
            if pattern.search(visible):
                out.append((number, phrase, visible.strip()))
                break
    return out


def main(argv: list[str]) -> int:
    files = collect(PATTERNS)
    if len(argv) > 1:
        files = restrict(files, argv[1:])
    if not files:
        print("verify_md_current: no files in scope, nothing to check")
        return 0

    violations: list[str] = []
    for path in files:
        for line, phrase, text in find_violations(path):
            clipped = text[:70] + ("…" if len(text) > 70 else "")
            violations.append(
                f"  {path.relative_to(ROOT)}:{line}  [{phrase}]  {clipped}"
            )

    if violations:
        print(
            "verify_md_current: history narration found (state the current fact; record history in a DCS-RFC):",
            file=sys.stderr,
        )
        print("\n".join(violations), file=sys.stderr)
        return 1
    print(f"verify_md_current: {len(files)} file(s) checked, current-state prose only")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
