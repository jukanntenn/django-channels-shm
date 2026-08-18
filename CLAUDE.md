# channels-shm — Agent Guide

A high-performance shared-memory channel layer for Django Channels, designed for
single-machine multi-process deployments. Rust native extension (`pyo3`) for the
hot path + Python for the async API. Linux-only (`MAP_SHARED` + `AF_UNIX`).

## Project layout

- `src/channels_shm/`        Python package: `layer` (public API), `channel/`, `group/`, `shm/`, `serializer`, `pump`
- `crates/_channels_shm_native/`   Rust native module: `atomic`, `ring`, `slab`, `index`, `layout`, `py_bindings`
- `examples/chat/`           demo + pre-release acceptance app (standalone uv project, prek orphan)
- `scripts/`                 helper scripts behind prek hooks (Python, stdlib-only)
- `tests/`                   mirrors `src/channels_shm` layout (unit/property/stateful; default test run): `layer/`, `channel/`, `group/`, `shm/`, `obs/`, `native/`, plus root `test_*.py`
- `tests/cross_process/`     normal multi-process interop (`@slow`, Linux only)
- `tests/recovery/`          fault-injection recovery + observability (fork/SIGKILL; `@slow`, Linux only)
- `tests/e2e/`               Django/channels stack e2e tests (`@e2e`, docker compose)
- `bench/`                   benchmarks: `py/` (pytest-benchmark single-process), `xproc/` (cross-process scripts), `docker/` (pinned-env 3-way), `checks/` (anchor/regression gates); Rust criterion benches live in the crate
- `stubs/`                   third-party type stubs

## First-time setup

    uv sync
    uvx maturin develop --skip-install   # build _native.abi3.so into src/
    uv tool install prek && prek install # pre-commit + pre-push + commit-msg hooks

The native module MUST be built before any test/type-check will pass.

## Quality gates — prek is the single source of truth

Every format/lint/consistency gate is defined in the prek workspace configs and
nowhere else (not in CI, not in AI tool hooks):

- `prek.toml` (root) — generic file checks, typos, actionlint, ruff, uv-lock,
  version check, agents-md sync, fast pytest (pre-push)
- `crates/_channels_shm_native/prek.toml` — cargo fmt / clippy / Cargo.lock
  freshness / cargo test (pre-push)
- `examples/chat/prek.toml` — the demo's own light checks (prek `orphan`)

Hook groups (`prek run --group <name>`):

| Group   | What                                                    | Who runs it |
|---------|---------------------------------------------------------|-------------|
| format  | mutating fixers (ruff format, cargo fmt, whitespace…)   | AI post-edit, commit |
| lint    | read-only gates incl. `ruff check --fix` and clippy     | AI Stop, commit, CI |
| check   | uv.lock / Cargo.lock freshness, PEP 440 version         | commit, CI |

Auto-fix is always on where the tool supports it (ruff `--fix`, `--fix=lf`,
`cargo fmt`); clippy is deliberately not `--fix`ed (machine fixes can alter
Rust semantics). CI's lint job runs exactly `prek validate-config` +
`prek run --all-files` — same gate as a local commit, so no drift.

Generated files (`uv.lock`, `Cargo.lock`, `.basedpyright-baseline.json`,
`examples/chat/uv.lock`) are exempt from all mutating hooks; their freshness
is enforced by the `check` group (lock files) or CI (baseline).

AI tool hooks (`.claude/`, `.zcode/`, `.codex/`, `.opencode/`) are thin
wrappers over prek — post-edit: `prek run --group format --group lint
--files <path>`; Stop/idle: `prek run --group lint --all-files`. They contain
no formatter mapping of their own.

## Commands

### Python

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
| Test (cross-process) | `uv run pytest -m slow` |
| Test (all) | `uv run pytest` |

### Rust (run inside `crates/_channels_shm_native/`)

| Action | Command |
|--------|---------|
| Format check | `cargo fmt --check` |
| Lint | `cargo clippy --all-targets --all-features -- -D warnings` |
| Test | `cargo test` |
| Native build | `uvx maturin develop --skip-install` |

### Example app (`examples/chat` — WeChat-style demo + release acceptance)

    cd examples/chat
    uv sync                                   # builds channels-shm from ../.. via maturin
    uv run uvicorn chat.asgi:application --workers 3 --port 8000
    uv run python manage.py demo_broadcast    # headless cross-process acceptance (must PASS)

