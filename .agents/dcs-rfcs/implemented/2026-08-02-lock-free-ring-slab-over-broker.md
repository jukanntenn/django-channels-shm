# DCS-RFC: A lock-free ring + slab over shared memory, not a broker

Status: implemented

English | [中文](2026-08-02-lock-free-ring-slab-over-broker.zh.md)

## Problem

Django Channels deployments on one machine still cross processes for every message, and the ecosystem default — `channels_redis` — routes that traffic through a network broker: a server process to deploy and monitor, TCP round-trips, and a serialization hop for what is, by definition, same-host IPC. `InMemoryChannelLayer` crosses no process boundary at all. A channel layer's hot path is `send`/`receive` between ASGI workers on one box; a broker taxes exactly that path while buying nothing a single-machine deployment needs.

## Decision

Messages travel between workers through one `mmap(MAP_SHARED)` region in `/dev/shm` — no Redis, no TCP, no broker, no server to start. The region holds a Vyukov bounded MPMC ring per channel (per-slot sequence numbers, lock-free `send`/`receive`) plus a slab for messages over `inline_size`, with channel/group indexes and `eventfd` (intra-process) / `AF_UNIX` datagram (cross-process) wakeup — sockets carry only wakeup bytes, never messages. The hot path is a Rust native extension (`pyo3`, abi3) under a Python async API implementing the channels `ChannelLayer` contract; the region and sockets are created lazily. Crash safety is owned in-region: every slot records its owner (`pid` + process start time) and a watchdog detects dead owners and reclaims their rings and slots without blocking live workers. The trade is scope: the layer is Linux-only (`MAP_SHARED` + `AF_UNIX`) and single-machine by construction.

## Alternatives considered

**Layer on Redis — the community default.** It lost: the broker is a second production service with its own failure modes, and its send path (TCP + serialization + server-side queueing) puts a latency floor orders above shared memory for traffic that never leaves the host; published pinned-container numbers show ~62× higher cross-process send throughput and ~13× higher group fan-out than `channels_redis` ([README](../../../README.md)).

**AF_UNIX sockets as the message path.** It lost: datagram sockets move bytes between two endpoints per message — every fan-out becomes N copies and N syscalls, capacity control and expiry need a queueing structure somewhere anyway, and the wakeup problem stays; here sockets carry only the wakeup signal while the message itself sits in a multi-consumer structure every process can see.

**POSIX message queues.** They lost: per-queue `mq_*` syscalls on the hot path, descriptor lifetime to manage per process, and no natural place for group fan-out or cross-process capacity policy — the kernel queue replaces the broker with kernel calls without removing the per-message hop.

## Consequences

The layer ships as a library with zero infrastructure: same-machine workers share one lazily-created region, a worker dying mid-message is a recovery event the watchdog owns, and the cost lands where the design spends it — crash-recovery machinery (ownership tracking, reclamation, seqlock staleness checks) exercised by the recovery test tier, an ABI-stable layout (`layout.rs`, ask before touching) binding Rust to Python through `py_bindings.rs` + `_native.pyi`, and Linux-only scope. Verified by the cross-process, recovery, and Docker e2e tiers; accepted throughput/latency numbers live in the [README benchmarks](../../../README.md).
