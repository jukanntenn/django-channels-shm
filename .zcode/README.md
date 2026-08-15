# ZCode Hooks

Project-local hook scripts for the ZCode agent — thin adapters that delegate
ALL formatting/linting to prek (the single source of truth, see the root
`prek.toml` and AGENTS.md § Quality gates). No formatter mapping lives in
these scripts, so they cannot drift from pre-commit/CI.

- `hooks/post_tool_use.py` — PostToolUse (`Edit|Write`): runs
  `prek run --group format --group lint --files <path>` on the edited file
  (auto-fix). Never blocks; diagnostics go to stderr.
- `hooks/stop.py` — Stop: runs `prek run --group lint --all-files` (ruff,
  typos, actionlint, agents-md sync, clippy, file checks); on failure it
  prints `{"decision":"block","reason":"..."}` (once per turn, guarded by
  the `stopHookActive` flag). ZCode caps Stop continuations at 3 natively.

Same logic as `.claude/hooks/` and `.codex/hooks/` (only the payload key
casing differs); `.opencode/plugin/hooks.ts` is the OpenCode equivalent.

## Why there is no `.zcode/config.json` here

The ZCode client UI and official docs support workspace-scope hooks in
`.zcode/config.json`, but the agent runtime on this machine (v2.1.0, WSL
server) **strips them unconditionally** — a "security policy" warning
(`config_project_hooks.ignored`) is logged and no hook runs. Only hooks in
the **user-level** `~/.zcode/cli/config.json` are executed (verified in
source and by log evidence).

The user-level config therefore points at these scripts via
`${ZCODE_PROJECT_DIR}` and guards on file existence, so other workspaces
without this directory are unaffected. Changing the runtime behavior would
require a ZCode update that honors workspace hooks.
