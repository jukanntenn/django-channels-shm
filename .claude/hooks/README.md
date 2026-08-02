# AI agent hooks

Two hooks guard AI edits in this repo. They are shared verbatim by Claude Code
and Codex (both speak the same stdin-JSON + stdout-JSON protocol).

## `format-on-edit.sh` — PostToolUse

Triggered after every `Edit` / `Write` / `MultiEdit` / `NotebookEdit`. Reads
`tool_input.file_path` from stdin, formats it with `ruff format` (`.py`) or
`rustfmt` (`.rs`). Never blocks — it only fixes format. Always `exit 0`.

## `lint-on-stop.sh` — Stop

Triggered when the AI is about to stop responding. Runs `uv run ruff check .`
from the project root. On failure, returns `exit 0` with stdout JSON
`{"decision":"block","reason":...}`. Both Claude Code and Codex interpret this
as "do not stop; show the reason to the agent so it can fix and continue".
Type-checking is deliberately omitted (baseline-strict CI handles type regressions).

## Claude Code setup

Already configured — `.claude/settings.json` is committed and picked up automatically.

## Codex setup (per-developer, user-level)

Codex project-level (`.codex/`) hook config is not well-documented yet, so
Codex hooks live in the user-level `~/.codex/config.toml`. Append this once
(replace `<PROJECT_ROOT>` with the absolute path to this repo):

    [[PostToolUse]]
    matcher = "^Edit$|^Write$"
    [[PostToolUse.hooks]]
    type = "command"
    command = "<PROJECT_ROOT>/.claude/hooks/format-on-edit.sh"

    [[Stop]]
    [[Stop.hooks]]
    type = "command"
    command = "<PROJECT_ROOT>/.claude/hooks/lint-on-stop.sh"

## Exit-code contract (why `exit 0` + JSON, not `exit 2`)

| Mode | Behavior | Used? |
|------|----------|-------|
| `exit 0` + no stdout | success, no decision | ✅ format hook |
| `exit 0` + `{"decision":"block","reason":...}` | block Stop, feed reason to agent | ✅ lint hook |
| `exit 2` + stderr | block, stderr shown as "hook error" | ❌ not used (JSON mode is preferred) |
