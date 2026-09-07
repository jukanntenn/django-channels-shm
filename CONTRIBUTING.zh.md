# 参与贡献 django-channels-shm

[English](CONTRIBUTING.md) | 中文

感谢你为这个项目投入时间 —— 欢迎任何形式的贡献：代码、测试、文档、基准数据与示例应用改进。参与本项目即表示你同意遵守[行为准则](.github/CODE_OF_CONDUCT.zh.md)。

## 贡献方式

- 修 bug、补测试、改进文档 —— 带 [`good first issue`](https://github.com/jukanntenn/django-channels-shm/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) 标签的 issue 是为新手准备的入口。
- 报 bug、提功能 → 开 issue（模板会引导你）。
- 提问与方向讨论 → GitHub Discussions；有价值的讨论会被蒸馏为 DCS-RFC。
- 提出架构或行为级变更 → DCS-RFC 流程（见下文）。

## 基本规则

任何变更都先开 issue —— 包括错字与文档修正。先开一个简短的 issue，再提 PR；没有关联 issue 的 PR 可能被关闭。这能让工作可见，也能避免重复劳动。

非平凡变更 —— 新能力、并发语义、共享内存布局（ABI）、工具链或流程 —— 还需在同一变更集中附一份 DCS-RFC。格式见 [.agents/dcs-rfcs/README.zh.md](.agents/dcs-rfcs/README.zh.md)；先 grep 一下现有树，该决策可能已有归属。

## 开发环境

你需要 Linux、Rust 工具链（≥ 1.86）和 [uv](https://docs.astral.sh/uv/)。

1. fork 仓库，克隆你的 fork 并创建 feature 分支。
2. `uv sync` —— 安装 Python 环境。
3. `uvx maturin develop --skip-install` —— 构建原生模块。
4. `uv tool install prek && prek install` —— 安装提交钩子。
5. `uv run pytest -m "not slow and not e2e"` —— 确认环境可用。

> 任何测试与类型检查都以原生模块已构建为前提，且每次改动 Rust 后都要重建。这是新贡献者最容易踩的坑。

完整的日常工作流 —— 门禁分组、类型检查基线、格式化 —— 见 [docs/development.zh.md](docs/development.zh.md)。

## 测试

行为变更必须附带测试；纯文档变更除外。快速套件：

```bash
uv run pytest -m "not slow and not e2e"
```

跨进程（`-m slow`）、Docker e2e 与 Rust（在 `crates/_channels_shm_native/` 下 `cargo test`）套件的说明见 README 的[测试](README.zh.md#测试)一节与[crate 子树规范](crates/_channels_shm_native/AGENTS.md)。

## Pull Request 流程

1. 先开 issue（见基本规则），再从 `main` 拉 feature 分支。
2. commit message 遵循 [Conventional Commits](https://www.conventionalcommits.org/)：`feat:`、`fix:`、`test:`、`docs:`、`refactor:`、`chore:`、`ci:`。`commit-msg` 钩子会校验 —— 永远不要用 `--no-verify` 绕过钩子。
3. 本地跑通 `prek run --all-files`。CI 跑的就是同一个门禁 —— 不存在第二套标准。
4. 填写 PR 模板；用 `Fixes #NN`（自动关闭）或 `Related to #NN`（仅关联）链接 issue。
5. 欢迎用 draft PR 尽早求反馈。

## 接受与不接受

- **欢迎：** bug 修复、测试与覆盖率、文档（以双语对形式）、可复现的基准数据、`examples/chat` 增强，以及 DCS-RFC 文档本身。
- **先讨论：** 新功能、并发语义、共享内存布局（ABI 影响）、依赖变更，以及 ruff/basedpyright 配置的修改。

## AI 辅助贡献者

本仓库欢迎 AI 辅助开发，并为此配了完整的指令体系 —— 用 agent 开发时，让它先读 `AGENTS.md` 与 `.agents/skills/` 下的技能。

## 求助

环境配置或入手方向卡住了？看[支持指南](.github/SUPPORT.zh.md) —— 提问去 Discussions，可复现的 bug 去 issues。
