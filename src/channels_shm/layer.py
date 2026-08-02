"""SharedMemoryChannelLayer — the main channel layer implementation."""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import random
import socket as _socket
import threading
import time
import uuid
from typing import TYPE_CHECKING, ClassVar, cast

from channels.layers import BaseChannelLayer
from typing_extensions import override

if TYPE_CHECKING:
    import re
    from typing import Protocol

    from channels_shm._obs.metrics import MetricsRegistry

    class _ObsLog(Protocol):
        """Minimal structlog-style logger surface used by this layer.

        Avoids depending on structlog type stubs (none ship with the package);
        the real object is a structlog BoundLogger, which satisfies this.
        """

        def info(self, event: str, **fields: object) -> None: ...
        def warning(self, event: str, **fields: object) -> None: ...


from channels_shm._native import SlabAllocator as NativeSlab
from channels_shm._native import (
    check_magic,
    compute_offsets,
    read_self_starttime,
    registry_get_valid,
    registry_lookup_socket,
    registry_mark_dead,
    registry_register,
    shm_init,
    validate_config,
)
from channels_shm._native import (
    compact as native_compact,
)
from channels_shm._native import (
    flush as native_flush,
)
from channels_shm.channel.manager import (
    ChannelManager,
    client_prefix_of,
    is_process_specific,
    non_local_name,
)
from channels_shm.channel.validator import validate_channel_name, validate_group_name
from channels_shm.exceptions import (
    ChannelFull,
    ConfigurationError,
    DeadProcessError,
    MessageTooLarge,
)
from channels_shm.group.manager import GroupManager
from channels_shm.pump import ReceivePump
from channels_shm.serializer import Message, pack_message
from channels_shm.shm.lock import FlushLock
from channels_shm.shm.region import ShmRegionHandle
from channels_shm.shm.wakeup import WakeupManager

logger = logging.getLogger(__name__)

# Maximum message size (1MB)
MAX_MESSAGE_SIZE = 1024 * 1024

# Send spin bound for ChannelFull retry
SEND_SPIN_BOUND = 3
# Emergency-drain backoff (§6.4 step 11). Base delay grows linearly per attempt;
# a small random jitter is added to avoid synchronized retry storms (L-28).
SEND_RETRY_BASE_DELAY = 0.01
SEND_RETRY_JITTER = 0.002

# AF_UNIX socket path sizing (§13.1 V4.1): usable path ≤ 107 bytes
# (sun_path[108] with NUL terminator). Path = "{prefix}_wakeup/{client_prefix}.sock"
# = len(prefix) + len("_wakeup/") + 32 (uuid4 hex) + len(".sock")
# = len(prefix) + 8 + 32 + 5 = len(prefix) + 45  →  len(prefix) ≤ 62.
AF_UNIX_PATH_MAX = 107
CHANNEL_SUFFIX_MAX = 45


