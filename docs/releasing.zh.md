# Releasing

[English](releasing.md) | 中文

1. 在 `pyproject.toml [project]` 提升 `version`（唯一事实源；PEP 440 —— 候选版用 `0.1.0rc1`）。
2. 在 [CHANGELOG.md](../CHANGELOG.md) 增加 `## [x.y.z] - YYYY-MM-DD` 小节（把 `[Unreleased]` 折入其中）。
3. 提交 `chore: release vX.Y.Z`，然后 `git tag vX.Y.Z && git push origin vX.Y.Z`。
4. [release.yml](https://github.com/jukanntenn/django-channels-shm/blob/main/.github/workflows/release.yml) 依次运行：tag/版本/changelog 守卫 → 完整 CI（复用 `ci.yml`）→ abi3 linux wheel（x86_64 + aarch64）+ sdist → TestPyPI → PyPI（均为 OIDC Trusted Publishing）→ GitHub Release。`rc` 标记为预发布、永不标记 latest。
5. 打 tag 前的本地验收：用工作树跑 `examples/chat`（`demo_broadcast`，见 [`examples/chat/AGENTS.md`](../examples/chat/AGENTS.md)），可选再对 TestPyPI rc 验收（见其 README）。
