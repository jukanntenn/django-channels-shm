# AGENTS.md — The documentation standard

This file defines where documentation lives and the writing rules every gate enforces. The [doc-standards](../.agents/skills/doc-standards/SKILL.md) skill turns it into a workflow; `uv run python scripts/doc_sync.py` validates.

## Tiers: one fact, one home

| Tier | Job |
| ---- | --- |
| `README.md` / `README.zh.md` | User-facing product docs |
| Root `AGENTS.md` | Standing orders for every session, 1–3 lines per rule, linking its home |
| Subtree `AGENTS.md` (`crates/_channels_shm_native/`, `examples/chat/`, this file) | Orders specific to that subtree; never repeat the root |
| `PRINCIPLES.md` + `.zh.md` | Behavioral constraints for the agent — the live home of the coding principles |
| `docs/` | Operation guides (development, releasing, benchmarking) |
| `.agents/dcs-rfcs/` | This project's RFCs — proposals and decision records ([README](../.agents/dcs-rfcs/README.md)) |
| `CHANGELOG.md` | Ledger — narrates history by design; exempt from prose gates |
| `.agents/skills/` | Agent workflows (byte-mirrored to `.claude/skills/` by `scripts/sync_agent_instructions.py`) |

Agent-instruction files (`AGENTS.md`, `CLAUDE.md`, `SKILL.md`) stay English-only; every other tier is bilingual (rule 7). Elsewhere, link; never restate. Rationale → `.agents/dcs-rfcs/`; procedures → `docs/`; rules an agent needs every session → `AGENTS.md`; principles with their failure shapes → `PRINCIPLES.md`.

## Rules and gates

1. **Current state only.** `README.md` and `docs/` guides describe what is — no `previously` / `no longer` / `已移除` / `不再` style narration. Link the owning DCS-RFC for the why — `verify_md_current.py`.
2. **Machine-checkable links.** Relative Markdown paths with resolving targets and `#fragment` anchors; never bare filenames — `verify_md_links.py`.
3. **One physical line per paragraph.** Soft-wrap in the editor; code blocks, tables, and lists keep their structure — `verify_md_wrap.py`.
4. **DCS-RFC format.** Header, Status-agrees-with-folder, `## Problem` opener, mandatory `## Alternatives considered` — `verify_dcs_rfc_format.py`.
5. **Word budgets.** The agent-instruction files carry `wc -w` ceilings in [`scripts/doc_budgets.manifest.json`](../scripts/doc_budgets.manifest.json); a budgeted file that is missing fails the gate — `verify_doc_budgets.py`. On red: relocate, condense, raise the ceiling last with a justified manifest diff.
6. **Bilingual pairs.** Every documentation file ships as `foo.md` (English) beside `foo.zh.md` (Chinese), equal authority: either side may be written or edited first, the edited side is the source for that change, the twin follows in the same change as a minimal patch — never a wholesale re-translation — and on substantive disagreement the wrong side is fixed; neither wins by default. Each side carries a header switcher linking the twin; relative links into the corpus use the reader's own locale; heading sequences and fenced code blocks (comments included) stay byte-identical — examples are not translated; machine tokens and DCS-RFC section headings stay in English; `.md` prose carries no CJK; Chinese uses half-width spaces around Latin and full-width punctuation. Exemptions and CJK allowances: [`scripts/doc_languages.manifest.json`](../scripts/doc_languages.manifest.json) — `verify_doc_pairs.py`.

The `CHANGELOG.md` ledger, `examples/chat/`, directory READMEs under `bench/` and `tests/`, and tool-local state (`.zcode/`) sit outside the gates (full list: the language manifest).

## Slop to hunt

The same rule stated in two homes (a subtree `AGENTS.md` restating the root is the common case); narrated history in current-state docs; implementation-status annotations ("future:", "已实现"); hand-restated catalogs where source or a script is authoritative; paragraph walls carrying several rules; emphasis inflation; one side of a language pair edited without its twin. Keep one home, link the rest.
