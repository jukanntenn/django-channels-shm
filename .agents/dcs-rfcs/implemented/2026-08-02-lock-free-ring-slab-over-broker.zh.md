# DCS-RFC: A lock-free ring + slab over shared memory, not a broker

Status: implemented

[English](2026-08-02-lock-free-ring-slab-over-broker.md) | 中文

## Problem

单机上的 Django Channels 部署每条消息都要跨进程传递，而生态默认项 —— `channels_redis` —— 把这些流量路由经过网络 broker：一个需要部署与监控的服务进程、TCP 往返、以及一次序列化中转，服务的却本就是同主机 IPC。`InMemoryChannelLayer` 则完全跨不了进程边界。信道层的热路径是同一台机器上 ASGI worker 之间的 `send`/`receive`；broker 恰好对这条路径征税，却买不来单机部署需要的任何东西。

## Decision

消息经 `/dev/shm` 中的一个 `mmap(MAP_SHARED)` 区域在 worker 之间传递 —— 无 Redis、无 TCP、无 broker、无需启动服务。区域内含每个 channel 一个 Vyukov 有界 MPMC ring（逐槽序号、无锁 `send`/`receive`）加上承载超过 `inline_size` 消息的 slab，配 channel/group 索引与 `eventfd`（进程内）/ `AF_UNIX` 数据报（跨进程）唤醒 —— socket 只传唤醒字节，从不传消息。热路径是 Python 异步 API 之下的 Rust 原生扩展（`pyo3`、abi3），实现 channels 的 `ChannelLayer` 契约；区域与 socket 惰性创建。崩溃安全在区域内闭环：每个槽位记录 owner（`pid` + 进程启动时间），watchdog 检测已死 owner 并回收其 ring 与槽位而不阻塞存活 worker。付出的代价是范围：该层仅限 Linux（`MAP_SHARED` + `AF_UNIX`），且构造上只支持单机。

## Alternatives considered

**在 Redis 上叠层 —— 社区默认。** 它输在：broker 是第二个生产服务、有它自己的失效模式，而其发送路径（TCP + 序列化 + 服务端排队）给根本不离开主机的流量设下了比共享内存高出数量级的延迟下限；固定容器规格的实测数字显示跨进程发送吞吐约为 `channels_redis` 的 62×、组广播约 13×（[README](../../../README.zh.md)）。

**AF_UNIX socket 作为消息通路。** 它输在：数据报 socket 每条消息在两个端点之间搬字节 —— 每次广播变成 N 份拷贝加 N 次系统调用，容量控制与过期无论如何都需要某个排队结构，唤醒问题依然存在；这里 socket 只承载唤醒信号，消息本身躺在每个进程可见的多消费者结构里。

**POSIX 消息队列。** 它输在：热路径上是逐队列的 `mq_*` 系统调用、每个进程要管理描述符生命周期、且没有承载组广播或跨进程容量策略的天然位置 —— 内核队列用内核调用替换了 broker，却没去掉逐消息一跳。

## Consequences

该层以零基础设施的库形态交付：同机 worker 共享一个惰性创建的区域，worker 死在消息中间成为 watchdog 拥有的恢复事件，成本落在设计花销之处 —— 崩溃恢复机制（所有权跟踪、回收、seqlock 陈旧性检查）由恢复测试层演练，ABI 稳定布局（`layout.rs`，动前先问）经 `py_bindings.rs` + `_native.pyi` 把 Rust 绑到 Python，以及仅限 Linux 的范围。由跨进程、恢复与 Docker e2e 层验证；已接受的吞吐/延迟数字见 [README 基准](../../../README.zh.md)。
