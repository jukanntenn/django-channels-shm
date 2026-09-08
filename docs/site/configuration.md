# Configuration

Configuration reaches the layer through the `CONFIG` dict in `CHANNEL_LAYERS` (Django) or as keyword arguments to `SharedMemoryChannelLayer(...)` (direct use). Every option is keyword-only, has a default that suits small deployments, and is documented authoritatively in the [`SharedMemoryChannelLayer.__init__`](reference/layer.md) API reference. This page covers how the options group and when to tune them.

## Region identity

- `prefix` is the one setting that must agree across processes. All workers that must talk to each other share one prefix (default `"channels_shm"`); different prefixes are fully isolated regions that never touch. The prefix also namespaces the wakeup socket directory, so it is limited to 53 characters — the resulting `/dev/shm/{prefix}_wakeup/{client}.sock` path must fit the AF_UNIX limit.

Region-level parameters describe one shared region and should be given the same values in every worker: `prefix`, `shm_size`, and the `max_*` index sizes. Per-channel policy (`capacity`, `channel_capacity`, `expiry`) is per-process and may differ.

## Throughput vs. latency

Tune in this order:

- `capacity` (default `100`) bounds each channel's ring in messages. Raise it for burstier producers; each slot costs region memory.
- `channel_capacity` overrides capacity per channel name with regular expressions — the most specific match wins:
  ```python
  "channel_capacity": {"^video\.": 1000, "^telemetry\.": 10000},
  ```
- `inline_size` (default `512`) is the inline cutoff: messages at most this many bytes are written straight into the ring slot with no allocation and no copy. Raise it to speed up medium messages, at the cost of region space.
- `expiry` (default `60`) is message lifetime in seconds; receivers skip expired messages. Lower values bound stale-queue buildup after a receiver dies.
- `shm_size` (default `256 MiB`) caps the whole region; the region is fixed at first touch, so size it for the peak traffic you expect. The arena covers inline slots, channel/group indexes, and the process registry.

## Scale limits

`max_channels` (default `10000`), `max_groups` (default `1000`), `max_processes` (default `4096`), and `max_members_per_group` (default `1024`) are preallocated index sizes, not caps on churn — index entries recycle as channels and groups expire. Raise a `max_*` when you hit a "too many" error; the price is region memory.

## Membership and cleanup

- `group_expiry` (default `86400`) makes group memberships leases: a member must re-`group_add` before it expires or it is swept. Match it to how long you expect a connection to stay in a room.
- `watchdog_interval` (default `30` seconds, `None` disables) controls how often the sweeper reclaims slots left by dead processes. Lower values reclaim faster after a crash, at the cost of a more frequent sweep.

## Observability (debug builds only)

`obs_dir` enables structured logs and metrics output; `log_max_bytes` and `log_backup_count` bound it. These exist only in debug builds: `python -O` compiles observability out, so a release deployment can leave them set without paying anything.

Next: [API reference](reference/layer.md).
