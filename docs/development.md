# Development guide

English | [中文](development.zh.md)

## First-time setup

```bash
uv sync
uvx maturin develop --skip-install   # builds _native.abi3.so into src/
uv tool install prek && prek install # pre-commit + pre-push + commit-msg hooks
```

The native module MUST be built before any test or type-check will pass. Rebuild with `uvx maturin develop --skip-install` after any Rust change.

## Daily commands

| Action | Command |
|--------|---------|
| All gates (what CI runs) | `prek run --all-files` |
| Only format group | `prek run --group format --all-files` |
| Only lint group | `prek run --group lint --all-files` |
| Single file (as AI hooks do) | `prek run --group format --group lint --files <path>` |
| Format | `uv run ruff format .` |
| Lint (fix) | `uv run ruff check --fix .` |
| Type check (committed progressive baseline) | `uv run basedpyright --baselinefile .basedpyright-baseline.json` |
| Test (default, fast) | `uv run pytest -m "not slow and not e2e"` |
| Test (cross-process + recovery) | `uv run pytest -m slow` |
| Test (all) | `uv run pytest` |
| Docs (strict build) | `uv sync --extra docs && uv run mkdocs build --strict` |
| Docs (live preview) | `uv run mkdocs serve` |

Rust commands live in [`crates/_channels_shm_native/AGENTS.md`](../crates/_channels_shm_native/AGENTS.md); the demo app's in [`examples/chat/AGENTS.md`](../examples/chat/AGENTS.md).

## Documentation site

The user-facing site (MkDocs Material + mkdocstrings) is generated from `docs/site/` by `mkdocs.yml`; the sources are English-only and API pages render the Google-style source docstrings live. Read the Docs builds it on every push and PR through [`.readthedocs.yaml`](../.readthedocs.yaml), which compiles the PyO3 extension with the image's Rust toolchain before installing `.[docs]`. Configuring the extras: `uv sync --extra docs`; day-to-day commands are in the table above.

First-time activation (one manual step, never repeated): import the repo at readthedocs.org as project `django-channels-shm` and confirm the default URL `https://django-channels-shm.readthedocs.io/`; the committed config performs the rest. Design and decisions: [DCS-RFC 2026-09-08-user-docs-site-on-readthedocs](../.agents/dcs-rfcs/implemented/2026-09-08-user-docs-site-on-readthedocs.md).

## Type-check baseline

[`.basedpyright-baseline.json`](../.basedpyright-baseline.json) is committed and absorbs known errors; the gate fails only on NEW errors. Refresh it after intentional error changes:

```bash
uv run basedpyright --writebaseline --baselinefile .basedpyright-baseline.json
```

The file is generated — never hand-edit it; CI's typecheck job owns its freshness.