### Benchmark (via nox)

`nox -s bench_py | bench_rust | bench_cross | bench | check_regression | check_anchors`

### Typecheck baseline

`.basedpyright-baseline.json` is committed and absorbs known errors; the gate
fails only on NEW errors. Refresh it after intentional error changes:

    uv run basedpyright --writebaseline --baselinefile .basedpyright-baseline.json

## Releasing

1. Bump `version` in `pyproject.toml [project]` (single source of truth;
   PEP 440 — `0.1.0rc1` for a release candidate).
2. Add the `## [x.y.z] - YYYY-MM-DD` section to `CHANGELOG.md` (collapse
   `[Unreleased]` into it).
3. Commit `chore: release vX.Y.Z`, then `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. `release.yml` runs: tag/version/changelog guards → full CI (reusable
   `ci.yml`) → abi3 linux wheels (x86_64 + aarch64) + sdist → TestPyPI →
   PyPI (both OIDC Trusted Publishing) → GitHub Release. `rc` tags are
   pre-releases and never marked latest.
5. Pre-tag local acceptance: `examples/chat` against the working tree
   (`demo_broadcast`), optionally against the TestPyPI rc (see its README).

## Quality gates

- Pre-commit: `prek` format + lint + check on staged files
- Pre-push: fast pytest + `cargo test` (mirrors CI; slow/e2e stay CI-only)
- Commit-msg: Conventional Commits (`scripts/check_commit_msg.py`)
- CI: see `.github/workflows/ci.yml` — the prek job plus typecheck/test/
  cross-process/e2e/rust/build jobs, and `.github/workflows/release.yml`

## Code style

### Python

- `ruff` formatter + `select = ["ALL"]` stable-strictest (see `[tool.ruff]` in `pyproject.toml`).
- Every ignore in `pyproject.toml` has an inline comment explaining why; never add a silent ignore.
- Type annotations enforced locally by `basedpyright` (mode = all) and edited in-editor via VSCode.
- Public API is exported from `src/channels_shm/__init__.py` via `__all__`; keep the public surface minimal.
- Async tests use `pytest-asyncio` in `asyncio_mode = "auto"` — do not decorate with `@pytest.mark.asyncio`.
- Helper/tooling scripts live in `scripts/` and are stdlib-only Python (cross-platform by default).

### Rust

- `rustfmt` default style + `clippy -D warnings`.
- Target module size under 500 LoC; split if a file exceeds ~800 LoC.
- No `#[allow(...)]` without an inline comment justifying it.

## Git workflow

- Feature branch → PR → `main`.
- Conventional Commits: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `ci:`.
- `prek` runs on pre-commit (format + lint + check), pre-push (tests), and
  commit-msg (message format). Never bypass with `--no-verify`.

## Boundaries

✅ **Always do**
  - Run `prek run --all-files` (or the matching group) after changes; it is the same gate CI runs.
  - Run `uv lock` and commit `uv.lock` after adding/removing dependencies (root and `examples/chat`).
  - Rebuild native with `uvx maturin develop --skip-install` after changing Rust.
  - Update `__all__` in `src/channels_shm/__init__.py` when the public API changes.
  - Add formatter/linter/consistency hooks ONLY to the prek configs — never to CI workflows or AI tool hooks.

⚠️ **Ask first**
  - Editing the ruff `ignore` list in `pyproject.toml` (affects global rules).
  - Editing `[tool.ruff]` / `[tool.basedpyright]` / `[tool.ty]` configuration.
  - Changing the shared-memory layout in `crates/_channels_shm_native/src/layout.rs` (ABI impact).

🚫 **Never do**
  - Commit `src/channels_shm/_native.abi3.so` (build artifact).
  - Commit `.coverage` / `.pytest_cache/` / `target/` / `.hypothesis/` / `.local/`.
  - Add `# type: ignore` / `noqa` / `#[allow(...)]` without an inline reason.
  - Run `git commit --no-verify` to bypass pre-commit hooks.
  - Edit generated files by hand (`uv.lock`, `Cargo.lock`, `.basedpyright-baseline.json`) — regenerate instead.

## Further reading

- `prek.toml` + `crates/_channels_shm_native/prek.toml` + `examples/chat/prek.toml` — the quality gates (authoritative)
- `.github/workflows/ci.yml` / `release.yml`  CI + release definitions
- `examples/chat/README.md` — demo & acceptance instructions
