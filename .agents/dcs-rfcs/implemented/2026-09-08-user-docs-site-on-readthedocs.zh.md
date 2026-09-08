# DCS-RFC: user-facing documentation site on Read the Docs

Status: implemented

[English](2026-09-08-user-docs-site-on-readthedocs.md) | 中文

## Problem

仓库的用户文档只是一份双语 README —— 一个落地页，而非文档站点 —— 而库已经长出真实的消费面（安装、快速开始、概念、配置、API 参考），靠手维护的 Markdown 难以复述。手写的 API 目录会与代码漂移，文档也没有托管、版本化的分发渠道。原生扩展（PyO3、maturin 后端）让文档托管比纯 Python 包更难：任何从活对象渲染 API 文档的站点都必须导入编译后的 `_native.abi3.so`，而它在 Rust 工具链完成构建之前不可导入。

## Decision

面向用户的 MkDocs 站点由 `mkdocs.yml`（仓库根）从 `docs/site/` 生成，由 `.readthedocs.yaml` 通过 Read the Docs 分发：

- 工具链：MkDocs + Material 主题 + `mkdocstrings`（经 griffe 解析 Google 风格 docstring）。API 页每个公开模块/类一行指令，实时渲染源码 docstring，代码即 API 目录的唯一来源。
- 构建：`.readthedocs.yaml` 在 RTD 镜像内编译扩展 —— `build.tools.rust: "1.86"`、`build.os: ubuntu-24.04`、Python 3.13 —— 然后 `pip install .[docs]` 安装真实包（`pyproject.toml` 的 `[project.optional-dependencies].docs`），使 `mkdocstrings` 能 import `channels_shm` 并从源码渲染成员。这与 pyca/cryptography 在 RTD 上用 maturin 跑生产构建的模式一致。
- 严格性：RTD 上 `mkdocs.fail_on_warning: true`；本地闸门为 `uv sync --extra docs && uv run mkdocs build --strict`。一条 warning 即红色构建。
- 分发模型：Read the Docs 版本语义 —— `latest` 跟随 `main`，`stable` 跟随最新 semver tag，PR 自动出预览。首次激活是一次性的手动导入：在 readthedocs.org 以项目名 `django-channels-shm` 导入仓库（记录在 `docs/development.md`）。
- 内容（仅英文）：Overview（`index.md`）、Installation、Quickstart、Concepts、Configuration，以及 API 参考的两页生成页 —— `SharedMemoryChannelLayer`（`reference/layer.md`）与异常模块（`reference/exceptions.md`）。站点是用户文档的扩展主场；README 链接它并保留入门级内容。`docs/site/` 在 `scripts/doc_languages.manifest.json` 中被排除出双语配对闸门（单语语料是决策而非疏漏），`docs/AGENTS.md` 的分层表也为其命名。
- 公开 API 不新增强制 docstring lint（ruff `D` 仍被忽略）；渲染出的页面如实呈现现有 docstring。API 文档质量按构造即是 docstring 质量。

## Alternatives considered

**Sphinx + autodoc + sphinx-rtd-theme。** Python 生态的经典栈，django/channels 与 pyca/cryptography 都在用，RTD 视其为头等公民。它在写作与迭代速度上落败：reStructuredText 是本仓库从不书写的第二种标记语言，而对一个新站，Material-for-MkDocs 提供更活跃的主题/插件生态（admonition、代码复制、导航 UX），mkdocstrings 以 Google 风格 docstring 覆盖同样的 autodoc 需求。两条栈都满足自动 API 文档、都能在 RTD 上编译原生扩展；能力对等时现代工具胜出。

**手写 API 页**（channels 与 cryptography 完全不用 autodoc，手写 `.. function::`/`.. class::` 树）。落败原因：手维护的签名目录正是本决策要消除的漂移，而 cryptography 为此付着高昂的维护账单。

**用 mock 替代真实构建。** RTD 的 `autodoc_mock_imports`（或 mkdocstrings 不 import 的静态检查）可避免编译扩展，但渲染的是存根而非签名，还会掩盖 import 错误。镜像自带 Rust 工具链，真实编译只花构建时间；cryptography 已证明该路线。

**中文优先或全双语站点。** 仓库 Markdown 语料按标准双语，zh 站点也认真考虑过以保持对称。发布期落败：RTD 多语言意味着第二个翻译项目外加逐页 gettext/PO 流水线，而 OSS 文档惯例是英文优先。英文语料今天保持自洽；`docs/site/` 从配对闸门豁免，翻译项目可日后跟进而无需重构。

**自托管或 Pages（pydantic 式 mike 分支、Cloudflare Pages）。** 输给对 Read the Docs 的既定偏好：`latest`/`stable` 版本语义、自动 PR 预览、徽章，以及零发布管线维护。

## Consequences

RTD 每次构建文档都要编译 Rust，因此文档构建比纯 Python 站点慢 —— 每次 push/PR 数分钟，RTD 尽量缓存。`docs/site/` 按决策保持单语，处于其余语料所在的配对/折行闸门之外，其 Markdown 不受 `doc_sync` 检查；评审依赖严格构建。README 与站点在入门级内容上有意重叠，直到 README 瘦身；本 RFC 记录该收敛点以便跟进。API 页渲染公开 API 的 Google 风格 docstring（Args/Raises 小节），layer 页的 `merge_init_into_class` 选项把构造器的配置参数作为权威参数目录呈现。站点 URL 与仓库导入名在一次性激活前为假设值（`django-channels-shm.readthedocs.io`）；如有出入，只需在 `mkdocs.yml`、`.readthedocs.yaml`、两份 README 与本文件各改一行。
