# django-channels-shm

**django-channels-shm** is a high-performance **shared-memory channel layer for [Django Channels](https://channels.readthedocs.io/)**, built for single-machine multi-process deployments.

Messages travel between ASGI workers through an `mmap(MAP_SHARED)` region in `/dev/shm` — no Redis, no TCP, no broker. The hot path (lock-free bounded MPMC rings, channel/group indexes, process registry) runs in a **Rust native extension (PyO3)**.

```
ASGI worker A ──send──► ┌───────────────────────────────┐ ──receive──► ASGI worker B
                        │  /dev/shm (MAP_SHARED)        │
                        │  lock-free MPMC rings + slab  │
                        │  channel/group indexes        │
                        │  eventfd / AF_UNIX wakeup     │
                        └───────────────────────────────┘
```

What you get:

- **Zero-copy shared memory** — channels and groups live in one shared region; small messages are written directly into ring slots with no allocation and no serialization hop.
- **Lock-free hot path** — Vyukov bounded MPMC rings in Rust with per-slot sequence numbers; `eventfd` / `AF_UNIX` wakeups instead of polling.
- **Crash recovery** — every slot tracks its owner (`pid` + process start time); dead workers are detected and their slots are reclaimed without blocking anyone else.
- **The complete channels API** — `send`, `receive`, `new_channel`, `group_add`, `group_discard`, `group_send`, `flush`, per-channel capacities and message expiry.
- **One process tree, no server** — the region and wakeup sockets are created lazily; every worker that shares a `prefix` talks to the same region.

Read on:

- [Installation](install.md) — requirements and install options
- [Quickstart](quickstart.md) — configure `CHANNEL_LAYERS` and send your first message
- [Concepts](concepts.md) — how channels, groups, and recovery work
- [Configuration](configuration.md) — every setting and what it trades off
- [API reference](reference/layer.md) — `SharedMemoryChannelLayer` and friends
