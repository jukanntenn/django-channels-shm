# AGENTS.md

A high-performance shared-memory channel layer for Django Channels, designed for single-machine multi-process deployments. Rust native extension (`pyo3`) for the hot path + Python for the async API. Linux-only (`MAP_SHARED` + `AF_UNIX`).

Design and behavior principles live in [`PRINCIPLES.md`](PRINCIPLES.md) — reach for them when making design or convention decisions. The documentation standard lives in [`docs/AGENTS.md`](docs/AGENTS.md). Subtree orders supplement this file and never repeat it: [`crates/_channels_shm_native/AGENTS.md`](crates/_channels_shm_native/AGENTS.md) (Rust build/style/test), [`examples/chat/AGENTS.md`](examples/chat/AGENTS.md) (demo + pre-release acceptance).

## Project layout

- `src/channels_shm/`        Python package: `layer` (public API), `channel/`, `group/`, `shm/`, `serializer`, `pump`
- `crates/_channels_shm_native/`   Rust native module: `atomic`, `ring`, `slab`, `index`, `layout`, `py_bindings` — orders in its `AGENTS.md`
- `examples/chat/`           demo + pre-release acceptance app (standalone uv project, prek orphan) — orders in its `AGENTS.md`
- `tests/`                   mirrors `src/channels_shm` (`layer/`, `channel/`, `group/`, `shm/`, `obs/`, `native/`) plus `cross_process/`, `recovery/` (`@slow`), `e2e/` (`@e2e`, docker compose)
- `docs/`                    operation guides + the documentation standard (`docs/AGENTS.md`)
- `.agents/dcs-rfcs/`        this project's RFCs — proposals and decision records ([README](.agents/dcs-rfcs/README.md))
- `.agents/skills/`          agent workflows (mirrored to `.claude/skills/`)
- `bench/`, `scripts/`, `stubs/`   benchmarks ([guide](docs/benchmarking.md)); stdlib-only helper scripts; third-party stubs

## First-time setup

```bash
uv sync
uvx maturin develop --skip-install   # builds _native.abi3.so into src/
uv tool install prek && prek install # pre-commit + pre-push + commit-msg hooks
```

The native module MUST be built before any test/type-check will pass. Day-to-day commands: [`docs/development.md`](docs/development.md); releasing: [`docs/releasing.md`](docs/releasing.md).

## Quality gates — prek is the single source of truth

Every format/lint/consistency gate is defined in the prek workspace configs and nowhere else (not in CI, not in AI tool hooks): `prek.toml` (root), `crates/_channels_shm_native/prek.toml`, `examples/chat/prek.toml`. Hook groups:

| Group   | What                                                    | Who runs it |
|---------|---------------------------------------------------------|-------------|
| format  | mutating fixers (ruff format, cargo fmt, whitespace, agent-instruction sync) | AI post-edit, commit |
| lint    | read-only gates incl. `ruff check`, clippy, typos, actionlint, documentation gates (`doc_sync`) | AI Stop, commit, CI |
| check   | uv.lock / Cargo.lock freshness, PEP 440 version         | commit, CI |

CI's lint job runs exactly `prek validate-config` + `prek run --all-files` — the same gate as a local commit, so no drift. The four AI tool hook trees (`.zcode/`, `.claude/`, `.codex/`, `.opencode/`) are thin wrappers over these groups.

## DCS-RFCs

Every non-trivial change adds or updates at least one DCS-RFC in the same change-set ([`.agents/dcs-rfcs/README.md`](.agents/dcs-rfcs/README.md)) — grep the tree for the topic first; only mechanical/local edits are exempt. The `writing-rfcs` skill owns the workflow.

## Commands (Python)

| Action | Command |
|--------|---------|
| All gates (what CI runs) | `prek run --all-files` |
| Test (default, fast) | `uv run pytest -m "not slow and not e2e"` |
| Test (cross-process + recovery) | `uv run pytest -m slow` |
| Documentation gates | `uv run python scripts/doc_sync.py` |

## Code style (Python)

- `ruff` formatter + `select = ["ALL"]` stable-strictest (see `[tool.ruff]` in `pyproject.toml`); every ignore in `pyproject.toml` has an inline comment — never add a silent ignore.
- Type annotations enforced by `basedpyright` against the committed progressive baseline.
- Public API is exported from `src/channels_shm/__init__.py` via `__all__`; keep the surface minimal.
- Async tests use `pytest-asyncio` in `asyncio_mode = "auto"` — do not decorate with `@pytest.mark.asyncio`.
- Helper/tooling scripts live in `scripts/` and are stdlib-only Python.
- Documentation is bilingual and follows [`docs/AGENTS.md`](docs/AGENTS.md).

## Git workflow

- Feature branch → PR → `main`. Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `ci:`.
- `prek` runs on pre-commit (format + lint + check), pre-push (tests), and commit-msg (message format). Never bypass with `--no-verify`.

## Boundaries

✅ **Always do**
  - Run `prek run --all-files` (or the matching group) after changes; it is the same gate CI runs.
  - Run `uv lock` and commit `uv.lock` after dependency changes (root and `examples/chat`).
  - Rebuild native with `uvx maturin develop --skip-install` after changing Rust.
  - Update `__all__` in `src/channels_shm/__init__.py` when the public API changes.
  - Add formatter/linter/consistency hooks ONLY to the prek configs — never to CI workflows or AI tool hooks.

⚠️ **Ask first**
  - Editing the ruff `ignore` list in `pyproject.toml`; editing `[tool.ruff]` / `[tool.basedpyright]` / `[tool.ty]` configuration.
  - Changing the shared-memory layout in `crates/_channels_shm_native/src/layout.rs` (ABI impact).

🚫 **Never do**
  - Commit `src/channels_shm/_native.abi3.so`, `.coverage` / `.pytest_cache/` / `target/` / `.hypothesis/` / `.local/`.
  - Add `# type: ignore` / `noqa` / `#[allow(...)]` without an inline reason.
  - Edit generated files by hand (`uv.lock`, `Cargo.lock`, `.basedpyright-baseline.json`) — regenerate instead.

## Editing these instructions

This file loads in every agent session — keep it to standing orders and link everything else to its home. `CLAUDE.md` is a byte-identical copy with no primary: edit either file; [`scripts/check_agent_instructions.py`](scripts/check_agent_instructions.py) self-heals the pair against git HEAD (prek `agent-instructions-sync`), and a true conflict names both sides. Word ceilings live in [`scripts/doc_budgets.manifest.json`](scripts/doc_budgets.manifest.json): relocate or condense before raising one, and justify any raise in the change. Subtree `AGENTS.md` files never repeat this file ([`docs/AGENTS.md`](docs/AGENTS.md) owns the tier rules).
