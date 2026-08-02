#!/usr/bin/env bash
# Stop hook: run ruff check on the project. On failure, exit 0 + stdout JSON
# {"decision":"block","reason":...} (officially endorsed mode, NOT exit 2).
# Claude & Codex both parse stdout JSON on exit 0; decision:block prevents Stop
# and feeds `reason` back as a continuation prompt so the AI fixes the issues.
# Type-checking is intentionally omitted (see AGENTS.md: separate type-debt track).

set -uo pipefail

input=$(cat)
cwd=$(echo "$input" | jq -r '.cwd // .' 2>/dev/null || echo "$PWD")
cd "$cwd" 2>/dev/null || cd "$PWD" || true

# Run ruff check; capture combined output.
if ! err=$(uv run ruff check . 2>&1); then
  # Truncate to last 50 lines + JSON-escape, to keep the continuation prompt small.
  reason=$(printf '%s' "$err" | tail -n 50 | jq -Rs .)
  printf '{"decision":"block","reason":%s}\n' "$reason"
  exit 0
fi

exit 0
