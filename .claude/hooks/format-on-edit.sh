#!/usr/bin/env bash
# PostToolUse hook: format the file AI just wrote/edited.
# Shared by Claude Code and Codex (both pass tool_input.file_path on stdin).
# PostToolUse cannot block the tool (it already ran); this hook only fixes format.

set -euo pipefail

input=$(cat)
file_path=$(echo "$input" | jq -r '.tool_input.file_path // empty')

# Nothing to do without a path.
[ -z "$file_path" ] && exit 0

# Skip generated/vendored/build paths.
case "$file_path" in
  *.local/*|*/.venv/*|*/target/*|*/__pycache__/*|*/node_modules/*|*.egg-info/*) exit 0 ;;
esac

# Dispatch by extension. Failures are non-fatal (don't disturb the AI).
case "$file_path" in
  *.py)
    if command -v uv >/dev/null 2>&1; then
      uv run ruff format "$file_path" >/dev/null 2>&1 || true
    fi
    ;;
  *.rs)
    if command -v rustfmt >/dev/null 2>&1; then
      rustfmt "$file_path" >/dev/null 2>&1 || true
    fi
    ;;
esac

exit 0
