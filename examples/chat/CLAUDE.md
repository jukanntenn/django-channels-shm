# AGENTS.md — the chat demo

`examples/chat` is a WeChat-style multi-process Django + Channels chat and the pre-release acceptance app. It is a standalone uv project (own `pyproject.toml` + `uv.lock`) and a prek orphan: `prek.toml` here defines its light checks separately from the workspace.

## Commands

```bash
cd examples/chat
uv sync                                   # builds django-channels-shm from ../.. via maturin
uv run uvicorn chat.asgi:application --workers 3 --port 8000
uv run python manage.py demo_broadcast    # headless acceptance: must print PASSED
```

## Rules

- `demo_broadcast` is the acceptance gate — it must PASS before any release tag ([releasing](../../docs/releasing.md)).
- `uv sync` builds django-channels-shm from the working tree through maturin; after Rust changes, this is where the fresh native module gets exercised end-to-end.
- Dependency changes: `uv lock` here and commit this project's `uv.lock` with the change.
- User-facing docs for the demo live in its `README.md` (exempt from the bilingual corpus — a standalone project's own docs).
