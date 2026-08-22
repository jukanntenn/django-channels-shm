# Releasing

English | [中文](releasing.zh.md)

1. Bump `version` in `pyproject.toml [project]` (single source of truth; PEP 440 — `0.1.0rc1` for a release candidate).
2. Add the `## [x.y.z] - YYYY-MM-DD` section to [CHANGELOG.md](../CHANGELOG.md) (collapse `[Unreleased]` into it).
3. Commit `chore: release vX.Y.Z`, then `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. [release.yml](https://github.com/jukanntenn/django-channels-shm/blob/main/.github/workflows/release.yml) runs: tag/version/changelog guards → full CI (reusable `ci.yml`) → abi3 linux wheels (x86_64 + aarch64) + sdist → TestPyPI → PyPI (both OIDC Trusted Publishing) → GitHub Release. `rc` tags are pre-releases and never marked latest.
5. Pre-tag local acceptance: `examples/chat` against the working tree (`demo_broadcast`, see [`examples/chat/AGENTS.md`](../examples/chat/AGENTS.md)), optionally against the TestPyPI rc (see its README).
