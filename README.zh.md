# django-channels-shm

[English](README.md) | 中文

[![CI](https://github.com/jukanntenn/django-channels-shm/actions/workflows/ci.yml/badge.svg)](https://github.com/jukanntenn/django-channels-shm/actions/workflows/ci.yml)
[![License: BSD-3-Clause](https://img.shields.io/badge/License-BSD_3--Clause-blue.svg)](LICENSE)

一个面向 **Django Channels** 的高性能 **共享内存信道层（channel layer）**，专为单机多进程部署设计。消息通过 `/dev/shm` 中的 `mmap(MAP_SHARED)` 区域在 ASGI worker 之间传递 —— 无需 Redis、无需 TCP、无需 broker —— 热路径运行在 **Rust 原生扩展（PyO3）** 中。

```
ASGI worker A ──send──► ┌───────────────────────────────┐ ──receive──► ASGI worker B
                        │  /dev/shm (MAP_SHARED)        │
                        │  lock-free MPMC rings + slab  │
                        │  channel/group indexes        │
                        │  eventfd / AF_UNIX wakeup     │
                        └───────────────────────────────┘
```

## 特性

- **零拷贝共享内存**：channel 与 group 全部存于同一共享区域；不超过 `inline_size` 的消息直接写入 ring 槽位（无分配、无序列化中转）。
- **无锁热路径**：`send`/`receive` 使用 Rust 实现的 Vyukov 有界 MPMC ring，每个槽位带独立序号。
- **崩溃恢复**：每个槽位记录 owner（`pid` + 进程启动时间）。检测到 owner 已死即安全回收其 ring/槽位 —— 某个 worker 崩溃不会阻塞其他 worker。
- **事件驱动唤醒**：进程内用 `eventfd`，跨进程用 `AF_UNIX` 数据报 socket。无轮询、无忙等。
- **完整 channels API**：`send` / `receive` / `new_channel` / `group_add` / `group_discard` / `group_send` / `flush`，进程专属通道（`!` 后缀）、按通道容量覆盖与消息过期。
- **开发可观测，生产高性能**：debug 构建带 watchdog、结构化日志与指标；`python -O` 运行时完全剥离。
- **测试充分**：单元、Hypothesis 属性、状态机、并发、跨进程、Docker e2e 全套测试（见[测试](#测试)）。

## 环境要求

- **Linux**（x86-64；AArch64 尽力支持）—— 依赖 `MAP_SHARED` 与 `AF_UNIX`
- **Python ≥ 3.11**
- **Rust ≥ 1.86**（仅构建原生扩展时需要）
- **channels ≥ 4.0**（唯一的运行时依赖 —— 层本身不依赖 Django，可用于 channels 支持的任何 Django/ASGI 技术栈）

在 Linux x86-64 上以 Python 3.11–3.13 × channels ≥ 4.0 做持续测试（CI 矩阵）。

## 安装

尚未发布到 PyPI，可从 GitHub 安装（需要 Rust 工具链，maturin 会在安装时构建 abi3 wheel）：

```bash
pip install git+https://github.com/jukanntenn/django-channels-shm.git
```

### 开发环境

```bash
uv sync
uvx maturin develop --skip-install   # builds _native.abi3.so into src/
```

构建原生模块后，测试与类型检查才能运行。

## 快速开始

```python
# settings.py
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_shm.SharedMemoryChannelLayer",
        "CONFIG": {
            "capacity": 100,
            "shm_size": 256 * 1024 * 1024,
        },
    },
}
```

同机所有 ASGI worker 共享同一区域：使用相同 `prefix`（默认 `"channels_shm"`）实例化即可。无需启动任何服务 —— 共享区域与唤醒 socket 会在 `/dev/shm` 中按需创建。

### 配置项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `prefix` | `"channels_shm"` | 共享区域与唤醒 socket 的命名空间。最长 53 字符（唤醒 socket 路径 `/dev/shm/{prefix}_wakeup/{client}.sock` 须满足 108 字节 AF_UNIX 限制）。 |
| `capacity` | `100` | 每个 channel 的 ring 默认容量（消息条数）。 |
| `channel_capacity` | `None` | `{正则: 容量}` 覆盖，如 `{"^video\.": 1000}`。 |
| `expiry` | `60` | 消息过期时间（秒）。 |
| `group_expiry` | `86400` | group 成员关系过期时间（秒）。 |
| `shm_size` | `256 MiB` | 共享区域上限。 |
| `inline_size` | `512` | 不超过该大小的消息内联存储于 ring 槽位。 |
| `max_channels` | `10000` | channel 索引条目上限。 |
| `max_groups` | `1000` | group 索引条目上限。 |
| `max_processes` | `4096` | 注册表中进程数上限。 |
| `max_members_per_group` | `1024` | 每个 group 成员数上限。 |
| `watchdog_interval` | `30` | watchdog 巡检间隔（秒），`None` 关闭。 |
| `obs_dir` | `None` | 可观测性输出目录（指标/日志；仅 debug 构建）。 |

## 性能基准

以下数字在 **2 核 CPU / 2 GB 内存** 的 Docker 容器中测得（`bench/docker/docker-compose.yml`），同一容器内跑完三套信道层，`channels_redis` 基线使用容器内的本地 `redis-server`。发布模式（`python -O`）。

| 场景（2 核 / 2 GB，50 B 消息） | InMemory | django-channels-shm | channels_redis |
|--------------------------------|---------:|-------------:|---------------:|
| 单进程 send+receive 往返      | 111k ops/s | 60k ops/s | — |
| 跨进程发送 S2（2 进程）        | — | 119k msg/s | 1.9k msg/s |
| 组广播 S4（4 个接收者）        | — | 10.5k msg/s | 672 msg/s |

- 跨进程发送吞吐约为 `channels_redis` 的 **61×**
- 组广播吞吐约为 `channels_redis` 的 **16×**
- 单进程往返延迟仅为纯内存层的 ~1.8× —— 这是“能在进程间共享消息”的代价。

延迟明细（7 次运行中位数）：

| 场景 | 实现 | send p50 / p99 | recv p50 |
|------|------|---------------:|---------:|
| S2 跨进程 | django-channels-shm | 7.4 µs / 35 µs | 2.0 ms |
| S2 跨进程 | channels_redis | 490 µs / 862 µs | 19.2 ms |
| 往返（单进程） | InMemory | 8.3 µs / 27 µs | — |
| 往返（单进程） | django-channels-shm | 15.0 µs / 53 µs | — |

> 该测试方法下 `recv` 延迟包含排队时间：发送方无背压地连发 `count` 条消息，接收方需要消化积压。send 侧指标是干净的对比；完整逐次运行数据已提交在 `bench/docker/results/`，测试方法学见 [docs/benchmarking.zh.md](docs/benchmarking.zh.md)。

### 复现

```bash
cd bench/docker
docker compose build
docker compose run --rm bench        # prints the full JSON summary
```

## 示例应用

[`examples/chat`](examples/chat/README.zh.md) 是一个微信风格的多进程 Django + Channels 聊天室，**零基础设施** —— 无需 Redis、无需数据库。它同时是发布前验收项目：在该目录 `uv sync` 会通过 maturin 从工作树真实构建 django-channels-shm，`manage.py demo_broadcast` 以无头方式断言跨进程消息分发。

```bash
cd examples/chat
uv sync                                   # builds django-channels-shm from ../.. via maturin
uv run uvicorn chat.asgi:application --workers 3 --port 8000
uv run python manage.py demo_broadcast    # headless acceptance: must print PASSED
```

在浏览器多个标签页打开 <http://127.0.0.1:8000/>，选昵称开聊 —— 按昵称私聊、按群名群聊（每群上限 500 人）。所有标签页访问同一端口；内核把连接分摊到各 worker 进程，每条消息都经 `/dev/shm` 跨进程流转（悬停消息可见是由哪个 worker PID 投递的）。截图与实现细节：[`examples/chat/README.zh.md`](examples/chat/README.zh.md)（English: [`examples/chat/README.md`](examples/chat/README.md)）。

## 测试

```bash
# fast unit / property / concurrency suite (no docker)
uv run pytest -m "not slow and not e2e"

# cross-process integration (Linux, multiprocessing)
uv run pytest -m slow

# Django/channels stack e2e — 3 ASGI workers via docker compose
cd tests/e2e
docker compose build
docker compose up -d worker1 worker2 worker3
docker compose run --rm runner pytest tests/e2e/ -v
```

## 开发

| 操作 | 命令 |
|------|------|
| 格式化 | `uv run ruff format .` |
| Lint | `uv run ruff check .` |
| 类型检查 | `uv run basedpyright`（渐进式基线，CI 只拦截新增错误） |
| Pre-commit | `prek run --all-files` |
| Rust 格式化 / Lint / 测试 | `cargo fmt --check && cargo clippy --all-targets --all-features -- -D warnings && cargo test` |

完整的日常工作流 —— 门禁分组、类型检查基线、格式化 —— 见 [docs/development.zh.md](docs/development.zh.md)。

CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）运行 `prek run --all-files`、basedpyright（Python 3.12–3.13）、Python 3.11–3.13 矩阵上的快速测试套件、`python -O` 发布模式冒烟、慢速跨进程套件、Docker e2e、`cargo test` 与 maturin wheel 构建。

## 社区与支持

- **用法、配置与调优问题** → [GitHub Discussions](https://github.com/jukanntenn/django-channels-shm/discussions)（Q&A 分类，先搜索再问）。有价值的讨论会被蒸馏为 DCS-RFC。
- **可复现 bug 与功能请求** → [GitHub issues](https://github.com/jukanntenn/django-channels-shm/issues/new/choose) —— 模板会引导你；什么样的报告算「具体」见[支持指南](.github/SUPPORT.zh.md)。
- **安全漏洞** → 按[安全策略](.github/SECURITY.zh.md)走私密报告渠道，绝不开公开 issue。

支持为社区性尽力而为，不提供商业支持或 SLA。

## 参与贡献

任何变更都先开 issue —— 包括错字修正。[贡献指南](CONTRIBUTING.zh.md)覆盖环境搭建、基本规则与接受范围；带 [`good first issue`](https://github.com/jukanntenn/django-channels-shm/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) 标签的 issue 是新手入口。

本项目由 jukanntenn 与贡献者们以尽力而为的方式维护。架构与行为级变更走 DCS-RFC 流程 —— 提案与决策记录见 [`.agents/dcs-rfcs/`](.agents/dcs-rfcs/README.zh.md) —— 同时欢迎 AI 辅助贡献（见 `AGENTS.md`）。

## License

BSD-3-Clause。见 [LICENSE](LICENSE)。
