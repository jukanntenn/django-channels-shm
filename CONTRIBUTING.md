# Contributing to django-channels-shm

English | [中文](CONTRIBUTING.zh.md)

Thanks for investing your time in this project — contributions of many forms are welcome: code, tests, documentation, benchmarks, and example-app improvements. By participating, you agree to uphold the [Code of Conduct](.github/CODE_OF_CONDUCT.md).

## Ways to contribute

- Fix bugs, add tests, or improve documentation — issues labeled [`good first issue`](https://github.com/jukanntenn/django-channels-shm/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) are the intended entry point.
- Report bugs or request features → open an issue (the templates guide you).
- Ask questions and discuss directions → GitHub Discussions; valuable discussions get distilled into a DCS-RFC.
- Propose architecture or behavior changes → the DCS-RFC process (see below).

## Ground rules

Every change starts with an issue — even typo and documentation fixes. Open a quick issue first, then the pull request; an unlinked pull request may be closed. This keeps the work visible and duplicates away.

Non-trivial changes — new capabilities, concurrency semantics, shared-memory layout (ABI), tooling or process — additionally require a DCS-RFC in the same changeset. See [.agents/dcs-rfcs/README.md](.agents/dcs-rfcs/README.md) for the format; `grep` the tree first, the decision may already have a home.

## Development environment

You need Linux, a Rust toolchain (≥ 1.86), and [uv](https://docs.astral.sh/uv/).

1. Fork, then clone your fork and create a feature branch.
2. `uv sync` — install the Python environment.
3. `uvx maturin develop --skip-install` — build the native module.
4. `uv tool install prek && prek install` — install the commit hooks.
5. `uv run pytest -m "not slow and not e2e"` — confirm the environment works.

> The native module MUST be built before any test or type-check can run, and rebuilt after every Rust change. This is the single most common trap for new contributors.

The full daily workflow — gate groups, type-check baseline, formatting — lives in [docs/development.md](docs/development.md).

## Testing

Behavior changes come with tests; documentation-only changes are exempt. The fast suite:

```bash
uv run pytest -m "not slow and not e2e"
```

Cross-process (`-m slow`), Docker e2e, and Rust (`cargo test` in `crates/_channels_shm_native/`) suites are described in the [Testing](README.md#testing) section of the README and the [crate orders](crates/_channels_shm_native/AGENTS.md).

## Pull requests

1. Open the issue first (see Ground rules), then a feature branch off `main`.
2. Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `ci:`. The `commit-msg` hook enforces this — never bypass hooks with `--no-verify`.
3. Make `prek run --all-files` pass locally. CI runs exactly the same gate — there is no second standard.
4. Fill in the pull request template; link the issue with `Fixes #NN` (auto-close) or `Related to #NN` (link only).
5. Draft pull requests are welcome for early feedback.

## What we accept

- **Welcome:** bug fixes, tests and coverage, documentation (as bilingual pairs), reproducible benchmark data, `examples/chat` improvements, and DCS-RFC documents themselves.
- **Discuss first:** new features, concurrency semantics, shared-memory layout (ABI impact), dependency changes, and edits to the ruff/basedpyright configuration.

## For AI-assisted contributors

This repository welcomes AI-assisted development and ships a full instruction system for it — when driving an agent, have it read `AGENTS.md` and the skills under `.agents/skills/` first.

## Getting help

Stuck on setup or where to start? See the [support guide](.github/SUPPORT.md) — questions belong in Discussions, reproducible bugs in issues.
