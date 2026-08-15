---
name: commit
description: Use when the user asks to commit or stage changes (commit/stage/save/submit), when a task ends with dirty files to commit, or when multiple files should be split into logical commits.
---

# Commit

Group by logical change, not by file. Draft a plan, confirm, then execute. Never push, never amend.

1. `git status --porcelain` + `git log --oneline -5` for current changes and history style.
2. Separate AI-edited files from unrecognized ones; list unrecognized separately, never mix them in.
3. Group by logical unit (Rust `ring`/`slab` + Python `layer` + tests; `py_bindings` + `_native.pyi` + call sites; chat consumer + template); order: `build/chore` → `feat` → `fix` → `refactor` → `style` → `docs` → `test`, release commit (`chore: release vX.Y.Z`) last.
4. Present the plan once; after confirmation run `git add` + `git commit` batch by batch. Rejected → stop, no second plan.
5. Verification is owned by prek — never run format/lint/test commands by hand. `git commit` itself runs the pre-commit stage on staged files (ruff format/check, typos, actionlint, uv-lock freshness, AGENTS↔CLAUDE sync, cargo fmt/clippy in the crate workspace, examples/chat orphan checks) and `commit-msg` checks Conventional Commits; never `--no-verify`. To gate by hand first, from the repo root: `prek run --all-files` (CI's prek job). Tests are the push gate, not a commit gate — `prek run --stage pre-push --all-files` runs fast pytest + cargo test on demand; after Rust changes rebuild native first (`uvx maturin develop --skip-install`) or they fail.
6. Single file → skip the plan, commit directly.

Message: `<type>(<scope>): <desc>` — lowercase, imperative, no trailing period. Types: `feat`/`fix`/`test`/`docs`/`refactor`/`chore`/`ci`/`build`/`style`/`perf`/`revert`. Scopes: `layer`/`channel`/`group`/`shm`/`serializer`/`pump` (Python), `native`/`ring`/`slab` (Rust), `chat`/`ci`/`release` (omit for cross-cutting). Match the change's language.

- Generated files (`uv.lock`, `examples/chat/uv.lock`, `Cargo.lock`, `.basedpyright-baseline.json`) bundle into the producing commit, or as a standalone `chore` — regenerate via `uv lock` / `uv run basedpyright --writebaseline --baselinefile .basedpyright-baseline.json`, never hand-edit.
- `AGENTS.md` and `CLAUDE.md` stay in sync in one commit (prek gate); same for `README.md` + `README.zh-CN.md`.
- Rust `py_bindings` changes pair with `_native.pyi` and the Python call sites; migration-free ABI changes to `layout.rs` need ask-first per AGENTS.md.
- Never silently include unrecognized files. Never amend, never push, never placeholder messages (wip, update files).
