//! O3: Rust-side metrics counters index definitions.
//! Only used when `#[cfg(feature = "metrics")]`; counter region always exists in shm layout.

/// Metrics counter indices (correspond to AtomicU64 slots in shm metrics region).
/// New counters should be appended here; index must be < layout::METRICS_COUNTER_COUNT.
#[repr(u32)]
#[allow(dead_code)] // entire enum guarded by #[cfg(feature = "metrics")]; dead without the feature
#[cfg(feature = "metrics")]
pub enum CounterId {
    /// ring enqueue success count
    RingEnqueueTotal = 0,
    /// ring dequeue success count
    RingDequeueTotal = 1,
    /// ring ChannelFull count
    RingFullTotal = 2,
    /// slab alloc count (hot path)
    SlabAllocTotal = 3,
    /// slab free count (hot path)
    SlabFreeTotal = 4,
    /// seqlock read success count
    SeqlockReadSuccessTotal = 5,
    /// seqlock odd retry count
    SeqlockOddRetryTotal = 6,
    /// eventfd_write count (intra-process wakeup)
    EventfdWriteTotal = 7,
    /// sendto success count (cross-process wakeup)
    SendtoSuccessTotal = 8,
    /// drain_rings call count
    DrainRingsTotal = 9,
    /// expired message drop count
    ExpiredMessagesDroppedTotal = 10,
    /// recover triggered (enqueue direction)
    RecoverTriggeredTotalEnqueue = 11,
    /// recover triggered (dequeue direction)
    RecoverTriggeredTotalDequeue = 12,
    /// recover overflow freed count
    RecoverOverflowFreedTotal = 13,
    /// recover seq reset count
    RecoverSeqResetTotal = 14,
    /// compact slot reset count
    CompactSlotResetTotal = 15,
    /// compact overflow freed count
    CompactOverflowFreedTotal = 16,
    /// compact marked count
    CompactMarkedTotal = 17,
    /// watchdog pump stuck detection count
    WatchdogPumpStuckTotal = 18,
    /// watchdog emergency drain count
    WatchdogEmergencyDrainTotal = 19,
    /// sendto dead errno count
    SendtoDeadErrnoTotal = 20,
    /// sendto transient errno count
    SendtoTransientErrnoTotal = 21,
    /// registry marked dead count
    RegistryMarkedDeadTotal = 22,
    /// expired messages dropped from dequeue
    ExpiredMessagesDroppedFromDequeueTotal = 23,
    /// pid_dead ESRCH branch
    PidDeadKillEsrchTotal = 24,
    /// pid_dead starttime mismatch
    PidDeadStarttimeMismatchTotal = 25,
    /// pid_dead stat read error
    PidDeadStatErrorTotal = 26,
}
