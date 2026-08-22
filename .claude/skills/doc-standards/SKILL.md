---
name: doc-standards
description: Use when writing, moving, reviewing, or auditing documentation in this repo — deciding where a fact lives (README/docs/PRINCIPLES/DCS-RFCs/skills), adding or updating a bilingual pair, trimming history narration from current-state docs, responding to a doc_sync.py gate failure, or requests like "improve the docs", "where should this be documented", "this doc is too long".
---

# Applying the documentation standard

The rules live in [docs/AGENTS.md](../../../docs/AGENTS.md). This workflow covers placement, writing discipline, and validation. Guidance, not a script.

## Placement: one fact, one home

Before writing, grep for a distinctive phrase to catch duplicates. New content goes to the tier whose job it is; everywhere else links there.

| Tier | Job |
| --- | --- |
| `README.md` / `README.zh.md` | User-facing product docs |
| Root `AGENTS.md` | Standing orders for every session, 1–3 lines per rule, linking its home |
| Subtree `AGENTS.md` (`crates/_channels_shm_native/`, `examples/chat/`, `docs/`) | Orders specific to that subtree; never repeat the root |
| `PRINCIPLES.md` + `.zh.md` | Behavioral constraints — the live home of the coding principles |
| `docs/` | Operation guides (development, releasing, benchmarking); [docs/AGENTS.md](../../../docs/AGENTS.md) owns the doc rules |
| `.agents/dcs-rfcs/` | This project's RFCs — proposals and decision records ([README](../../dcs-rfcs/README.md)) |
| `CHANGELOG.md` | Ledger — narrates history by design, exempt from prose gates |
| `.agents/skills/` | Agent workflows (byte-mirrored to `.claude/skills/` by `scripts/sync_agent_instructions.py`) |

Rationale and change stories go to `.agents/dcs-rfcs/`, never into guide prose. Procedures ("how to release") go to `docs/`; rules an agent needs every session go to `AGENTS.md`; principles with their failure shapes go to `PRINCIPLES.md`.

## Writing discipline

- **Current state only** in `README.md` and `docs/`: no "previously/now/no longer/已移除/不再". Name the live mechanism; link the owning DCS-RFC for the why. `verify_md_current.py` gates this.
- **One physical line per paragraph**; let the editor soft-wrap. Code blocks, tables, and list structure keep their formatting. `verify_md_wrap.py` gates this.
- **Relative Markdown links with real targets and `#fragment` anchors**; never bare filenames or free prose references. `verify_md_links.py` gates this.
- **Bilingual pairs**: every documentation file pairs `foo.md` with `foo.zh.md`, equal authority — write or edit either side first, bring the twin along in the same change with a minimal patch, never a wholesale re-translation. `verify_doc_pairs.py` gates completeness, switchers, link locale, structure, and purity; exemptions live in `scripts/doc_languages.manifest.json`.
- **Word ceilings** bound the agent-instruction files (`scripts/doc_budgets.manifest.json`, gated by `verify_doc_budgets.py`); on red, relocate, condense, raise the ceiling last with a justified manifest diff.
- Deleting or renaming a doc is atomic: move the content (both languages), fix every inbound link — one change.

## Validate

From the repo root: `uv run python scripts/doc_sync.py` (all gates over the full corpus; add file paths to restrict). It also runs as the prek `doc-check` hook on commit and in CI. When a gate fails: fix the doc, not the gate; if the gate itself is wrong, change it in the same change and say why in the owning DCS-RFC.
