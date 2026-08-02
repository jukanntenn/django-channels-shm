# channels-shm — Agent Guide

A high-performance shared-memory channel layer for Django Channels, designed for
single-machine multi-process deployments. Rust native extension (`pyo3`) for the
hot path + Python for the async API. Linux-only (`MAP_SHARED` + `AF_UNIX`).

## Project layout

- `src/channels_shm/`        Python package: `layer` (public API), `channel/`, `group/`, `shm/`, `serializer`, `pump`
- `crates/_channels_shm_native/`   Rust native module: `atomic`, `ring`, `slab`, `index`, `layout`, `py_bindings`
- `tests/`                   unit / property / stateful / concurrency tests (default test run)
- `tests/test_cross_process/`    cross-process integration tests (`@slow`, Linux only)
- `tests/e2e/`               Django/channels stack e2e tests (`@e2e`, docker compose)
- `bench/`                   benchmarks (`pytest-benchmark` Python + `criterion` Rust)
- `stubs/`                   third-party type stubs

## First-time setup

    uv sync
    uvx maturin develop --skip-install   # build _native.abi3.so into src/
    uv tool install prek && prek install # pre-commit hooks

The native module MUST be built before any test/type-check will pass.

## Commands

### Python

| Action | Command |
|--------|---------|
| Format | `uv run ruff format .` |
| Lint (fix) | `uv run ruff check --fix .` |
| Lint (check) | `uv run ruff check .` |
| Type check (local, strictest) | `uv run basedpyright` |
| Test (default, fast) | `uv run pytest -m "not slow and not e2e"` |
| Test (cross-process) | `uv run pytest -m slow` |
| Test (all) | `uv run pytest` |

### Rust (run inside `crates/_channels_shm_native/`)

| Action | Command |
|--------|---------|
| Format check | `cargo fmt --check` |
| Lint | `cargo clippy --all-targets --all-features -- -D warnings` |
| Test | `cargo test` |
| Native build | `uvx maturin develop --skip-install` |

### Benchmark (via nox)

`nox -s bench_python | bench_rust | bench_cross | bench | check_regression | check_anchors`

### Quality gates

- Pre-commit: `prek run` (or `prek run --all-files` for the whole repo)
- CI: see `.github/workflows/ci.yml` (format / lint / typecheck / test / cross-process / e2e / rust / build / precommit)

## Code style

### Python

- `ruff` formatter + `select = ["ALL"]` stable-strictest (see `[tool.ruff]` in `pyproject.toml`).
- Every ignore in `pyproject.toml` has an inline comment explaining why; never add a silent ignore.
- Type annotations enforced locally by `basedpyright` (mode = all) and edited in-editor via VSCode.
- Public API is exported from `src/channels_shm/__init__.py` via `__all__`; keep the public surface minimal.
- Async tests use `pytest-asyncio` in `asyncio_mode = "auto"` — do not decorate with `@pytest.mark.asyncio`.

### Rust

- `rustfmt` default style + `clippy -D warnings`.
- Target module size under 500 LoC; split if a file exceeds ~800 LoC.
- No `#[allow(...)]` without an inline comment justifying it.

## Git workflow

- Feature branch → PR → `main`.
- Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `ci:`.
- `prek` runs on every `git commit` (format + lint + file checks + lockfile + spelling).

## Boundaries

✅ **Always do**
  - Run the matching command after changes (`ruff check` / `pytest` / `cargo test`).
  - Run `uv lock` and commit `uv.lock` after adding/removing dependencies.
  - Rebuild native with `uvx maturin develop --skip-install` after changing Rust.
  - Update `__all__` in `src/channels_shm/__init__.py` when the public API changes.

⚠️ **Ask first**
  - Editing the ruff `ignore` list in `pyproject.toml` (affects global rules).
  - Editing `[tool.ruff]` / `[tool.basedpyright]` / `[tool.ty]` configuration.
  - Changing the shared-memory layout in `crates/_channels_shm_native/src/layout.rs` (ABI impact).

🚫 **Never do**
  - Commit `src/channels_shm/_native.abi3.so` (build artifact).
  - Commit `.coverage` / `.pytest_cache/` / `target/` / `.hypothesis/` / `.local/`.
  - Add `# type: ignore` / `noqa` / `#[allow(...)]` without an inline reason.
  - Run `git commit --no-verify` to bypass pre-commit hooks.

## Further reading

- `.github/workflows/ci.yml`  CI definition (authoritative command source)
