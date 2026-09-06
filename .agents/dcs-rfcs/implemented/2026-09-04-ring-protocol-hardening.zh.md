# DCS-RFC: Ring 协议加固 —— CAS-claim 准入、幂等修复、看门狗压实

Status: implemented

[English](2026-09-04-ring-protocol-hardening.md) | 中文

## Problem

对 [Vyukov 环](./2026-08-02-lock-free-ring-slab-over-broker.zh.md) 的外部评审发现一族正确性缺陷，多数源于同一个设计选择：入队在检查槽位之前用 `fetch_add` 取票，因此每次 `Full` 返回都消耗一张票而不落任何 seq 账。烧掉的票留下一个 seq 永远追不上票流的槽；消费者停在它上面（队头阻塞），一次 `ChannelFull` —— 被 layer 的 send 重试放大 4 倍 —— 就能永久杀死一个物理上已空的环。周边缺陷：`pid_dead(u32::MAX)` 返回 true（`kill(-1,0)` 广播"成功"；`/proc/4294967295` 不存在 → 保守判死），于是停在 `SLOT_RECOVERING` 修复态的槽被再次"修复"，recover 的 CAS 互斥退化为 `RECOVERING→RECOVERING` 的空转，放进第二个修复者 —— 确定性的溢出页 double-free。compact 的 `≥2×cap` 推进度闸门恰恰由它要修的烧票喂养，而 `max(enq,deq)−cap` 界一旦 enq 被烧票抬高就能命中未消费的活消息。`seq>pos` 入队分支无界自旋（整进程冻结：pyo3 持 GIL）。Python 校验器允许 200 字节名字而 in-shm 字段只有 128 字节（环内静默截断导致消息误路由；`channel_index_create` 越界写穿字段进邻槽）。`capacity=0` 使环 panic（`pos % 0`），而 `capacity or default` 又把它静默换成默认值。修复过程中还浮出一个缺陷：capacity=1 从来就不可表示 —— "已发布未消费"与"已回收空位"都等于 `pos+1`，第二次入队会在没有任何 `Full` 信号的情况下静默覆写活消息。

## Decision

入队准入改为先验证后认领（Vyukov CAS-claim）：只有目标槽被验证为 EMPTY（`seq == pos`）后才消耗票；每次 `Full` 返回都发生在认领之前，因此 `enqueue_pos` 在失败时绝不前进，排空后的环用同一张票接受重试的 send。owner 记录移到认领之后 —— 记录现在总是描述本生产者拥有的票。`seq>pos` 分支（仅当 compact 修复与在途生产者竞争时可达）有界自旋 —— 64 次 yield 后返回 `Full`。slab 耗尽留下 SKIP 墓碑（`seq = pos + cap`）而不是死槽；消费者现有的跳过分支零新代码即可消化它。

修复机制整体幂等。所有路径（消费者读取、过期、recover、compact）的溢出页释放改为先声明后释放：对 `SLOT_OVERFLOW_OFF` 做 CAS 赢得 `slab.free` 的权利，声明后崩溃泄漏一页（安全），而旧的先释放后清零顺序会 double-free（free-list 损坏）。`recover_slot` 的 seq 写入是从观测值出发的 CAS —— 永不会回拨已被并发修复或生产者推进的槽 —— owner 只从 `RECOVERING` 清除。`pid_dead` 拒绝 i32 视图 ≤ 0 的 pid，哨兵从此不可能被"确认死亡"；入队与出队把停在 `RECOVERING` 的槽当作忙（`Full`/`None`）。

compact 去掉了 `≥2×cap` 推进度闸门：烧票曾经喂养它，停滞的环不产生任何推进，闸门恰好饿死最需要修复的环。两遍确认改由调用节奏分隔。`SLOT_RECOVERING` 视为无主 —— compact 接管修复者崩溃留下的修复，落点是被中断轮次自己的票（`ticket+cap`）；无主的 E1 残余修复到下一个瞄准该槽的票（已修复并有回归测试的 p 公式）。pump 看门狗每 tick 在全局 flock 下运行原生 `compact`，E1/D1 残余与崩溃的修复在约两个 tick 内自愈。

