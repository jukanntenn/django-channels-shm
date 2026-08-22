# DCS-RFC: prek is the single source of every quality gate

Status: implemented

English | [中文](2026-08-16-prek-single-source-of-gates.zh.md)

## Problem

Formatter, linter, and consistency checks were at risk of being defined in parallel: AI tool hooks one way, CI another, human muscle memory a third. Formatter and lint coverage leaking across tools means "run the linter" means different things in different contexts — formatting fights and lint skips reproduce locally while CI disagrees, and every agent-facing convention drifts unless something mechanical owns it.

## Decision

Every check the pipeline runs is declared exactly once, in prek: `prek.toml` at the root (generic file checks, typos, actionlint, ruff, uv-lock freshness, version check, documentation gates, the agent-instruction mirrors, fast pytest on pre-push), `crates/_channels_shm_native/prek.toml` (cargo fmt / clippy / Cargo.lock), and `examples/chat/prek.toml` (the demo's own light policy, as a prek orphan). The invocation contracts are fixed: AI post-edit hooks run `prek run --group format --group lint --files <edited>`, the AI Stop hook runs `prek run --group lint --all-files`, and CI's lint job runs exactly `prek validate-config` + `prek run --all-files` — the same gate as a local commit, so no drift. `git commit` runs the pre-commit stage, `commit-msg` checks Conventional Commits, pre-push runs tests. The four AI tool hook trees (`.zcode/`, `.claude/`, `.codex/`, `.opencode/`) are thin wrappers over these groups and contain no formatter mapping of their own. No formatter, linter, or gate is defined anywhere outside the prek configs — not in CI workflows, not in AI tool hooks.

## Alternatives considered

**Per-tool hook configurations.** Each AI tool configured with its own formatter/lint commands. It lost: that is the parallel definition this decision exists to remove — N tools means N definitions drifting apart, and the tool that loads `CLAUDE.md` silently disagrees with the one that loads `AGENTS.md`.

**A second hook runner (lefthook, husky).** Capable runners; it lost: prek was selected for native multi-language hook management across the Python root, the Rust crate, and the demo's orphan project, and a second runner alongside it would reintroduce the parallel definition being eliminated.

**CI-only enforcement.** Single definition, but it lost: feedback arrives minutes late and local commits accumulate unformatted work; the tiered model — fast fixers at commit via the `format` group, read-only gates via `lint`, tests at push, full corpus in CI — needs local hooks anyway.

## Consequences

`prek install` is the one-time setup and every environment that runs the hooks sees the same behavior. Adding a check means editing one prek file — the documentation gates ([doc_sync](../../../scripts/doc_sync.py)) and the agent-instruction mirrors joined exactly that way. Committing requires the hook chain; `--no-verify` is out of bounds. The cost: prek version drift is pinned by `minimum_prek_version` and frozen hook revs, so updates are deliberate.
