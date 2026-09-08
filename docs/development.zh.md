# Development guide

[English](development.md) | 中文

## First-time setup

```bash
uv sync
uvx maturin develop --skip-install   # builds _native.abi3.so into src/
uv tool install prek && prek install # pre-commit + pre-push + commit-msg hooks
```

原生模块必须先构建，任何测试/类型检查才可能通过。Rust 有改动后用 `uvx maturin develop --skip-install` 重建。

## Daily commands

| 操作 | 命令 |
|--------|---------|
| 全部门控（CI 同款） | `prek run --all-files` |
| 仅 format 组 | `prek run --group format --all-files` |
| 仅 lint 组 | `prek run --group lint --all-files` |
| 单文件（AI 钩子同款） | `prek run --group format --group lint --files <path>` |
| 格式化 | `uv run ruff format .` |
| Lint（修复） | `uv run ruff check --fix .` |
| 类型检查（提交式渐进基线） | `uv run basedpyright --baselinefile .basedpyright-baseline.json` |
| 测试（默认，快） | `uv run pytest -m "not slow and not e2e"` |
| 测试（跨进程 + 恢复） | `uv run pytest -m slow` |
| 测试（全部） | `uv run pytest` |
| 文档（严格构建） | `uv sync --extra docs && uv run mkdocs build --strict` |
| 文档（本地预览） | `uv run mkdocs serve` |

Rust 命令在 [`crates/_channels_shm_native/AGENTS.md`](../crates/_channels_shm_native/AGENTS.md)；demo 应用在 [`examples/chat/AGENTS.md`](../examples/chat/AGENTS.md)。

## Documentation site

面向用户的站点（MkDocs Material + mkdocstrings）由 `mkdocs.yml` 从 `docs/site/` 生成；源文件仅英文，API 页实时渲染 Google 风格的源码 docstring。Read the Docs 在每次 push 与 PR 上通过 [`.readthedocs.yaml`](../.readthedocs.yaml) 构建它——先用镜像自带的 Rust 工具链编译 PyO3 扩展，再安装 `.[docs]`。本地配置 extras：`uv sync --extra docs`；日常命令见上表。

首次激活（一次性手动步骤）：在 readthedocs.org 以项目名 `django-channels-shm` 导入本仓库，确认默认 URL `https://django-channels-shm.readthedocs.io/`；其余由已提交的配置完成。设计与决策见 [DCS-RFC 2026-09-08-user-docs-site-on-readthedocs](../.agents/dcs-rfcs/implemented/2026-09-08-user-docs-site-on-readthedocs.zh.md)。

## Type-check baseline

[`.basedpyright-baseline.json`](../.basedpyright-baseline.json) 已提交并吸收已知错误；闸门只对新增错误失败。有意的错误变动后刷新它：

```bash
uv run basedpyright --writebaseline --baselinefile .basedpyright-baseline.json
```

该文件是生成物 —— 绝不手改；CI 的 typecheck job 负责其新鲜度。
