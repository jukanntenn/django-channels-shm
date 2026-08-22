# Coding principles

[English](PRINCIPLES.md) | 中文

针对 agent 的行为约束。每一条都是不说 agent 就会做错的规则。生产安全与数据完整性高于此处所有原则 —— 包括从零重写的许可；与单纯的默认或风格规则相抵时，原则获胜。

## Ground every conclusion in fact

库的事实、API 与协议必须先读源码或文档再据以行动 —— 训练数据是盲区，不是来源。每个结论都要在现场验证：Python 与 Rust 逻辑用 `file:line`，行为用 `uv run pytest`，native 行为用 `crates/_channels_shm_native/` 内的 `cargo test`，跨进程验收用 `examples/chat` 里的 `uv run python manage.py demo_broadcast`，运行时行为用对运行中栈的只读观测。纯算法或语法知识可以用训练知识。

做错的样子：凭记忆猜一个 `py_bindings` 签名或共享内存布局，而不是对照 `_native.pyi` 契约去读 `crates/_channels_shm_native/src/py_bindings.rs` 与 `layout.rs` —— ABI 不匹配会静默损坏每个进程共享的区域。本层依赖的接口 —— channels/asgiref 的 `ChannelLayer`、msgpack 序列化、`eventfd`/`AF_UNIX` 唤醒、`MAP_SHARED` 语义 —— 依赖之前必须读它们的活源码。

## Defer to community convention

当约定或最佳实践不确定时，先问“社区/官方约定是什么”，并对权威开源源码验证，而不是依赖训练记忆（例如 `format`/`lint` 在这里是不是 prek 组名、pyo3/maturin 期望 abi3 扩展怎么构建 —— 两者都可对照工具自身的 schema/文档验证）。

与 *Ground every conclusion in fact* 的区别：那条管的是你正在集成的库的事实；这条管的是约定与最佳实践决策。

## Converge before you implement

规格或计划必须自含、完整、无歧义 —— 一个没有品位的执行者也能机械落地，没有即兴发挥的空间。实现之前解决所有开放问题；不要在半定稿的计划上开工。

## Fix the root cause, not the symptom

你选择的方案必须是最自然、最优的 —— 不是给症状打补丁，也不是被既有实现困住。当根修需要时，可以抛开全部遗留、从零开始。

formatter/lint 覆盖曾在多个工具间泄漏时，修法不是分别配置每个 AI 钩子，而是把 `prek.toml` 立为唯一事实 —— prek 之外不存在任何平行的 formatter 或 lint 定义。

## Design from first principles

从业务本质推导设计；一切前提皆可打破；优雅的方案胜过继承来的方案。与 *Fix the root cause, not the symptom* 的区别：那条说的是怎么**修**问题（根因，而非补丁）；这条说的是怎么**设计**系统（重新推导、质疑假设）。彻底放弃 broker —— 用一个 `MAP_SHARED` 区域上的无锁 MPMC ring + slab 取代在 Redis 上叠层 —— 就是这条原则的应用。

## Single source of truth

每类信息恰好有一个权威来源：共享内存布局是 `crates/_channels_shm_native/src/layout.rs`（ABI —— 动之前先问），绑定契约是 `py_bindings.rs` + `_native.pyi` stub，发布版本是 `pyproject.toml [project].version`（由 maturin 盖进每个 wheel），一切质量闸门只活在 `prek.toml`。公共 API 从 `src/channels_shm/__init__.py` 的 `__all__` 导出；保持面最小。生成文件（`uv.lock`、`Cargo.lock`、`.basedpyright-baseline.json`）只能重新生成、绝不手改；`AGENTS.md` 与 `CLAUDE.md` 保持字节一致（prek 闸门）。

## Naming is part of the API

名字就是 API 面。名字不合业务含义时不要硬用 —— 头脑风暴候选并让用户选择，防止语义漂移。channel 名、group 名、错误面（`ChannelFull`、`ChannelLayer`）都是公共契约。

## Degrade gracefully, never silently

失败必须被处理、被可观测地记录，并且不得阻塞下游 —— 但静默失败永远是错的。一个 worker 死在消息中间，其 ring 与槽位必须被 watchdog 回收而不阻塞他人；channel 满了必须抛错、绝不静默丢弃；配置校验在启动时大声失败，而不是回退到没人知道的默认值。debug 构建带 watchdog、结构化日志与指标；`python -O` 完全剥离。

## Minimal mock, maximal real

只 mock 请求边界，绝不 mock 整个服务。测试跑在 `/dev/shm` 的真实共享内存区域上 —— 跨进程用真实 multiprocessing，e2e 用 Docker 里的真实 Django/channels 栈 —— 不给被测对象用任何内存替身。本地与 CI 尽可能跑同一套件（slow/e2e 按设计只在 CI 跑）。
