# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[PEP 440](https://peps.python.org/pep-0440/) (`0.1.0rc1` = release
candidate, published as a pre-release).

## [Unreleased]

### Changed

- **prek is the single source of truth for all quality gates.** The flat
  config became a prek workspace: root `prek.toml` plus
  `crates/_channels_shm_native/prek.toml` and `examples/chat/prek.toml`
  (auto-discovered, each running with its own working directory). Hooks are
  grouped `format` (mutating fixers, auto-fix always on) / `lint`
  (read-only gates incl. `ruff check --fix` and clippy) / `check`
  (uv.lock + Cargo.lock freshness, PEP 440 version validity).
- **CI runs the same gate as local commits.** The `format`/`lint`/`rust
  fmt+clippy`/`precommit` jobs collapsed into one `prek run --all-files`
  job; no ruff/cargo invocation is duplicated in CI anymore. `ci.yml` is
  now a reusable workflow (`workflow_call`) used by the release pipeline as
  its test gate.
- **AI tool hooks (.claude/.zcode/.codex/.opencode) are thin prek
  wrappers.** Post-edit hooks run `prek run --group format --group lint
  --files <edited>`; Stop/idle hooks run `prek run --group lint
  --all-files` and block on failure. All formatter mapping previously
  duplicated per tool was deleted.
- Tests moved into the gate at `pre-push` stage (fast pytest + `cargo
  test`), mirroring CI; slow/e2e suites stay CI-only.

### Added

- **Release pipeline** (`release.yml`): pushing a tag `v<PEP 440>` runs the
  full CI, builds abi3 linux wheels (x86_64 + aarch64) + sdist via
  maturin-action, publishes to TestPyPI then PyPI (OIDC Trusted
  Publishing), and cuts the GitHub Release from this changelog. `rc` tags
  are pre-releases and never marked `latest`.
- **Example app `examples/chat`** — a multi-process Django/Channels chat
  with zero infrastructure (no Redis, no DB). `uv sync` there builds the
  library from the working tree through maturin, so it doubles as the
  pre-release acceptance project (`manage.py demo_broadcast` asserts
  cross-process fan-out; `manage.py run_workers` starts real multi-process
  workers for the browser demo).
- `scripts/`: `check_version.py` (PEP 440 gate), `check_commit_msg.py`
  (Conventional Commits, commit-msg stage), `check_agents.py`
  (AGENTS.md == CLAUDE.md).
- Lock/freshness gates: `uv-lock` hooks in root and example configs,
  `cargo metadata --locked` in the crate config; generated files
  (`uv.lock`, `Cargo.lock`, `.basedpyright-baseline.json`) are exempt from
  all mutating hooks.

[Unreleased]: https://github.com/jukanntenn/django-channels-shm/commits/HEAD
