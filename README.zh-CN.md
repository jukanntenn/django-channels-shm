# channels-shm

[![CI](https://github.com/jukanntenn/django-channels-shm/actions/workflows/ci.yml/badge.svg)](https://github.com/jukanntenn/django-channels-shm/actions/workflows/ci.yml)

一个面向 **Django Channels** 的高性能 **共享内存信道层（channel layer）**，
专为单机多进程部署设计。消息通过 `/dev/shm` 中的 `mmap(MAP_SHARED)` 区域在
ASGI worker 之间传递 —— 无需 Redis、无需 TCP、无需 broker —— 热路径运行在
**Rust 原生扩展（PyO3）** 中。

```
ASGI worker A ──send──► ┌───────────────────────────────┐ ──receive──► ASGI worker B
                        │  /dev/shm (MAP_SHARED)        │
                        │  无锁 MPMC ring + slab 分配器  │
                        │  channel/group 索引            │
                        │  eventfd / AF_UNIX 唤醒        │
                        └───────────────────────────────┘
```

## 特性

- **零拷贝共享内存**：channel 与 group 全部存于同一共享区域；不超过
  `inline_size` 的消息直接写入 ring 槽位（无分配、无序列化中转）。
- **无锁热路径**：`send`/`receive` 使用 Rust 实现的 Vyukov 有界 MPMC ring，
  每个槽位带独立序号。
- **崩溃恢复**：每个槽位记录 owner（`pid` + 进程启动时间）。检测到 owner
  已死即安全回收其 ring/槽位 —— 某个 worker 崩溃不会阻塞其他 worker。
- **事件驱动唤醒**：进程内用 `eventfd`，跨进程用 `AF_UNIX` 数据报 socket。
  无轮询、无忙等。
- **完整 channels API**：`send` / `receive` / `new_channel` /
  `group_add` / `group_discard` / `group_send` / `flush`，进程专属通道
  （`!` 后缀）、按通道容量覆盖与消息过期。
- **开发可观测，生产高性能**：debug 构建带 watchdog、结构化日志与指标；
  `python -O` 运行时完全剥离。
- **测试充分**：单元、Hypothesis 属性、状态机、并发、跨进程、Docker e2e
  全套测试（见[测试](#测试)）。

## 环境要求

- **Linux**（x86-64；AArch64 尽力支持）—— 依赖 `MAP_SHARED` 与 `AF_UNIX`
- **Python ≥ 3.11**
- **Rust ≥ 1.83**（仅构建原生扩展时需要）
- **Django ≥ 5.2**、**channels ≥ 4.0**（运行时依赖）

## 安装

尚未发布到 PyPI，可从 GitHub 安装（需要 Rust 工具链，maturin 会在安装时
构建 abi3 wheel）：

```bash
pip install git+https://github.com/jukanntenn/django-channels-shm.git
```

### 开发环境

```bash
uv sync
uvx maturin develop --skip-install   # 构建 _native.abi3.so 到 src/
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

同机所有 ASGI worker 共享同一区域：使用相同 `prefix`（默认
`"channels_shm"`）实例化即可。无需启动任何服务 —— 共享区域与唤醒 socket
会在 `/dev/shm` 中按需创建。

### 配置项

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `prefix` | `"channels_shm"` | 共享区域与唤醒 socket 的命名空间。最长 62 字符（受 AF_UNIX 路径长度限制）。 |
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

以下数字在 **2 核 CPU / 2 GB 内存** 的 Docker 容器中测得
（`bench/docker/docker-compose.yml`），同一容器内跑完三套信道层，
`channels_redis` 基线使用容器内的本地 `redis-server`。发布模式
（`python -O`）。

| 场景（2 核 / 2 GB，50 B 消息） | InMemory | channels-shm | channels_redis |
|--------------------------------|---------:|-------------:|---------------:|
| 单进程 send+receive 往返      | 109k ops/s | 62k ops/s | — |
| 跨进程发送 S2（2 进程）        | — | 118k msg/s | 1.9k msg/s |
| 组广播 S4（4 个接收者）        | — | 8.8k msg/s | 661 msg/s |

- 跨进程发送吞吐约为 `channels_redis` 的 **62×**
- 组广播吞吐约为 `channels_redis` 的 **13×**
- 单进程往返延迟仅为纯内存层的 ~1.8× —— 这是"能在进程间共享消息"的代价。

延迟明细（7 次运行中位数）：

| 场景 | 实现 | send p50 / p99 | recv p50 |
|------|------|---------------:|---------:|
| S2 跨进程 | channels-shm | 7.4 µs / 36 µs | 2.2 ms |
| S2 跨进程 | channels_redis | 484 µs / 880 µs | 24 ms |
| 往返（单进程） | InMemory | 8.3 µs / 31 µs | — |
| 往返（单进程） | channels-shm | 14.4 µs / 56 µs | — |

> 该测试方法下 `recv` 延迟包含排队时间：发送方无背压地连发 `count` 条
> 消息，接收方需要消化积压。send 侧指标是干净的对比；完整逐次运行数据
> 已提交在 `bench/docker/results/`。

### 复现

```bash
cd bench/docker
docker compose build
docker compose run --rm bench        # 输出完整 JSON 汇总
```

## 示例应用

[`examples/chat`](examples/chat/) 是一个多进程 Django + Channels 聊天室，
**零基础设施** —— 无需 Redis、无需数据库。它同时是发布前验收项目：在该目录
`uv sync` 会通过 maturin 从工作树真实构建 channels-shm，`manage.py
demo_broadcast` 以无头方式断言跨进程消息分发。

```bash
cd examples/chat
uv sync
uv run python manage.py run_workers      # 在连续端口启动 N 个 daphne worker
uv run python manage.py demo_broadcast   # 无头验收：必须输出 PASSED
```

在浏览器打开两个 worker 端口：每条聊天记录都标注了处理它的 worker PID ——
消息经 `/dev/shm` 跨进程流转。

## 测试

```bash
# 快速单元 / 属性 / 并发套件（无需 docker）
uv run pytest -m "not slow and not e2e"

# 跨进程集成（Linux，multiprocessing）
uv run pytest -m slow

# Django/channels 全栈 e2e —— docker compose 起 3 个 ASGI worker
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

CI（`.github/workflows/ci.yml`）在 Python 3.11–3.13 上运行上述全部检查，
外加 Docker e2e 与 maturin wheel 构建。

## License

BSD-3-Clause。见 [LICENSE](LICENSE)。
