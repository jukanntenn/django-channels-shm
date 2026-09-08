# Concepts

This page explains the model the layer implements. The mechanics live in the code and in the design records (`.agents/dcs-rfcs/`); here is the part a user of the API needs.

## One region, many processes

All workers sharing a `prefix` attach to one `mmap(MAP_SHARED)` region in `/dev/shm` plus a per-process wakeup socket directory `/dev/shm/{prefix}_wakeup/`. Sending writes into the shared ring and nudges the receiver's socket; receiving sleeps on that socket until a message arrives. There is no broker process and no TCP.

## Channels

A channel is a named, bounded queue inside the region. Messages are small dicts (any JSON-serializable-ish structure channels allows), packed with msgpack.

- A `send` appends to the tail of the channel's ring; a `receive` from another process (or thread) pops the head. Senders and receivers are decoupled — this is a queue, not a request/response pipe.
- Rings are bounded by `capacity` (messages) or `channel_capacity` overrides. A `send` that finds no room retries briefly, then raises `ChannelFull`.
- Messages carry an `expiry`; a receiver never sees an expired message.

## Process-specific channels

A channel name that ends in `!` is **process-specific**: it routes to the process that created it, whatever hostname fragment the name carries. Channels' `new_channel()` returns such names (`channel-name!suffix`), and `non_local_name()` is the primitive behind it. This is what makes `reply_channel`-style patterns work across processes: the sender does not need to know which process owns the receiver.

## Groups

A group is a named fan-out list of channels. `group_add`/`group_discard` maintain membership with its own `group_expiry`; `group_send` copies the message into each member's ring. Fan-out is bounded by the *member's* capacity, not the sender's: one slow or full receiver does not block the other members.

## Capacity, size, and memory layout

The region is a fixed `shm_size` arena, carved into:

- per-slot inline storage for messages up to `inline_size` — no allocation and no serialization hop on the send path;
- an index with room for `max_channels` channels and `max_groups` groups;
- a process registry of `max_processes` entries.

Region-level parameters (`shm_size`, the `max_*` indexes, `prefix`) describe one shared region — give every worker the same values. Per-channel `capacity`/`channel_capacity` and `expiry` are per-process policy and may differ.

## Failure and recovery

Everything in the region is owned: every slot and ring tracks its owner (`pid` + process start time). When a process dies, the watchdog sweep (every `watchdog_interval`) detects the dead owner and reclaims its rings and slots, so a worker that dies mid-message never blocks the others, and its process-specific channels are cleaned up.

`DeadProcessError` surfaces on the send side when the wakeup socket belongs to a process that is already gone — the caller learns immediately instead of queueing into a void.

## Observability

Debug builds (`python` without `-O`) can emit structured logs and metrics to `obs_dir`. Release mode (`python -O`, the deployment default) compiles observability out entirely — zero overhead outside debugging.

Next: [Configuration](configuration.md) or the [API reference](reference/layer.md).
