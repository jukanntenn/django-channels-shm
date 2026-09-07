#!/usr/bin/env python3
"""Shared logic for the agent-instruction mirrors.

`AGENTS.md` and `CLAUDE.md` carry the same standing orders under the two
tool-side filenames; every pair in MIRROR_PAIRS lives in one directory.
A pair has no primary: direction is decided against git HEAD so the side
edited last wins, and both-sides-edited is a conflict the callers refuse
to guess through. The skills trees (`.agents/skills/` and
`.claude/skills/`) are mirrored file-by-file under the same rule — no
primary, per-file detection. mtime is the obvious alternative and is
wrong — clone and checkout reset it, leaving both sides equally "new".
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MIRROR_PAIRS: list[tuple[str, str]] = [
    ("AGENTS.md", "CLAUDE.md"),
    ("crates/_channels_shm_native/AGENTS.md", "crates/_channels_shm_native/CLAUDE.md"),
    ("examples/chat/AGENTS.md", "examples/chat/CLAUDE.md"),
]

SKILLS_TREES: tuple[Path, Path] = (
    ROOT / ".agents" / "skills",
    ROOT / ".claude" / "skills",
)

IN_SYNC = "in-sync"
FIXED = "fixed"
CONFLICT = "conflict"
DELETED = "deleted"


@dataclass(frozen=True)
class PairStatus:
    status: str
    newer: str | None
    older: str | None
    detail: str


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _head(path: str) -> bytes | None:
    proc = subprocess.run(  # noqa: S603 — fixed argv `git show HEAD:<tracked path>`
        ["git", "-C", str(ROOT), "show", f"HEAD:{path}"],  # noqa: S607 — git resolved from PATH by design
        capture_output=True,
        check=False,
    )
    return proc.stdout if proc.returncode == 0 else None


def pair_status(agents: str, claude: str) -> PairStatus:  # noqa: PLR0911 — eight verdicts, one branch each; a decision table, not branching logic
    disk = {agents: _read(ROOT / agents), claude: _read(ROOT / claude)}
    if disk[agents] is None and disk[claude] is None:
        return PairStatus(IN_SYNC, None, None, f"{agents} and {claude} both absent")

    head = {agents: _head(agents), claude: _head(claude)}
    changed = {p: disk[p] != head[p] for p in disk}

    if disk[agents] is not None and disk[claude] is not None:
        if disk[agents] == disk[claude]:
            return PairStatus(IN_SYNC, None, None, f"{agents} == {claude}")
        if changed[agents] and not changed[claude]:
            return PairStatus(
                FIXED, agents, claude, f"{agents} changed while {claude} stayed at HEAD"
            )
        if changed[claude] and not changed[agents]:
            return PairStatus(
                FIXED, claude, agents, f"{claude} changed while {agents} stayed at HEAD"
            )
        return PairStatus(
            CONFLICT,
            None,
            None,
            f"{agents} and {claude} each changed since HEAD and disagree",
        )

    present, missing = (agents, claude) if disk[claude] is None else (claude, agents)
    if head[missing] is None:
        return PairStatus(
            FIXED, present, missing, f"{missing} is new; bootstrapped from {present}"
        )
    if changed[present]:
        return PairStatus(
            CONFLICT, None, None, f"{missing} was deleted while {present} changed"
        )
    return PairStatus(
        DELETED, None, None, f"{missing} was deleted while {present} stayed at HEAD"
    )


def apply_fix(status: PairStatus) -> None:
    dst = ROOT / status.older
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / status.newer, dst)


def stage(path: str) -> bool:
    return (
        subprocess.run(  # noqa: S603 — fixed argv `git add <tracked path>`
            ["git", "-C", str(ROOT), "add", path],  # noqa: S607 — git resolved from PATH by design
            check=False,
        ).returncode
        == 0
    )


def skills_pairs() -> list[tuple[str, str]]:
    """Repo-relative (agents-side, claude-side) path pairs for every file
    present under either skills tree."""
    rels: set[Path] = set()
    for tree in SKILLS_TREES:
        if tree.is_dir():
            rels.update(p.relative_to(tree) for p in tree.rglob("*") if p.is_file())
    return [
        (f".agents/skills/{rel.as_posix()}", f".claude/skills/{rel.as_posix()}")
        for rel in sorted(rels)
    ]


def all_pairs() -> list[tuple[str, str]]:
    return MIRROR_PAIRS + skills_pairs()