class SharedMemoryChannelLayer(BaseChannelLayer):
    """A channel layer backed by shared memory for single-machine multi-process use.

    Uses lock-free Vyukov MPMC ring buffers for hot-path operations
    and fcntl.flock for cold-path structural changes.
    """

    extensions: ClassVar[list[str]] = ["groups", "flush"]

    # Instance attributes (typed for basedpyright strictness).
    expiry: int
    group_expiry: int
    capacity: int
    prefix: str
    shm_size: int
    inline_size: int
    max_channels: int
    max_groups: int
    max_processes: int
    max_members_per_group: int
    watchdog_interval: int | None
    client_prefix: str
    pid: int
    start_time: int
    _shm_path: str
    _wakeup_dir: str
    _channel_capacity_overrides: dict[str, int]
    channel_capacity: list[tuple[re.Pattern[str], int]]
    _region: ShmRegionHandle | None
    _slab: NativeSlab | None
    _lock: FlushLock | None
    _wakeup: WakeupManager | None
    _channel_mgr: ChannelManager | None
    _group_mgr: GroupManager | None
    _pump: ReceivePump | None
    _loop: asyncio.AbstractEventLoop | None
    _loop_thread: int | None  # thread id that first bound the pump (E-03)
    _closed: bool  # close() idempotency guard (E-04)
    _group_slot_cache: dict[str, int]  # group_name -> grp_slot_off (I-01)
    # O3/O4: observability config (release: unused, python -O eliminates usage)
    _obs_log_max_bytes: int
    _obs_log_backup_count: int
    _obs_dir: str | None
    _obs_log: _ObsLog | None  # structlog BoundLogger; only set under __debug__
    _obs_metrics: MetricsRegistry | None  # only set under __debug__

    def __init__(
        self,
        *,
        expiry: int = 60,
        group_expiry: int = 86400,
        capacity: int = 100,
        channel_capacity: dict[str, int] | None = None,
        prefix: str = "channels_shm",
        shm_size: int = 256 * 1024 * 1024,
        inline_size: int = 512,
        max_channels: int = 10000,
        max_groups: int = 1000,
        max_processes: int = 4096,
        max_members_per_group: int = 1024,
        watchdog_interval: int | None = 30,
        obs_dir: str | None = None,
        log_max_bytes: int = 10 * 1024 * 1024,
        log_backup_count: int = 5,
    ) -> None:
        # BaseChannelLayer.__init__ is typed for the channels_redis/InMemory
        # surface and trips basedpyright's call-arg check here; the call is
        # valid at runtime (it only stores expiry/capacity).
        super().__init__(expiry=expiry, capacity=capacity)  # type: ignore[call-arg]

        # Validate prefix length (§13.1 V4.1): len(prefix)+45 ≤ 107 → len(prefix) ≤ 62
        max_prefix_len = AF_UNIX_PATH_MAX - CHANNEL_SUFFIX_MAX
        if len(prefix) > max_prefix_len:
            msg = (
                f"prefix too long: len({prefix!r})={len(prefix)} > {max_prefix_len} "
                f"(AF_UNIX socket path would exceed {AF_UNIX_PATH_MAX}-byte limit)"
            )
            raise ConfigurationError(msg)

        self.expiry = expiry
        self.group_expiry = group_expiry
        self.capacity = capacity
        self.prefix = prefix
        self.shm_size = shm_size
        self.inline_size = inline_size
        self.max_channels = max_channels
        self.max_groups = max_groups
        self.max_processes = max_processes
        self.max_members_per_group = max_members_per_group
        self.watchdog_interval = watchdog_interval

        # O3/O4: observability configuration (only used under if __debug__:)
        self._obs_dir = obs_dir
        self._obs_log_max_bytes = log_max_bytes
        self._obs_log_backup_count = log_backup_count

        # Compile channel capacities (§5.7)
        self._channel_capacity_overrides = channel_capacity or {}
        self.channel_capacity = self.compile_capacities(  # pyright: ignore[reportIncompatibleVariableOverride]
            self._channel_capacity_overrides  # pyright: ignore[reportArgumentType]
        )

        # Process identity
        self.client_prefix = uuid.uuid4().hex
        self.pid = os.getpid()
        self.start_time = read_self_starttime()

        # Paths
        self._shm_path = f"/dev/shm/{prefix}"
        self._wakeup_dir = f"/dev/shm/{prefix}_wakeup"

        # Will be set during _initialize
        self._region = None
        self._slab = None
        self._lock = None
        self._wakeup = None
        self._channel_mgr = None
        self._group_mgr = None
        self._pump = None
        self._loop = None
        self._loop_thread = None
        self._closed = False
        self._group_slot_cache = {}

        # Initialize
        self._initialize()

    def _initialize(self) -> None:
        """Initialize shared memory, create wakeup mechanism, start pump.

        Wrapped in try/except (E-07): if init fails partway, release the
        resources already created (region/wakeup/pump) before re-raising, so a
        failed __init__ does not leak fds/mmaps/sockets.
        """
        # O3: configure observability ONLY under __debug__ (release python -O eliminates this block)
        if __debug__:
            from channels_shm._obs import ObservabilityConfig, configure_logging
            from channels_shm._obs.metrics import MetricsRegistry

            obs_config = ObservabilityConfig(
                self.prefix,
                obs_dir=self._obs_dir,
                log_max_bytes=self._obs_log_max_bytes,
                log_backup_count=self._obs_log_backup_count,
            )
            self._obs_log = configure_logging(obs_config, self.pid)
            self._obs_metrics = MetricsRegistry(
                obs_config.metrics_dir,
                self.pid,
                flush_interval=obs_config.metrics_flush_interval,
            )
            # O-01: start the periodic-flush daemon so a SIGKILL/OOM crash
            # (routine in Django deploys, §10) still leaves metrics behind,
            # covering all but the last flush_interval. Without this, metrics
            # were written only from close() and lost entirely on crash.
            self._obs_metrics.start_periodic_flush()
        # (release build: self._obs_log / self._obs_metrics never set; never accessed under -O)

        try:
            self._do_initialize()
        except BaseException:
            # Clean up anything already created so a failed __init__ leaks nothing.
            if self._pump is not None:
                self._pump.stop()
            if self._wakeup is not None:
                self._wakeup.close()
            if self._region is not None:
                self._region.close()
            raise

    def _do_initialize(self) -> None:
        """Actual initialization (called by _initialize inside the try)."""

        # Compute layout offsets
        _ch_off, _grp_off, _members_off, _reg_off, _metrics_off, pool_off = (
            compute_offsets(
                self.max_channels,
                self.max_groups,
                self.max_members_per_group,
                self.max_processes,
            )
        )
        pool_size = self.shm_size - pool_off

        # Open shm file
        region = ShmRegionHandle(self._shm_path)
        region.open(create=True)
        self._region = region
        lock = FlushLock(region.fd)
        self._lock = lock

        # First-process initialization protocol (§10.4).
        # E-04 (L-04): detect under flock, but rebuild OUTSIDE the `with lock:`
        # block. The `with` binds the OLD lock (old fd); closing the region
        # inside it would make __exit__ call flock() on a closed fd (EBADF).
        # Detecting the decision under the lock keeps the config read consistent;
        # rebuilding after __exit__ releases the old fd's flock cleanly.
        need_rebuild = False
        need_init = False
        with lock:
            if region.size > 0 and check_magic(region.native):
                if not validate_config(
                    region.native,
                    self.inline_size,
                    self.capacity,
                    self.max_channels,
                    self.max_groups,
                    self.max_members_per_group,
                    self.max_processes,
                ):
                    need_rebuild = True
            else:
                # shm has data but no valid magic (corrupted/partial) — initialize
                need_init = True

        if need_rebuild:
            logger.warning("Config mismatch, reinitializing shm")
            region.close()
            region.unlink()
            region = ShmRegionHandle(self._shm_path)
            region.open(create=True)
            self._region = region
            lock = FlushLock(region.fd)
            self._lock = lock
            self._init_shm(region, lock, pool_off, pool_size)
        elif need_init:
            self._init_shm(region, lock, pool_off, pool_size)

        # Create slab allocator
        slab = NativeSlab(pool_off, pool_size)
        self._slab = slab

        # Create managers
        self._channel_mgr = ChannelManager(
            region.native,
            slab,
            self.inline_size,
            self.capacity,
            self.max_channels,
        )
        self._group_mgr = GroupManager(
            region.native,
            slab,
            self.max_groups,
            self.max_members_per_group,
            self.group_expiry,
        )

        # Create wakeup mechanism
        os.makedirs(self._wakeup_dir, exist_ok=True)
        wakeup = WakeupManager(
            self.client_prefix,
            self._wakeup_dir,
            metrics=self._obs_metrics if __debug__ else None,
        )
        wakeup.create()
        self._wakeup = wakeup

        # Orphan socket cleanup (§10.1.1 mechanism 2)
        self._cleanup_orphan_sockets()

        # Register in Wakeup Registry
        with lock:
            slot_off = registry_register(
                region.native,
                self.client_prefix,
                wakeup.socket_path,
                self.pid,
                self.start_time,
                self.max_processes,
            )
            if slot_off == 0:
                logger.warning(
                    "Wakeup Registry full (max_processes=%d), cross-process wakeup may not work",
                    self.max_processes,
                )

        # Create and start pump
        self._pump = ReceivePump(
            region=region.native,
            slab=slab,
            channel_mgr=self._channel_mgr,
            wakeup=wakeup,
            capacity=self.capacity,
            expiry=self.expiry,
            pid=self.pid,
            start_time=self.start_time,
            watchdog_interval=self.watchdog_interval,
            metrics=self._obs_metrics if __debug__ else None,
            log=self._obs_log if __debug__ else None,
        )

    def _init_shm(
        self,
        region: ShmRegionHandle,
        lock: FlushLock,
        pool_off: int,
        pool_size: int,
    ) -> None:
        """First-process shm initialization (must be under flock)."""
        region.ftruncate_and_remap(self.shm_size)
        slab = NativeSlab(pool_off, pool_size)
        with lock:
            shm_init(
                region.native,
                self.shm_size,
                self.inline_size,
                self.capacity,
                self.expiry,
                self.group_expiry,
                self.max_channels,
                self.max_groups,
                self.max_members_per_group,
                self.max_processes,
                slab,
            )

    def _cleanup_orphan_sockets(self) -> None:
        """Scan wakeup directory for orphan socket files (§10.1.1 mechanism 2)."""
        if not os.path.isdir(self._wakeup_dir):
            return
        my_socket = self._wakeup.socket_path if self._wakeup else None

        for entry in os.listdir(self._wakeup_dir):
            if not entry.endswith(".sock"):
                continue
            sock_path = os.path.join(self._wakeup_dir, entry)
            if sock_path == my_socket:
                continue
            # Try sendto probe
            try:
                probe = _socket.socket(
                    _socket.AF_UNIX, _socket.SOCK_DGRAM | _socket.SOCK_NONBLOCK
                )
                try:
                    _ = probe.sendto(b"\x00", sock_path)
                except OSError as e:
                    if e.errno in (errno.ENOENT, errno.ECONNREFUSED, errno.ENOTCONN):
                        try:
                            os.unlink(sock_path)
                        except FileNotFoundError:
                            pass
                finally:
                    probe.close()
            except OSError:
                # Probe-socket creation failure (e.g. EMFILE). Other errors
                # (logic bugs) propagate. (L-14 / J-1: narrowed from Exception.)
                pass

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create the event loop and start the pump.

        Thread model (§6.5.3 / E-03): a layer instance is bound to a single
        event loop, on a single thread. The FIRST caller's thread becomes the
        owner; subsequent calls from a DIFFERENT thread raise RuntimeError
        (rather than silently racing on pump state). Calls from the SAME thread
        on a NEW loop (the single-thread `async_to_sync` pattern) are allowed:
        we stop the pump on the old loop and restart it on the new one.
        """
        loop = asyncio.get_running_loop()
        bound = self._loop
        if bound is loop:
            return loop
        if bound is None:
            # First binding — record the owning thread.
            self._loop = loop
            self._loop_thread = threading.get_ident()
            if self._pump is not None:
                self._pump.start(loop)
            return loop
        # A different loop than the bound one.
        if threading.get_ident() != self._loop_thread:
            msg = (
                "SharedMemoryChannelLayer is bound to a single event loop on a "
                f"single thread (§6.5.3); bound loop {bound!r} != current {loop!r}"
            )
            raise RuntimeError(msg)
        # Same thread, new loop: single-thread async_to_sync — switch.
        if self._pump is not None:
            self._pump.stop()
        self._loop = loop
        if self._pump is not None:
            self._pump.start(loop)
        return loop

    def _check_open(self, method_name: str) -> None:
        """Raise if the layer is not usable for a public method.

        Centralizes the per-method None checks (L-25) and distinguishes the
        failure modes (L-27/X-03): "closed" vs "not initialized (init failed)".
        """
        if self._closed:
            msg = f"Channel layer is closed ({method_name} after close)"
            raise RuntimeError(msg)
        if self._region is None:
            msg = f"Channel layer not initialized ({method_name}); __init__ failed?"
            raise RuntimeError(msg)

    def _resources(
        self,
    ) -> tuple[
        ShmRegionHandle,
        ChannelManager,
        GroupManager,
        NativeSlab,
        FlushLock,
        WakeupManager,
        ReceivePump,
    ]:
        """Return all live resource handles, narrowing their types (non-None).

        Pairs with _check_open: after _check_open raises on a closed/uninitialized
        layer, this gives the type-checker proof that the handles are non-None
        without runtime `assert` (S101). Each attribute is checked again here so a
        race or programming error still raises rather than returning a None handle.
        """
        region = self._region
        channel_mgr = self._channel_mgr
        group_mgr = self._group_mgr
        slab = self._slab
        lock = self._lock
        wakeup = self._wakeup
        pump = self._pump
        if (
            region is None
            or channel_mgr is None
            or group_mgr is None
            or slab is None
            or lock is None
            or wakeup is None
            or pump is None
        ):
            # Should be unreachable after _check_open, but double-check defensively.
            msg = "Channel layer is closed"
            raise RuntimeError(msg)
        return region, channel_mgr, group_mgr, slab, lock, wakeup, pump

    def _obs(self) -> tuple[MetricsRegistry, _ObsLog]:
        """Return the observability (metrics, log) handles as non-None.

        Only ever called inside `if __debug__:` blocks, where _initialize has
        populated both. The cast tells the type-checker what runtime already
        guarantees, avoiding per-call None-narrowing boilerplate.
        """
        return cast("MetricsRegistry", self._obs_metrics), cast(
            "_ObsLog", self._obs_log
        )

    def _wakeup_targets(self, channel: str) -> None:
        """Send wakeup signals to the appropriate targets (§6.4 step 10).

        Process-specific channels (S2) wake ONLY the owning process via a
        targeted unicast (§4.2.1: S2 must be directed). Regular channels
        broadcast to all valid processes.
        """
        wakeup = self._wakeup
        if wakeup is None:
            return

        if is_process_specific(channel):
            # Process-specific: wake ONLY the owning process (targeted unicast).
            owner = client_prefix_of(channel)
            if owner == self.client_prefix:
                wakeup.wakeup_local()
            else:
                self._wakeup_by_prefix(owner)
        else:
            # Regular channel: broadcast to all valid processes.
            self._wakeup_broadcast()

    def _wakeup_by_prefix(self, target_prefix: str) -> None:
        """Targeted unicast: wake the one process owning `target_prefix` (L-03).

        Replaces the previous O(N) broadcast-to-all. Looks up the target's
        socket path directly via the registry's client_prefix field.
        """
        region = self._region
        wakeup = self._wakeup
        if region is None or wakeup is None:
            return
        path_bytes = registry_lookup_socket(
            region.native, target_prefix, self.max_processes
        )
        if path_bytes is None:
            # Owner not registered / dead / slot recycled. The watchdog will
            # drain the message when the owner (re)appears; at-most-once.
            return
        try:
            wakeup.wakeup_remote(path_bytes.decode("utf-8"))
        except DeadProcessError:
            self._registry_mark_dead_by_path(path_bytes.decode("utf-8"))

    def _wakeup_broadcast(self) -> None:
        """Broadcast wakeup to all valid processes (regular channels, §6.4).

        Dead entries are collected and marked in a SINGLE flock (C-02/L-06),
        using the slot_off already returned by registry_get_valid — no second
        scan, no per-dead-entry flock, no TOCTOU window.
        """
        region = self._region
        wakeup = self._wakeup
        lock = self._lock
        if region is None or wakeup is None:
            return
        entries = registry_get_valid(region.native, self.max_processes)
        # Always wake ourselves
        wakeup.wakeup_local()
        dead_slots: list[int] = []
        for slot_off, path_bytes in entries:
            path = path_bytes.decode("utf-8")
            if path == wakeup.socket_path:
                continue  # Skip self
            try:
                wakeup.wakeup_remote(path)
            except DeadProcessError:
                dead_slots.append(slot_off)  # defer; batch under one flock
        if dead_slots and lock is not None:
            with lock:
                for slot_off in dead_slots:
                    registry_mark_dead(region.native, slot_off)

    def _registry_mark_dead_by_path(self, path: str) -> None:
        """Mark a registry entry dead by socket path (targeted-unicast fallback).

        Used only by _wakeup_by_prefix's single-target path. The broadcast path
        uses slot_off directly (no re-scan). Here we still re-scan because the
        single-target lookup returned a path, not a slot; the scan is bounded
        by max_processes and runs at most once per dead target.
        """
        region = self._region
        lock = self._lock
        if region is None or lock is None:
            return
        entries = registry_get_valid(region.native, self.max_processes)
        for slot_off, path_bytes in entries:
            if path_bytes.decode("utf-8") == path:
                with lock:
                    registry_mark_dead(region.native, slot_off)
                break

    # ── Channel Layer API ────────────────────────────────────────

    @override
    async def send(self, channel: str, message: Message) -> None:
        """Send a message to a channel."""
        self._check_open("send")
        region, channel_mgr, _group_mgr, slab, lock, _wakeup, _pump = self._resources()

        # Validate (runtime guard: type system can't enforce at call site).
        # The two pyright ignores below are intentional: this is a runtime
        # guard for callers that pass a non-dict, which the static type
        # signature cannot enforce.
        if not isinstance(message, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = "Message must be a dict"  # pyright: ignore[reportUnreachable]
            raise TypeError(msg)
        validate_channel_name(channel)
        # L-18: this layer routes via slot metadata, NOT a __asgi_channel__ key
        # in the message dict (§6.9 V3.1). Defense against callers that assume
        # channels_redis-style key injection.
        if "__asgi_channel__" in message:
            msg = (
                "message must not contain reserved key '__asgi_channel__'; this "
                "layer routes via slot metadata, not message keys (§6.9)"
            )
            raise ValueError(msg)

        # Serialize — zero-copy memoryview straight into the native ring (§6.1,
        # L-19). No bytes() copy: pyo3 consumes the buffer protocol directly.
        msg_data = pack_message(message)

        # Size check
        if len(msg_data) > MAX_MESSAGE_SIZE:
            raise MessageTooLarge(
                f"Message too large: {len(msg_data)} bytes > {MAX_MESSAGE_SIZE} limit"
            )

        # Get or create ring (cold path for creation)
        ring_key = non_local_name(channel)
        ring = channel_mgr.get_ring(ring_key)
        if ring is None:
            with lock:
                ring, _ = channel_mgr.get_or_create_ring(
                    channel, self.get_capacity(channel)
                )

        # Enqueue
        expiry_ts = time.time() + self.expiry
        ch_bytes = channel.encode("utf-8")
        success = ring.try_enqueue(
            region.native,
            slab,
            ch_bytes,
            msg_data,
            expiry_ts,
            self.pid,
            self.start_time,
        )

        if not success:
            # Channel full — emergency drain + bounded spin retry (§6.4 step 11).
            # H-03: wake the ACTUAL consumer (targeted unicast via
            # _wakeup_targets), not just our own pump — for process-specific
            # channels (S2) the consumer is a remote process whose drain is the
            # only thing that frees ring space. H-04: jitter to avoid
            # synchronized retry storms when many producers hit a full ring.
            for attempt in range(SEND_SPIN_BOUND):
                self._wakeup_targets(channel)
                await asyncio.sleep(
                    SEND_RETRY_BASE_DELAY * (attempt + 1)
                    # Jitter in [0, SEND_RETRY_JITTER) to avoid synchronized
                    # retry storms (L-28).
                    + random.random() * SEND_RETRY_JITTER  # noqa: S311 - jitter, not crypto
                )
                success = ring.try_enqueue(
                    region.native,
                    slab,
                    ch_bytes,
                    msg_data,
                    expiry_ts,
                    self.pid,
                    self.start_time,
                )
                if success:
                    break

            if not success:
                if __debug__:
                    metrics, log = self._obs()
                    metrics.counter("send_full_after_emergency_drain_total").inc(
                        channel=channel
                    )
                    log.warning(
                        "send channel full after emergency drain",
                        channel=channel,
                    )
                raise ChannelFull(f"Channel {channel!r} is full")

        if __debug__:
            metrics, _log = self._obs()
            metrics.counter("send_total").inc(channel=channel)

        # Wakeup (§6.4 step 10)
        self._wakeup_targets(channel)

    @override
    async def receive(self, channel: str) -> Message:
        """Receive a message from a channel (blocks until available)."""
        self._check_open("receive")
        _region, channel_mgr, _group_mgr, _slab, lock, _wakeup, pump = self._resources()

        validate_channel_name(channel)
        # L-02: a process-specific channel (contains '!') must belong to THIS
        # process. Otherwise a different process's pump could CAS-dequeue the
        # message out from under the real owner (Vyukov MPMC allows any
        # consumer). Mirrors channels_redis core.py:260-264.
        if (
            is_process_specific(channel)
            and client_prefix_of(channel) != self.client_prefix
        ):
            msg = f"Cannot receive on a channel owned by another process: {channel!r}"
            raise ValueError(msg)

        # Ensure the pump is running
        _ = self._ensure_loop()

        # Register channel with pump (creates buffer, does initial drain)
        buf = pump.watch_channel(channel)

        # Ensure the ring exists for this channel (first-watch cold path). The
        # returned (ring, is_new) is intentionally discarded: the pump re-looks
        # up the ring itself during drain_rings (L-24).
        ring_key = non_local_name(channel)
        ring = channel_mgr.get_ring(ring_key)
        if ring is None:
            with lock:
                _ = channel_mgr.get_or_create_ring(channel, self.get_capacity(channel))

        # Wait for a message
        return await buf.get()

    @override
    async def new_channel(self, prefix: str = "specific.") -> str:
        """Create a new process-specific channel name.

        The name embeds this process's client_prefix as the owning-process
        marker: "{prefix}.{client_prefix}!{suffix}". L-07: validate prefix —
        it must not contain '!' or '?', or it would corrupt the ownership
        marker (non_local_name slices at the first '!').
        """
        if prefix and ("!" in prefix or "?" in prefix):
            msg = (
                f"prefix must not contain '!' or '?': {prefix!r} (would corrupt "
                "the process-specific ownership marker)"
            )
            raise ValueError(msg)

        suffix = uuid.uuid4().hex
        # Ensure prefix ends with "." for proper formatting
        if prefix and not prefix.endswith("."):
            prefix = f"{prefix}."
        return f"{prefix}{self.client_prefix}!{suffix}"

    # ── Groups extension ─────────────────────────────────────────

    @override
    async def group_add(self, group: str, channel: str) -> None:
        """Add a channel to a group."""
        self._check_open("group_add")
        _region, _channel_mgr, group_mgr, _slab, lock, _wakeup, _pump = (
            self._resources()
        )

        validate_group_name(group)
        validate_channel_name(channel)

        with lock:
            group_mgr.add(group, channel)
        # Speculative cache population for group_send's hot path (I-01): record
        # the slot so the next send skips the O(max_groups) index scan. A miss
        # on lookup falls back to the scan, so a stale entry only costs one
        # lookup; flush clears the cache.
        self._refresh_group_slot_cache(group)

    @override
    async def group_discard(self, group: str, channel: str) -> None:
        """Remove a channel from a group."""
        self._check_open("group_discard")
        _region, _channel_mgr, group_mgr, _slab, lock, _wakeup, _pump = (
            self._resources()
        )

        validate_group_name(group)
        validate_channel_name(channel)

        with lock:
            group_mgr.discard(group, channel)

    @override
    async def group_send(self, group: str, message: Message) -> None:
        """Send a message to all channels in a group.

        Never raises ChannelFull: a member whose ring is full is silently
        skipped (§7.4). MAY raise RuntimeError on cold-path structural failures
        (e.g. channel index full) and TypeError/MessageTooLarge/ValueError on
        invalid input (L-21).
        """
        self._check_open("group_send")
        region, channel_mgr, group_mgr, slab, lock, wakeup, _pump = self._resources()

        # Runtime type guard (pyright: runtime check for non-dict callers).
        if not isinstance(message, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            msg = "Message must be a dict"  # pyright: ignore[reportUnreachable]
            raise TypeError(msg)
        validate_group_name(group)
        if "__asgi_channel__" in message:
            msg = "message must not contain reserved key '__asgi_channel__' (§6.9)"
            raise ValueError(msg)

        # Serialize once — zero-copy memoryview into each member ring (§6.1).
        msg_data = pack_message(message)
        if len(msg_data) > MAX_MESSAGE_SIZE:
            raise MessageTooLarge(
                f"Message too large: {len(msg_data)} bytes > {MAX_MESSAGE_SIZE} limit"
            )

        # I-02: read members with a single bulk Rust call (with Rust-side
        # expiry filter), instead of up to max_members_per_group FFI round
        # trips. I-01: members read is lock-free (seqlock-safe lookup); the
        # group slot offset is taken from the per-process cache when present.
        members = group_mgr.get_members(group)
        if not members:
            return

        expiry_ts = time.time() + self.expiry
        # C-03: pre-compute {owner client_prefix -> socket_path} so each owning
        # process is woken exactly ONCE, regardless of how many of its channels
        # are in the group. Replaces per-member registry scans. Uses real
        # per-process identity (socket_path) for dedup, not magic strings (L-13).
        woken: set[str] = set()
        need_broadcast = False  # set if any non-process-specific member exists

        for channel in members:
            ring_key = non_local_name(channel)
            ring = channel_mgr.get_ring(ring_key)
            if ring is None:
                # Ring doesn't exist yet — create it (cold path)
                with lock:
                    ring, _ = channel_mgr.get_or_create_ring(
                        channel, self.get_capacity(channel)
                    )

            success = ring.try_enqueue(
                region.native,
                slab,
                channel.encode("utf-8"),
                msg_data,
                expiry_ts,
                self.pid,
                self.start_time,
            )
            if not success:
                # Channel full — silently skip (§7.4)
                continue

            if __debug__:
                _metrics, _log = self._obs()
                _metrics.counter("group_send_member_total").inc(group=group)

            # Determine which process to wake. Process-specific members wake
            # their owner (targeted); a single non-process-specific member
            # triggers one broadcast for the whole send.
            if is_process_specific(channel):
                owner = client_prefix_of(channel)
                if owner == self.client_prefix:
                    woken.add("__self__")  # sentinel distinct from any socket path
                # Targeted wake — _wakeup_by_prefix dedups via registry; to
                # avoid repeated lookups for the same owner we track owners.
                elif owner not in woken:
                    self._wakeup_by_prefix(owner)
                    woken.add(owner)
            else:
                need_broadcast = True

        if "__self__" in woken:
            wakeup.wakeup_local()
        if need_broadcast:
            self._wakeup_broadcast()

    def _refresh_group_slot_cache(self, group: str) -> None:
        """Populate the group-slot cache after a structural change (I-01).

        Best-effort: if the lookup doesn't find the group (e.g. discard removed
        the last member and the slot went inactive), the entry is removed so a
        later group_send re-scans. Errors are swallowed — the cache is only an
        optimization; correctness falls back to the index scan.
        """
        group_mgr = self._group_mgr
        if group_mgr is None:
            return
        try:
            found, slot_off, _members_off, _count, _active = group_mgr.lookup_slot(
                group
            )
        except Exception:
            return
        if found:
            self._group_slot_cache[group] = slot_off
        else:
            _ = self._group_slot_cache.pop(group, None)

    # ── Flush extension ──────────────────────────────────────────

    @override
    async def flush(self) -> None:
        """Reset the channel layer to blank state (§9.6).

        Must be called in a quiescent state (no concurrent send/receive).
        Does NOT touch Wakeup Registry (C-flush).
        """
        self._check_open("flush")
        region, _channel_mgr, _group_mgr, slab, lock, _wakeup, _pump = self._resources()

        if __debug__:
            _metrics, log = self._obs()
            log.info("flush invoked")

        # L-01/L-10: the per-slot reset loop is done in ONE Rust call (no
        # hardcoded layout magic numbers in Python, no FFI round trip per slot,
        # shorter flock hold). The caller holds the flock around the call
        # (§9.6: the Rust side assumes the global flock is held).
        with lock:
            native_flush(region.native, slab, self.max_channels, self.max_groups)

        # flush invalidated every group slot offset — drop the cache (I-01).
        self._group_slot_cache.clear()

        # Clear pump state
        if self._pump is not None:
            self._pump.clear()

        # Wake all pumps
        if self._wakeup is not None:
            self._wakeup.wakeup_local()
            self._wakeup_broadcast()

    # ── Lifecycle ────────────────────────────────────────────────

    async def compact(self) -> None:
        """Non-destructive stuck slot repair (§9.7).

        Unlike flush(), this does NOT reset slot fields. Iterates channel index
        slots and runs Ring.compact on each (the Rust side also covers the
        seqlock stale-odd repair that group slots need; B-5).
        """
        self._check_open("compact")
        region, _channel_mgr, _group_mgr, slab, lock, _wakeup, _pump = self._resources()

        with lock:
            native_compact(region.native, slab, self.max_channels, self.start_time)

    async def close(self) -> None:
        """Close the channel layer and release process resources.

        Idempotent (E-04): a second call is a no-op. Exception-safe: state is
        cleared in a `finally` so a mid-close error still drops all references
        (and thus frees the underlying fds/mmaps via the wrappers' close).
        """
        if self._closed:
            return
        self._closed = True
        try:
            if __debug__:
                metrics = self._obs()[0]
                # Stop the periodic-flush daemon first, then do a final flush.
                metrics.stop_periodic_flush()
                # flush() is idempotent (atomic temp-file rename), so even a
                # second close() — had we not short-circuited — would be safe.
                _ = metrics.flush()

            if self._pump is not None:
                self._pump.stop()

            # Mark our registry entry as dead
            region = self._region
            lock = self._lock
            wakeup = self._wakeup
            if region is not None and lock is not None:
                entries = registry_get_valid(region.native, self.max_processes)
                my_path = wakeup.socket_path if wakeup is not None else None
                for slot_off, path_bytes in entries:
                    if path_bytes.decode("utf-8") == my_path:
                        with lock:
                            registry_mark_dead(region.native, slot_off)
                        break

            if wakeup is not None:
                wakeup.close()
            if region is not None:
                region.close()
        finally:
            # Drop all references even if something above raised. Under
            # __debug__, also clear the observability handles (L-11: they were
            # previously left dangling, relying on the implicit contract that
            # flush() is idempotent).
            self._pump = None
            self._wakeup = None
            self._region = None
            self._slab = None
            self._lock = None
            self._channel_mgr = None
            self._group_mgr = None
            self._group_slot_cache.clear()
            self._loop = None
            self._loop_thread = None
            if __debug__:
                self._obs_log = None
                self._obs_metrics = None

    def unlink_shm(self) -> None:
        """Explicitly unlink (delete) the shared memory file."""
        if self._region is not None:
            self._region.unlink()
