# DCS-RFC: A tiered test architecture — fast default, real environments behind markers

Status: implemented

[English](2026-08-18-tiered-test-architecture.md) | 中文

## Problem

测试套件长成了一团：812 行的 `tests/test_layer.py` 把单元、属性与集成用例并排塞在一起，故障注入场景（worker 在入队中途被 SIGKILL、PID 复用、watchdog 泵卡死）无处安家，Docker 全栈 e2e 与亚秒级单元测试同处一个目录。单一扁平套件逼出一个假选择：要么每次编辑都付最慢测试的延迟，要么真环境测试被跳过然后腐烂。重 mock 的快套件躲开了延迟，但测的不再是交付物 —— 共享内存区域的跨进程语义。

## Decision

测试按环境保真度分层，用 pytest 标记门控，`tests/` 镜像 `src/channels_shm/`：默认运行（`uv run pytest -m "not slow and not e2e"`）覆盖针对 `/dev/shm` 真实区域的单元、Hypothesis 属性与状态机测试；`@slow` 的 `tests/cross_process/` 跑真实 `multiprocessing` 互操作；`@slow` 的 `tests/recovery/` 是故障注入层 —— fork/SIGKILL 夹具（`_workers.py`、`conftest.py`）驱动 owner 崩溃、槽位回收、PID 复用、seqlock 陈旧与 watchdog 场景；`@e2e` 的 `tests/e2e/` 在 docker compose 的三个 ASGI worker 下演练真实 Django/channels 栈。快层保持为 pre-push 与 CI 默认测试闸门（镜像 CI 的 test job）；slow 与 e2e 按设计只在 CI 跑。本地与 CI 尽可能跑同一套件 —— 不给被测对象用内存替身，mock 只出现在请求边界。

## Alternatives considered

**一个扁平套件，每次全跑。** 它输在：恢复层与 e2e 层每用例耗时数秒到数分钟；把它们定为每次单元测试循环的代价，就注定了它们在本地被跳过 —— 那正是这次重组要防止的腐烂。

**为了速度 mock 掉区域。** 它输在：本层的契约就是跨进程共享内存语义 —— 所有权、回收、唤醒 —— 而 mock 替换的恰是受测对象；快层已经够快，因为真实区域本就是一次内存映射，而不是因为伪造了什么。

**继续养大 `test_layer.py`。** 它输在：一个没有内部边界的 812 行模块让人看不出哪层行为被哪类测试覆盖，属性/状态机/并发的拆分也无从评审；重组给每类测试自己的文件、每层自己的目录。

## Consequences

反馈延迟与保真度相配：亚秒级默认运行，CI 必跑的可选 slow/e2e 层。标记拆分即契约 —— `pyproject.toml` 注册 `slow`/`e2e`，pre-push 跑快速子集，AGENTS 命令表承载三种调用。重组本身（116 个文件，+8424/−6029）在套件全绿中落地，恢复层现在拥有 [ring + slab 决策](./2026-08-02-lock-free-ring-slab-over-broker.zh.md)所作崩溃安全主张的验证权。