配置边界快速失败：capacity < 2 被 layer（`ConfigurationError`）、manager 与环绑定（`ValueError`）拒绝 —— 0 除零， 1 不可表示。名字由校验器限到 128，并在每个把名字拷入固定 128 字节字段的绑定处（channel/group 索引创建、成员添加、环入队）以 `ValueError` 显式拒绝。

## Alternatives considered

**计数器准入、取票后等待（一位评审提出的完整协议）。** 通过计数器准入并取票的生产者此后必须发布或落墓碑 —— 返回 `Full` 即烧票 —— 因此它要跨进程等待一个正在拷出的活消费者，而 pyo3 持着 GIL；SIGSTOP、cgroup 冻结或普通的调度饥饿都会把等待变成调度可达路径上的整进程冻结（与无界自旋同一失效模式、更宽的触发面）。它还需要消费者侧的 owner 写序改动，用"claim 前崩溃丢一条"换"claim 前崩溃不丢"。败给 CAS-claim：后者根本没有取票后的等待；将满时的回收窗口返回一个瞬态、正确的 `Full`，由 Python 层带 `await` 的调度重试。

**只把 compact 接上看门狗、不动协议（最小方案）。** 它被自己的证据击败：烧票既抬高 compact 的界（按计划删除活消息）又喂养它的闸门；只接线会让周期性 compact 变得危险，而去掉烧票之后闸门将永远饿死冻结的环。

**一行守卫把 `RECOVERING` 当作活 owner。** 败在：修复者中途崩溃时槽被身份已抹除的哨兵停住，所有观测者永远等它（compact 跳过 owner ≠ 0 的槽）；幂等修复体加 compact 接管才是让守卫安全的前提。

**保留 capacity=1 并做容量特化的别名修补。** 败在：单槽环的任何未来 seq 值都是某个可达位置上消费者的 READY 标记 —— 消费者的跳过追赶必然落在幻影读上；编码在协议内不可修，拒绝才是诚实的修法。

## Consequences

`ChannelFull` 重新变为瞬态：排空后的环用同一张票接受下一次 send，FIFO 与 at-most-once 在全部测试层（快速、slow 跨进程、恢复层）下成立，活消息再也满足不了 compact 的修复条件（诚实的计数器保持 `enq − deq ≤ cap`）。E1 崩溃窗口（认领 → owner 写入，约 3 条指令）现在冻结它的环 —— 环绕点上入队 `Full`、接收为空 —— 直到看门狗 compaction 在约两个 tick 内修复；"冻结—自愈"而非 cap−1 退化，是测试所编码的文档化预期。溢出页声明与释放之间的崩溃可能泄漏一页 slab（有界、安全）—— 永不 double-free。此前静默损坏的配置现在大声失败：capacity 0/1 在构造时抛出，129–200 字节名字在校验与绑定处抛出 `TypeError`/`ValueError`。热路径把 `fetch_add` 换成 load+CAS（RMW 次数相同、多一次 load）。对照修复前代码的实测（criterion，本机）：无竞争入队 36.8 → 40.6 ns、出队 78.6 → 76.8 ns（含[flush 代数栅栏](./2026-09-04-flush-generation-fence.zh.md)）——均远在 100 ns 锚点门内；竞争套件未见超出运行间噪声的回退；`bench/py` 全部锚点通过。既有套件原样通过，仅两处机械适配 —— 用例编码了被移除的机制本身：compact p 公式回归测试改为直接注入残余态而非用烧票制造，recover-CAS 群撞测试从 cap=1（现已不可表示）移到 cap=2；recover 测试断言未动，compact 测试的烧票位置断言则改写为不烧票不变式（意图不变——p 公式与防挂起）。二十个 Rust 与三十四个 Python 回归测试钉住新不变式；覆盖率 Rust 98.99%（`cargo llvm-cov`，按其文档化的插桩限制排除 pyo3 宏文件）、Python 97%。
