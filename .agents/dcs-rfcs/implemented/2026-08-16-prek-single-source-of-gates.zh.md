# DCS-RFC: prek is the single source of every quality gate

Status: implemented

[English](2026-08-16-prek-single-source-of-gates.md) | 中文

## Problem

格式化、lint 与一致性检查面临被并行定义的风险：AI 工具钩子一套、CI 一套、人的肌肉记忆又一套。formatter 与 lint 覆盖在工具间泄漏意味着“跑一下 linter”在不同上下文里是不同的事 —— 格式化冲突与 lint 逃逸在本地复现而 CI 又不认，且每一条 agent 面向的约定都会漂移，除非有机械的东西拥有它。

## Decision

流水线运行的每个检查都只声明一次，在 prek 里：根 `prek.toml`（通用文件检查、typos、actionlint、ruff、uv-lock 新鲜度、版本检查、文档闸门、agent 指令镜像、pre-push 快速 pytest）、`crates/_channels_shm_native/prek.toml`（cargo fmt / clippy / Cargo.lock）、`examples/chat/prek.toml`（demo 自己的轻量策略，prek orphan）。调用契约固定：AI post-edit 钩子跑 `prek run --group format --group lint --files <被编辑文件>`，AI Stop 钩子跑 `prek run --group lint --all-files`，CI 的 lint job 恰好跑 `prek validate-config` + `prek run --all-files` —— 与本地提交同一道闸门，无从漂移。`git commit` 跑 pre-commit 阶段，`commit-msg` 检查 Conventional Commits，pre-push 跑测试。四个 AI 工具钩子树（`.zcode/`、`.claude/`、`.codex/`、`.opencode/`）是这些组的薄封装，自身不含任何 formatter 映射。任何 formatter、linter、闸门都不在 prek 配置之外定义 —— 不在 CI 工作流里，不在 AI 工具钩子里。

## Alternatives considered

**按工具分别配置钩子。** 每个 AI 工具配自己的 formatter/lint 命令。它输在：这正是本决策要消除的并行定义 —— N 个工具就是 N 份各自漂移的定义，加载 `CLAUDE.md` 的工具与加载 `AGENTS.md` 的工具会无声地各执一词。

**第二个钩子运行器（lefthook、husky）。** 能力不俗；它输在：prek 是因跨 Python 根、Rust crate 与 demo orphan 项目的原生多语言钩子管理而入选的，再并一个运行器只会重新引入要消除的并行定义。

**只在 CI 强制。** 定义唯一，但它输在：反馈迟到数分钟，本地提交积累未格式化的工作；分层模型 —— 提交时 `format` 组快速修复、`lint` 组只读闸门、推送时测试、CI 全量 —— 本来就需要本地钩子。

## Consequences

`prek install` 是一次性安装，跑钩子的每个环境看到相同行为。加一个检查等于改一个 prek 文件 —— 文档闸门（[doc_sync](../../../scripts/doc_sync.py)）与 agent 指令镜像正是沿这条路接入的。提交必须经过钩子链；`--no-verify` 不可用。代价：prek 版本漂移由 `minimum_prek_version` 与冻结的 hook rev 钉住，更新必须是有意为之。
