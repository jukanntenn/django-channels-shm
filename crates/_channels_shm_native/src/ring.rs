// Vyukov bounded MPMC ring buffer implementation.
// Per-slot sequence number + owner tracking for crash recovery.

/// Bound on the `seq > pos` spin in try_enqueue. Only a compact repair racing
/// an in-flight producer can push a slot's seq past the producer's ticket;
/// after this many yields the enqueue returns Full (zero side effects, no
/// ticket held) instead of spinning forever while pyo3 holds the GIL.
const SEQ_AHEAD_SPIN_BOUND: u32 = 64;

use crate::layout;
use crate::region::ShmRegion;
use crate::slab::SlabAllocator;

/// Owner identity for ring slot tracking (§5.4/§10.2.1).
/// Bundles pid + start_time to reduce parameter count and make owner
/// semantics explicit. Internal to Rust — Python still passes pid, start_time.
#[derive(Clone, Copy)]
pub struct OwnerIdentity {
    pub pid: u32,
    pub start_time: u64,
}

/// Result of a ring enqueue operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EnqueueResult {
    /// Message enqueued successfully.
    Ok,
    /// Ring is full (ChannelFull).
    Full,
}

/// A Vyukov bounded MPMC ring buffer in shared memory.
pub struct Ring {
    /// Offset of the ring header within the shm region.
    ring_offset: usize,
}

impl Ring {
    /// Create a Ring handle for an existing ring at `ring_offset`.
    pub fn new(ring_offset: usize) -> Self {
        Self { ring_offset }
    }

    /// Get the ring header offset.
    pub fn offset(&self) -> usize {
        self.ring_offset
    }

    /// Initialize a newly allocated ring buffer.
    /// Must be called under global flock.
    ///
    /// # Capacity constraint
    ///
    /// `capacity` MUST be >= 2. A single-slot ring is not representable in
    /// this seq encoding: `published-unconsumed` (seq = pos+1) and
    /// `recycled-empty` (seq = pos+cap = pos+1) collide, so a second enqueue
    /// would silently overwrite a live message with no Full signal.
    ///
    /// # Safety
    /// - `region` must be valid.
    /// - The memory at `ring_offset` must be large enough for the ring.
    pub unsafe fn init(&self, region: &ShmRegion, capacity: u32) {
        debug_assert!(capacity >= 2, "single-slot rings are not representable");
        // Write header
        region.store_u64(self.ring_offset + layout::RING_ENQUEUE_POS, 0);
        region.store_u64(self.ring_offset + layout::RING_DEQUEUE_POS, 0);
        region.write_u32(self.ring_offset + layout::RING_CAPACITY, capacity);
        region.store_u64(self.ring_offset + layout::RING_LAST_COMPACT_ENQ, 0);
        region.store_u64(self.ring_offset + layout::RING_LAST_COMPACT_DEQ, 0);
        region.store_u64(self.ring_offset + layout::RING_GENERATION, 0);

        // Initialize each slot
        let slot_size = layout::SLOT_SIZE;
        let slots_start = self.ring_offset + layout::RING_HEADER_SIZE;
        for i in 0..capacity as usize {
            let slot_off = slots_start + i * slot_size;
            region.store_u64(slot_off + layout::SLOT_SEQ, i as u64);
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
            region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
            region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, 0);
            region.write_u8(slot_off + layout::SLOT_COMPACT_MARK, 0);
            region.store_u64(slot_off + layout::SLOT_EXPIRY_TS, 0);
            region.write_u16(slot_off + layout::SLOT_CHANNEL_LEN, 0);
            region.write_u32(slot_off + layout::SLOT_MSG_LEN, 0);
            region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, 0);
        }
    }

    /// Get the capacity of this ring.
    ///
    /// # Safety
    ///
    /// `region` must be a live, mapped shared-memory region containing this
    /// ring's initialized header (written by [`Ring::init`]).
    pub unsafe fn capacity(&self, region: &ShmRegion) -> u32 {
        region.read_u32(self.ring_offset + layout::RING_CAPACITY)
    }

    /// Get the inline size from the global header.
    unsafe fn inline_size(&self, region: &ShmRegion) -> u32 {
        region.read_u32(layout::HDR_INLINE_SIZE)
    }

    /// Get the slot offset for a given index.
    unsafe fn slot_offset(&self, idx: usize) -> usize {
        self.ring_offset + layout::RING_HEADER_SIZE + idx * layout::SLOT_SIZE
    }

    /// Enqueue a message into the ring.
    ///
    /// Vyukov CAS-claim admission: a ticket is consumed only after the target
    /// slot is verified EMPTY (`seq == pos`). Every `Full` return happens
    /// before the claim, so `enqueue_pos` never advances on failure — a send
    /// retried after a drain lands on the same ticket, and a crashed
    /// producer's claimed-but-unpublished ticket is the only residual state
    /// (compact's job).
    ///
    /// # Safety
    /// - The ring must be initialized.
    /// - `region` must be valid.
    #[allow(clippy::too_many_arguments)]
    pub unsafe fn try_enqueue(
        &self,
        region: &ShmRegion,
        slab: &SlabAllocator,
        channel_name: &[u8],
        msg_data: &[u8],
        expiry_ts: f64,
        owner: OwnerIdentity,
    ) -> EnqueueResult {
        let cap = self.capacity(region) as u64;
        debug_assert!(cap >= 2, "single-slot rings are not representable");
        let inline_size = self.inline_size(region) as usize;

        // Flush fence: if reset() bumps the generation while this operation
        // is in flight, the pool this payload is heading into has been wiped;
        // retract (tombstone our own round) instead of publishing a stale
        // message or a resurrected overflow pointer.
        let gen0 = region.load_u64(self.ring_offset + layout::RING_GENERATION);

        // Step 1: verify-then-claim. A Full return here consumed no ticket.
        let pos;
        let slot_off;
        let mut seq_ahead_spins: u32 = 0;
        loop {
            let p = region.load_u64(self.ring_offset + layout::RING_ENQUEUE_POS);
            let so = self.slot_offset((p % cap) as usize);
            let seq = region.load_u64(so + layout::SLOT_SEQ);
            if seq == p {
                // EMPTY for round p — take the ticket.
                if region
                    .cas_u64(self.ring_offset + layout::RING_ENQUEUE_POS, p, p + 1)
                    .is_ok()
                {
                    pos = p;
                    slot_off = so;
                    break;
                }
                continue; // Lost to a concurrent producer — re-read.
            }
            if seq < p {
                // Ticket p's slot still holds its previous round: the ring is
                // full by ticket accounting, or the slot is a crash residual.
                let owner_pid = region.read_u32(so + layout::SLOT_OWNER_PID);
                if owner_pid != 0
                    && owner_pid != layout::SLOT_RECOVERING
                    && crate::layout::pid_dead(
                        owner_pid,
                        region.load_u64(so + layout::SLOT_OWNER_START_TIME),
                    )
                {
                    self.recover_slot(region, slab, so, cap, owner_pid);
                    continue;
                }
                return EnqueueResult::Full;
            }
            // seq > pos: a compact repair wrote a future ticket under an
            // in-flight producer. Bounded spin, then Full (no side effects).
            seq_ahead_spins += 1;
            if seq_ahead_spins >= SEQ_AHEAD_SPIN_BOUND {
                return EnqueueResult::Full;
            }
            std::thread::yield_now();
        }

        // Step 2: owner tracking — AFTER the claim, so the record describes a
        // ticket this producer owns and never clobbers the previous round's
        // owner (which recover_slot relies on).
        debug_assert!(
            owner.pid != layout::SLOT_RECOVERING,
            "pid collides with RECOVERING sentinel"
        );
        region.write_u32(slot_off + layout::SLOT_OWNER_PID, owner.pid);
        region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, pos);
        region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, owner.start_time);

        // Step 3: overflow allocation BEFORE any payload write, so the SKIP
        // tombstone below leaves the payload untouched.
        let overflow_off = if msg_data.len() > inline_size {
            let off = slab.alloc(region, msg_data.len());
            if off == 0 {
                // Slab exhausted. Unraced: recycle the claimed round (SKIP
                // tombstone — the ring keeps working; zero new consumer code,
                // seq = pos + cap falls into dequeue's existing skip branch).
                // Raced by a flush: write NOTHING — the reset state is already
                // the correct final state for an unpublished round, and a
                // fresh round may have re-claimed this slot under the same
                // ticket number (cross-generation aliasing), which any store
                // here could clobber. Drop our owner record only; the CAS
                // fails harmlessly if a fresh round already replaced it.
                if region.load_u64(self.ring_offset + layout::RING_GENERATION) != gen0 {
                    let _ = region.cas_u64(slot_off + layout::SLOT_OWNER_PID, owner.pid as u64, 0);
                } else {
                    let _ = region.cas_u64(slot_off + layout::SLOT_SEQ, pos, pos + cap);
                    region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
                    region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
                }
                return EnqueueResult::Full;
            }
            off
        } else {
            0
        };

        // Step 4: write message data
        // Write expiry_ts
        region.store_u64(slot_off + layout::SLOT_EXPIRY_TS, expiry_ts.to_bits());

        // Write channel name
        let name_len = channel_name.len().min(128);
        region.write_u16(slot_off + layout::SLOT_CHANNEL_LEN, name_len as u16);
        region.copy_in(
            slot_off + layout::SLOT_CHANNEL_NAME,
            &channel_name[..name_len],
        );

        // Write message
        region.write_u32(slot_off + layout::SLOT_MSG_LEN, msg_data.len() as u32);
        if overflow_off == 0 {
            // Inline
            region.copy_in(slot_off + layout::SLOT_INLINE, msg_data);
            region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, 0);
        } else {
            // Overflow page allocated in step 3
            region.copy_in(overflow_off as usize, msg_data);
            region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, overflow_off);
        }

        // Step 5: flush raced us mid-payload — retract WITHOUT writing. The
        // round is unpublished, so the reset state is already the correct
        // final state (the next round overwrites the payload before it
        // publishes, and our never-published overflow pointer is inert).
        // Writing seq/overflow here could clobber a FRESH round that
        // re-claimed this slot under the same ticket number after the flush
        // (cross-generation aliasing: worst case a zeroed live overflow
        // pointer → garbage delivery). Drop our owner record only; the CAS
        // fails harmlessly if a fresh round already replaced it.
        if region.load_u64(self.ring_offset + layout::RING_GENERATION) != gen0 {
            let _ = region.cas_u64(slot_off + layout::SLOT_OWNER_PID, owner.pid as u64, 0);
            return EnqueueResult::Full;
        }

        // Step 6: Publish (seq = pos + 1), then clear owner.
        region.store_u64(slot_off + layout::SLOT_SEQ, pos + 1);
        region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
        region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);

        // Step 7: the flush landed between the publish and the world seeing
        // it — retract the published round. A pre-flush message must not
        // surface post-flush; the CAS keeps a consumer that already claimed
        // the round safe (they finish on the intact payload; the overflow
        // claim-CAS keeps their free exactly-once).
        if region.load_u64(self.ring_offset + layout::RING_GENERATION) != gen0 {
            let _ = region.cas_u64(slot_off + layout::SLOT_SEQ, pos + 1, pos + cap);
        }

        EnqueueResult::Ok
    }

    /// Try to dequeue a message from the ring (non-blocking).
    /// Returns None if the ring is empty or no message is currently available.
    /// Returns Some((channel_name, msg_data)) on success.
    ///
    /// Uses CAS on dequeue_pos to avoid consuming positions for empty slots.
    ///
    /// # Safety
    /// - The ring must be initialized.
    pub unsafe fn try_dequeue(
        &self,
        region: &ShmRegion,
        slab: &SlabAllocator,
        now: f64,
        owner: OwnerIdentity,
    ) -> Option<(Vec<u8>, Vec<u8>)> {
        let cap = self.capacity(region) as u64;
        let dequeue_pos_off = self.ring_offset + layout::RING_DEQUEUE_POS;

        loop {
            let pos = region.load_u64(dequeue_pos_off);
            // Flush fence (see try_enqueue): a reset that lands between the
            // claim below and the overflow free would push a page of the
            // wiped pool into the fresh free list (double allocation).
            let gen0 = region.load_u64(self.ring_offset + layout::RING_GENERATION);
            let idx = (pos % cap) as usize;
            let slot_off = self.slot_offset(idx);

            // Check slot state
            let seq = region.load_u64(slot_off + layout::SLOT_SEQ);
            if seq < pos + 1 {
                // Phase behind: check for a crashed owner. SLOT_RECOVERING is
                // never treated as a dead owner: a parked repair is either
                // still running (pid_dead hardening returns false for the
                // sentinel) or its finisher is compact.
                let owner_pid = region.read_u32(slot_off + layout::SLOT_OWNER_PID);
                if owner_pid != 0 {
                    let owner_st = region.load_u64(slot_off + layout::SLOT_OWNER_START_TIME);
                    if owner_pid != layout::SLOT_RECOVERING
                        && crate::layout::pid_dead(owner_pid, owner_st)
                    {
                        self.recover_slot(region, slab, slot_off, cap, owner_pid);
                        continue;
                    }
                }
                return None; // Ring empty or slot being written/repaired
            }
            if seq > pos + 1 {
                // Slot already consumed by another consumer or stale
                // Try to advance dequeue_pos
                if region.cas_u64(dequeue_pos_off, pos, pos + 1).is_ok() {
                    continue; // Advanced, try next
                }
                continue; // CAS failed, retry with new pos
            }
            // seq == pos + 1: READY — try to claim via CAS
            match region.cas_u64(dequeue_pos_off, pos, pos + 1) {
                Ok(_) => {
                    // Claimed! Process the message — unless a flush raced the
                    // claim: recycle the slot WITHOUT freeing its overflow
                    // page into the wiped-and-restarted pool, and let the
                    // caller retry from the fresh dequeue_pos.
                    if region.load_u64(self.ring_offset + layout::RING_GENERATION) != gen0 {
                        let _ = region.cas_u64(slot_off + layout::SLOT_SEQ, pos + 1, pos + cap);
                        region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
                        region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
                        return None;
                    }
                }
                Err(_) => continue, // Another consumer claimed it
            }

            // Check expiry
            let expiry_bits = region.load_u64(slot_off + layout::SLOT_EXPIRY_TS);
            let expiry_ts = f64::from_bits(expiry_bits);
            if now > expiry_ts {
                // Expired — skip this message, recycle slot
                region.store_u64(slot_off + layout::SLOT_SEQ, pos + cap);
                region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
                region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
                // Release overflow page if any. Claim-then-free: a crash
                // after the CAS leaks the page (safe); the old free-then-zero
                // order double-freed it into the slab free-list.
                let overflow_off = region.load_u64(slot_off + layout::SLOT_OVERFLOW_OFF);
                if overflow_off != 0 {
                    let msg_len = region.read_u32(slot_off + layout::SLOT_MSG_LEN) as usize;
                    if region
                        .cas_u64(slot_off + layout::SLOT_OVERFLOW_OFF, overflow_off, 0)
                        .is_ok()
                    {
                        slab.free(region, overflow_off, msg_len);
                    }
                }
                continue; // Try next slot
            }

            // Set owner (紧贴判定后置位)
            debug_assert!(
                owner.pid != layout::SLOT_RECOVERING,
                "pid collides with RECOVERING sentinel"
            );
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, owner.pid);
            region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, pos);
            region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, owner.start_time);

            // Read channel name
            let ch_len = region.read_u16(slot_off + layout::SLOT_CHANNEL_LEN) as usize;
            let mut channel_name = vec![0u8; ch_len];
            region.read_bytes(slot_off + layout::SLOT_CHANNEL_NAME, &mut channel_name);

            // Read message data
            let msg_len = region.read_u32(slot_off + layout::SLOT_MSG_LEN) as usize;
            let overflow_off = region.load_u64(slot_off + layout::SLOT_OVERFLOW_OFF);
            let msg_data = if overflow_off != 0 {
                // Read from overflow page
                let data = region.copy_out(overflow_off as usize, msg_len);
                // Free overflow page (dequeue is the last reader).
                // Claim-then-free: a crash after the CAS leaks the page
                // (safe); free-then-zero double-freed on crash.
                if region
                    .cas_u64(slot_off + layout::SLOT_OVERFLOW_OFF, overflow_off, 0)
                    .is_ok()
                {
                    slab.free(region, overflow_off, msg_len);
                }
                data
            } else {
                // Read inline
                let mut buf = vec![0u8; msg_len];
                region.read_bytes(slot_off + layout::SLOT_INLINE, &mut buf);
                buf
            };

            // Recycle slot (seq = pos + capacity)
            region.store_u64(slot_off + layout::SLOT_SEQ, pos + cap);

            // Clear owner
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
            region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);

            return Some((channel_name, msg_data));
        }
    }

    /// Recover a crashed slot (owner != 0, pid_dead confirmed by caller).
    /// Uses CAS on owner_pid as a mutex: only the first caller to flip
    /// owner_pid from the dead PID to SLOT_RECOVERING performs the repair;
    /// concurrent callers' CAS fails and they return immediately.
    /// This prevents double-free of overflow pages under thundering-herd
    /// recovery (multiple enqueuers/dequeuers hit the same dead slot).
    ///
    /// # Safety
    /// - slot_off must be a valid slot offset.
    /// - dead_owner_pid must be the value read by the caller AND confirmed
    ///   dead via pid_dead (used as CAS expected to avoid TOCTOU re-read).
    unsafe fn recover_slot(
        &self,
        region: &ShmRegion,
        slab: &SlabAllocator,
        slot_off: usize,
        cap: u64,
        dead_owner_pid: u32,
    ) {
        // CAS mutex: claim exclusive repair right.
        // CAS operates on the 8-byte slot [SLOT_OWNER_PID, +8); padding is 0 (layout invariant).
        // Observers never pass SLOT_RECOVERING here (call-site guards plus
        // pid_dead hardening), so this CAS cannot degrade into the no-op
        // (RECOVERING → RECOVERING) that broke mutual exclusion.
        if region
            .cas_u64(
                slot_off + layout::SLOT_OWNER_PID,
                dead_owner_pid as u64,
                layout::SLOT_RECOVERING as u64,
            )
            .is_err()
        {
            return; // Another process is already repairing this slot.
        }

        // Repair to the dead owner's own next round.
        let ticket = region.load_u64(slot_off + layout::SLOT_OWNER_TICKET);
        let observed_seq = region.load_u64(slot_off + layout::SLOT_SEQ);
        self.repair_slot_to(region, slab, slot_off, observed_seq, ticket + cap);
    }

    /// Idempotent stuck-slot repair: make the slot EMPTY for `target`.
    ///
    /// Safe to re-run (compact finishing a repair whose recoverer crashed)
    /// and safe to race:
    /// - the overflow release is CAS-claimed (exactly once; a crash after the
    ///   claim leaks the page instead of double-freeing it),
    /// - the seq store is a CAS from `observed_seq`, so it can never move a
    ///   slot that a concurrent repair or producer already advanced,
    /// - ownership is only cleared while the slot still shows RECOVERING.
    ///
    /// # Safety
    /// - slot_off must be a valid slot offset.
    unsafe fn repair_slot_to(
        &self,
        region: &ShmRegion,
        slab: &SlabAllocator,
        slot_off: usize,
        observed_seq: u64,
        target: u64,
    ) {
        // Release overflow page if any (claim-then-free, idempotent).
        let overflow_off = region.load_u64(slot_off + layout::SLOT_OVERFLOW_OFF);
        if overflow_off != 0 {
            let msg_len = region.read_u32(slot_off + layout::SLOT_MSG_LEN) as usize;
            if region
                .cas_u64(slot_off + layout::SLOT_OVERFLOW_OFF, overflow_off, 0)
                .is_ok()
            {
                slab.free(region, overflow_off, msg_len);
            }
        }
        // Fix seq = target (conditional: never move an already-advanced slot).
        // Safe against the consumer for every representable capacity (>= 2):
        // target is one full round ahead, so it can only satisfy the
        // consumer's skip check (seq > deq+1), never its READY equality.
        let _ = region.cas_u64(slot_off + layout::SLOT_SEQ, observed_seq, target);
        // Clear owner (RECOVERING → 0; no-op when already ownerless).
        let _ = region.cas_u64(
            slot_off + layout::SLOT_OWNER_PID,
            layout::SLOT_RECOVERING as u64,
            0,
        );
        region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
    }

    /// Reset the ring for flush.
    /// Must be called under global flock.
    ///
    /// Concurrent producers/consumers are tolerated: the generation is
    /// bumped (release) BEFORE the teardown, and the hot paths sample it
    /// around their critical sections — an operation that raced this reset
    /// retracts instead of publishing into, or freeing pages of, the wiped
    /// state (2026-09-04-flush-generation-fence). The residual window is the
    /// few instructions between an operation's last generation check and its
    /// final seq store / slab free.
    ///
    /// # Safety
    ///
    /// - `region` must be a live, mapped shared-memory region containing this
    ///   initialized ring.
    /// - The caller must hold the global flock (serializes reset against the
    ///   other cold-path mutations; the fence handles the lock-free hot path).
    pub unsafe fn reset(&self, region: &ShmRegion) {
        let cap = self.capacity(region);
        // Flush fence: bump the generation FIRST (release), so in-flight
        // producers/consumers sampling the old value retract instead of
        // publishing into — or freeing pages of — the state torn down below.
        let gen = region.load_u64(self.ring_offset + layout::RING_GENERATION);
        region.store_u64(
            self.ring_offset + layout::RING_GENERATION,
            gen.wrapping_add(1),
        );
        region.store_u64(self.ring_offset + layout::RING_ENQUEUE_POS, 0);
        region.store_u64(self.ring_offset + layout::RING_DEQUEUE_POS, 0);
        region.store_u64(self.ring_offset + layout::RING_LAST_COMPACT_ENQ, 0);
        region.store_u64(self.ring_offset + layout::RING_LAST_COMPACT_DEQ, 0);

        let slots_start = self.ring_offset + layout::RING_HEADER_SIZE;
        for i in 0..cap as usize {
            let slot_off = slots_start + i * layout::SLOT_SIZE;
            region.store_u64(slot_off + layout::SLOT_SEQ, i as u64);
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
            region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
            region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, 0);
            region.write_u8(slot_off + layout::SLOT_COMPACT_MARK, 0);
            region.store_u64(slot_off + layout::SLOT_EXPIRY_TS, 0);
            region.write_u16(slot_off + layout::SLOT_CHANNEL_LEN, 0);
            region.write_u32(slot_off + layout::SLOT_MSG_LEN, 0);
            // Free overflow page if any
            let overflow_off = region.load_u64(slot_off + layout::SLOT_OVERFLOW_OFF);
            if overflow_off != 0 {
                // In flush context, we can't easily determine the slab class,
                // but the slab will be reset anyway.
                region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, 0);
            }
        }
    }

    /// Compact the ring: fix residual stuck slots.
    /// Must be called under global flock.
    ///
    /// Two-pass confirmation: a slot is repaired only when it still looks
    /// stuck on a SECOND pass. The passes are separated by the caller's
    /// cadence (watchdog ticks or manual calls) — the old ≥2×cap
    /// position-advancement gate is gone: since enqueue stopped burning
    /// tickets on Full, a stalled ring's positions never advance, and the
    /// gate would starve exactly the rings that need repair.
    ///
    /// # Safety
    /// - The ring must be initialized.
    pub unsafe fn compact(&self, region: &ShmRegion, slab: &SlabAllocator, _start_time: u64) {
        let cap = self.capacity(region) as u64;
        let enq = region.load_u64(self.ring_offset + layout::RING_ENQUEUE_POS);
        let deq = region.load_u64(self.ring_offset + layout::RING_DEQUEUE_POS);
        let max_pos = enq.max(deq);

        let slots_start = self.ring_offset + layout::RING_HEADER_SIZE;
        for i in 0..cap as usize {
            let slot_off = slots_start + i * layout::SLOT_SIZE;
            let seq = region.load_u64(slot_off + layout::SLOT_SEQ);
            let owner_pid = region.read_u32(slot_off + layout::SLOT_OWNER_PID);

            // P = next ticket targeting this slot: the smallest ticket >= max_pos
            // that satisfies ticket % cap == i. The naive ((max_pos/cap)+1)*cap+i
            // is one full round ahead whenever max_pos % cap <= i, which repairs
            // healthy slots to a future ticket and deadlocks the next enqueue in
            // the seq>pos spin branch (docs/BUGS.md).
            let base = (max_pos / cap) * cap;
            let p = if max_pos % cap <= i as u64 {
                base + i as u64
            } else {
                base + cap + i as u64
            };

            // Condition (a): the slot's round is fully behind the ring's
            // frontier AND it has no live owner. With honest counters
            // (enq - deq <= cap, an invariant of CAS-claim admission) a live
            // message's seq = ticket+1 always exceeds max_pos - cap, so live
            // messages cannot match. SLOT_RECOVERING counts as ownerless: a
            // crashed recoverer erased its identity; the idempotent repair
            // body is the only safe takeover.
            let ownerless = owner_pid == 0 || owner_pid == layout::SLOT_RECOVERING;
            // max_pos >= cap keeps the all-zero ring (fresh or just flushed)
            // out of the predicate: its slot 0 has seq 0 <= 0 saturated, which
            // would mark and "repair" it to the same value every cycle.
            if max_pos >= cap && seq <= max_pos - cap && ownerless {
                let compact_mark = region.read_u8(slot_off + layout::SLOT_COMPACT_MARK);

                if compact_mark == 1 {
                    // Condition (c): confirmed across two compact passes — repair.
                    let target = if owner_pid == layout::SLOT_RECOVERING {
                        // Finish the interrupted repair at its own round.
                        region.load_u64(slot_off + layout::SLOT_OWNER_TICKET) + cap
                    } else {
                        p
                    };
                    self.repair_slot_to(region, slab, slot_off, seq, target);
                    region.write_u8(slot_off + layout::SLOT_COMPACT_MARK, 0);
                } else {
                    // First observation — mark
                    region.write_u8(slot_off + layout::SLOT_COMPACT_MARK, 1);
                }
            } else {
                // Not stuck — clear any residual mark
                region.write_u8(slot_off + layout::SLOT_COMPACT_MARK, 0);
            }
        }

        // Baseline bookkeeping (observability only — no gate reads it).
        region.store_u64(self.ring_offset + layout::RING_LAST_COMPACT_ENQ, enq);
        region.store_u64(self.ring_offset + layout::RING_LAST_COMPACT_DEQ, deq);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::region::ShmRegion;
    use crate::slab::SlabAllocator;
    use std::ptr::NonNull;

    /// Create a zeroed 8-byte-aligned region backed by a Vec<u64>.
    fn make_region(size: usize) -> (Vec<u64>, ShmRegion) {
        let words = size.div_ceil(8);
        let buf = vec![0u64; words];
        let ptr = buf.as_ptr() as *mut u8;
        let non_null = NonNull::new(ptr).unwrap();
        let region = unsafe { ShmRegion::new(non_null, size) };
        (buf, region)
    }

    /// Setup a ring with given capacity after the global header, and a slab after it.
    /// Sets HDR_INLINE_SIZE for the ring's inline_size() read.
    fn setup_ring(capacity: u32, inline_size: u32) -> (Vec<u64>, ShmRegion, Ring, SlabAllocator) {
        // Ring: header + capacity * SLOT_SIZE
        let ring_size = layout::RING_HEADER_SIZE + capacity as usize * layout::SLOT_SIZE;
        // Place ring after the global header to avoid overlap
        let ring_offset = layout::HDR_SIZE;
        // Slab pool: 32KB after the ring
        let pool_offset = ring_offset + ring_size;
        let pool_size = 32 * 1024;
        let total_size = pool_offset + pool_size;

        let (buf, region) = make_region(total_size);

        // Set inline_size in the global header (offset 24)
        // SAFETY: offset 24 + 4 <= total_size.
        unsafe {
            region.write_u32(layout::HDR_INLINE_SIZE, inline_size);
        }

        // Initialize slab
        let slab = SlabAllocator::new(pool_offset, pool_size);
        slab.init(&region);

        // Initialize ring after the global header
        let ring = Ring::new(ring_offset);
        // SAFETY: region is large enough.
        unsafe {
            ring.init(&region, capacity);
        }

        (buf, region, ring, slab)
    }

    #[test]
    fn test_ring_init_capacity() {
        let (_buf, region, ring, _slab) = setup_ring(4, 512);
        // SAFETY: ring is initialized.
        unsafe {
            assert_eq!(ring.capacity(&region), 4);
            // Test the offset() method - ring is after the global header
            assert_eq!(ring.offset(), layout::HDR_SIZE);
        }
    }

    #[test]
    fn test_ring_enqueue_dequeue_basic() {
        let (_buf, region, ring, slab) = setup_ring(4, 512);

        let channel = b"test.channel";
        let msg = b"hello world"; // 11 bytes, inline (inline_size=512)
        let expiry = f64::MAX; // Never expires
        let pid = 1u32;
        let start_time = 0u64;

        // Enqueue - this should take the inline path (msg.len() <= inline_size=512)
        // SAFETY: ring is initialized, region and slab are valid.
        let result = unsafe {
            ring.try_enqueue(
                &region,
                &slab,
                channel,
                msg,
                expiry,
                OwnerIdentity { pid, start_time },
            )
        };
        assert_eq!(result, EnqueueResult::Ok);

        // Dequeue - this should read inline
        // SAFETY: ring has one message.
        let dequeued = unsafe {
            ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time })
        };
        let (ch, data) = dequeued.expect("should dequeue a message");
        assert_eq!(ch, channel);
        assert_eq!(data, msg);
    }

    #[test]
    fn test_ring_fifo_order() {
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        // Enqueue 3 messages
        // SAFETY: ring is initialized.
        unsafe {
            for i in 0..3u8 {
                let msg = [i];
                let result = ring.try_enqueue(
                    &region,
                    &slab,
                    b"ch",
                    &msg,
                    expiry,
                    OwnerIdentity { pid, start_time },
                );
                assert_eq!(result, EnqueueResult::Ok);
            }
        }

        // Dequeue in FIFO order
        // SAFETY: ring has 3 messages.
        unsafe {
            for i in 0..3u8 {
                let (ch, data) = ring
                    .try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time })
                    .expect("should dequeue");
                assert_eq!(ch, b"ch");
                assert_eq!(data, [i]);
            }
        }
    }

    #[test]
    fn test_ring_full() {
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        // Fill the ring (capacity = 2)
        // SAFETY: ring is initialized.
        unsafe {
            assert_eq!(
                ring.try_enqueue(
                    &region,
                    &slab,
                    b"ch",
                    b"msg1",
                    expiry,
                    OwnerIdentity { pid, start_time }
                ),
                EnqueueResult::Ok
            );
            assert_eq!(
                ring.try_enqueue(
                    &region,
                    &slab,
                    b"ch",
                    b"msg2",
                    expiry,
                    OwnerIdentity { pid, start_time }
                ),
                EnqueueResult::Ok
            );
            // Third enqueue should fail (Full)
            assert_eq!(
                ring.try_enqueue(
                    &region,
                    &slab,
                    b"ch",
                    b"msg3",
                    expiry,
                    OwnerIdentity { pid, start_time }
                ),
                EnqueueResult::Full
            );
        }
    }

    #[test]
    fn test_ring_wrap_around() {
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        // SAFETY: ring is initialized.
        unsafe {
            // Enqueue + dequeue cycle 1
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"a",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            let (ch, data) = ring
                .try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time })
                .unwrap();
            assert_eq!(ch, b"ch");
            assert_eq!(data, b"a");

            // Enqueue + dequeue cycle 2 (wraps around)
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"b",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            let (_ch, data) = ring
                .try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time })
                .unwrap();
            assert_eq!(data, b"b");

            // Enqueue + dequeue cycle 3 (wraps again)
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"c",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            let (_ch, data) = ring
                .try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time })
                .unwrap();
            assert_eq!(data, b"c");
        }
    }

    #[test]
    fn test_ring_empty() {
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let pid = 1u32;
        let start_time = 0u64;

        // Dequeue from empty ring should return None
        // SAFETY: ring is initialized but empty.
        let result = unsafe {
            ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time })
        };
        assert!(
            result.is_none(),
            "dequeue from empty ring should return None"
        );
    }

    #[test]
    fn test_ring_expired_message_skipped() {
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let pid = 1u32;
        let start_time = 0u64;

        // Enqueue with past expiry → should be skipped on dequeue
        // SAFETY: ring is initialized.
        unsafe {
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"expired",
                0.0,
                OwnerIdentity { pid, start_time },
            );
            // now=100 > expiry=0 → expired
            let result = ring.try_dequeue(&region, &slab, 100.0, OwnerIdentity { pid, start_time });
            assert!(result.is_none(), "expired message should be skipped");
        }
    }

    #[test]
    fn test_ring_overflow_message() {
        // Use small inline_size (16) so a larger message uses the slab overflow path.
        let (_buf, region, ring, slab) = setup_ring(4, 16);

        let large_msg = vec![0xABu8; 100]; // Larger than inline_size=16
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        // SAFETY: ring is initialized, slab is large enough for overflow.
        unsafe {
            let result = ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                &large_msg,
                expiry,
                OwnerIdentity { pid, start_time },
            );
            assert_eq!(result, EnqueueResult::Ok);

            let (ch, data) = ring
                .try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time })
                .expect("should dequeue overflow message");
            assert_eq!(ch, b"ch");
            assert_eq!(data, large_msg);
        }
    }

    #[test]
    fn test_ring_reset() {
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        // SAFETY: ring is initialized.
        unsafe {
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            // Reset should clear the ring
            ring.reset(&region);
            // After reset, ring should be empty
            let result =
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            assert!(result.is_none(), "ring should be empty after reset");
            // Enqueue should still work
            let result = ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"new",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            assert_eq!(result, EnqueueResult::Ok);
        }
    }

    #[test]
    fn test_ring_compact_stuck_slot() {
        // Test compact() fixing a residual-window stuck slot (owner_pid=0, seq behind).
        // compact requires baseline_advancement >= 2*cap between consecutive calls.
        let cap = 2u32;
        let (_buf, region, ring, slab) = setup_ring(cap, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // Advance positions past 2*cap for first compact
            for _ in 0..6 {
                ring.try_enqueue(
                    &region,
                    &slab,
                    b"ch",
                    b"msg",
                    expiry,
                    OwnerIdentity { pid, start_time },
                );
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            }

            // Force a stuck slot at index 0: seq behind with owner_pid=0
            let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.store_u64(slot_off + layout::SLOT_SEQ, 0);
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
            region.write_u8(slot_off + layout::SLOT_COMPACT_MARK, 0);

            // First compact: marks the slot (compact_mark: 0→1)
            ring.compact(&region, &slab, start_time);
            let mark = region.read_u8(slot_off + layout::SLOT_COMPACT_MARK);
            assert_eq!(mark, 1, "first compact should mark the slot");

            // Reset baseline so second compact passes the advancement check
            // (simulates more time passing without needing to enqueue/dequeue)
            region.store_u64(ring.ring_offset + layout::RING_LAST_COMPACT_ENQ, 0);
            region.store_u64(ring.ring_offset + layout::RING_LAST_COMPACT_DEQ, 0);

            // Second compact: confirms mark and resets (compact_mark: 1→0)
            ring.compact(&region, &slab, start_time);
            let mark = region.read_u8(slot_off + layout::SLOT_COMPACT_MARK);
            assert_eq!(mark, 0, "second compact should clear the mark");
        }
    }

    #[test]
    fn test_ring_compact_repairs_to_next_ticket_not_ahead() {
        // Regression test for the compact p-formula bug (docs/BUGS.md):
        // p must be the NEXT ticket targeting the slot, not one full round ahead.
        // Repro (burn-free since CAS-claim: Full no longer advances
        // enqueue_pos, so the stuck state is injected directly):
        //   1. drive 16 enq+deq rounds -> enq=deq=16, seqs=[16,17,18,19]
        //   2. inject the E1 residual on slot 0 (seq stuck behind, owner==0)
        //   3. enqueue must return Full WITHOUT consuming ticket 16
        //   4. compact #1 (mark) -> compact #2 (repair; no advancement gate)
        //   assert slot0.seq == 16 (next ticket), NOT 20 (one round ahead).
        // With the bug, seq is repaired to 20 and ticket 16 then hits the
        // seq>pos spin branch (permanent deadlock).
        let cap = 4u32;
        let (_buf, region, ring, slab) = setup_ring(cap, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;
        let owner = OwnerIdentity { pid, start_time };

        unsafe {
            // 1. Drive 16 healthy rounds: enq=deq=16.
            for _ in 0..16 {
                assert_eq!(
                    ring.try_enqueue(&region, &slab, b"ch", b"m", expiry, owner),
                    EnqueueResult::Ok
                );
                assert!(ring.try_dequeue(&region, &slab, f64::MAX, owner).is_some());
            }

            // 2. Inject the E1 residual window on slot 0: a claimed-but-
            //    crashed round whose owner was never recorded (seq one round
            //    behind the frontier, ownerless).
            let slot0_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.store_u64(slot0_off + layout::SLOT_SEQ, 4);
            region.write_u32(slot0_off + layout::SLOT_OWNER_PID, 0);
            region.write_u8(slot0_off + layout::SLOT_COMPACT_MARK, 0);

            // 3. Enqueue must NOT take ticket 16 (slot not EMPTY for it) —
            //    and must not burn it either (enqueue_pos stays at 16).
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"m", expiry, owner),
                EnqueueResult::Full
            );
            assert_eq!(
                region.load_u64(ring.ring_offset + layout::RING_ENQUEUE_POS),
                16
            );

            // 4. Compact #1 marks; compact #2 confirms and repairs. The old
            //    >=2*cap advancement gate would starve here (positions are
            //    stalled), so this also regression-tests its removal.
            ring.compact(&region, &slab, start_time);
            assert_eq!(region.read_u8(slot0_off + layout::SLOT_COMPACT_MARK), 1);
            ring.compact(&region, &slab, start_time);

            // After repair, slot0.seq must equal the next ticket targeting
            // slot 0 (16). The buggy formula ((16/4)+1)*4+0=20 is one round
            // ahead and deadlocks ticket 16 in the seq>pos spin branch.
            let seq = region.load_u64(slot0_off + layout::SLOT_SEQ);
            assert_eq!(
                seq, 16,
                "compact must repair slot to the next ticket (16), got {seq} (bug: one round ahead)"
            );

            // 5. And enqueue of ticket 16 must succeed (not spin on seq>pos).
            // Guarded by a timeout so a regression fails fast instead of hanging.
            use std::sync::mpsc;
            let (tx, rx) = mpsc::channel();
            let handle = std::thread::spawn(move || {
                let result = ring.try_enqueue(&region, &slab, b"ch", b"m", expiry, owner);
                tx.send(result).unwrap();
            });
            match rx.recv_timeout(std::time::Duration::from_secs(2)) {
                Ok(result) => {
                    assert_eq!(
                        result,
                        EnqueueResult::Ok,
                        "ticket 16 must enqueue after repair"
                    );
                }
                Err(_) => {
                    panic!("enqueue after compact hangs (seq>pos spin) — compact p-formula bug")
                }
            }
            let _ = handle.join();
        }
    }

    #[test]
    fn test_ring_compact_does_not_touch_healthy_slots() {
        // Healthy ring must survive compact unchanged: seqs stay at the next
        // ticket values and enqueue keeps working.
        let cap = 4u32;
        let (_buf, region, ring, slab) = setup_ring(cap, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;
        let owner = OwnerIdentity { pid, start_time };

        unsafe {
            // Drive 8 enq+deq: enq=deq=8, seqs=[8,9,10,11] (all healthy EMPTY)
            for _ in 0..8 {
                ring.try_enqueue(&region, &slab, b"ch", b"m", expiry, owner);
                ring.try_dequeue(&region, &slab, f64::MAX, owner);
            }
            // Compact #1 (advancement 8 >= 8): must NOT mark healthy slots
            ring.compact(&region, &slab, start_time);
            for i in 0..4 {
                let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE + i * layout::SLOT_SIZE;
                let mark = region.read_u8(slot_off + layout::SLOT_COMPACT_MARK);
                assert_eq!(mark, 0, "healthy slot {i} must not be marked");
            }
            // Drive 8 more: enq=16
            for _ in 0..8 {
                ring.try_enqueue(&region, &slab, b"ch", b"m", expiry, owner);
                ring.try_dequeue(&region, &slab, f64::MAX, owner);
            }
            // Compact #2: must not modify healthy seqs
            ring.compact(&region, &slab, start_time);
            for i in 0..4 {
                let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE + i * layout::SLOT_SIZE;
                let seq = region.load_u64(slot_off + layout::SLOT_SEQ);
                assert_eq!(
                    seq,
                    16 + i as u64,
                    "healthy slot {i} seq must stay at next ticket ({})",
                    16 + i
                );
            }
            // Enqueue must still work immediately (ticket 16 -> slot 0)
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"m", expiry, owner),
                EnqueueResult::Ok
            );
        }
    }

    #[test]
    fn test_ring_compact_skips_when_not_enough_advancement() {
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let start_time = 0u64;

        unsafe {
            // Don't advance positions enough — compact should skip
            ring.compact(&region, &slab, start_time);
            // No crash, no-op
        }
    }

    #[test]
    fn test_ring_compact_clears_mark_on_non_stuck_slot() {
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // Enqueue and dequeue to advance positions enough
            for _ in 0..10 {
                ring.try_enqueue(
                    &region,
                    &slab,
                    b"ch",
                    b"msg",
                    expiry,
                    OwnerIdentity { pid, start_time },
                );
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            }

            // Manually set a compact_mark on a non-stuck slot
            let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.write_u8(slot_off + layout::SLOT_COMPACT_MARK, 1);

            // Compact should clear the mark since the slot is not stuck
            ring.compact(&region, &slab, start_time);
            let mark = region.read_u8(slot_off + layout::SLOT_COMPACT_MARK);
            assert_eq!(mark, 0, "non-stuck slot mark should be cleared");
        }
    }

    #[test]
    fn test_ring_recover_slot() {
        // Test recover_slot when a process crashes with owner set.
        // To trigger recover in dequeue: seq < pos+1 AND owner_pid != 0 AND pid_dead.
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let pid = 1u32;
        let start_time = 0u64;

        unsafe {
            // Set up slot 0 with seq=0 (phase behind: 0 < 0+1) and dead owner
            let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.store_u64(slot_off + layout::SLOT_SEQ, 0);
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 999999);
            region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
            region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, 0);

            // Dequeue should detect phase behind + dead owner, recover, return None
            let result =
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            assert!(result.is_none());
        }
    }

    #[test]
    fn test_ring_dequeue_seq_greater_than_pos_plus_one() {
        // Test the seq > pos+1 path in try_dequeue (CAS retry).
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // Enqueue 2 messages
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg1",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg2",
                expiry,
                OwnerIdentity { pid, start_time },
            );

            // Read the current dequeue_pos
            let deq_pos = region.load_u64(ring.ring_offset + layout::RING_DEQUEUE_POS);

            // Advance dequeue_pos past the first message (simulate another consumer)
            region.store_u64(ring.ring_offset + layout::RING_DEQUEUE_POS, deq_pos + 1);

            // Now try_dequeue should get msg2 (the second message)
            let result =
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            let (_ch, data) = result.expect("should dequeue msg2");
            assert_eq!(data, b"msg2");
        }
    }

    #[test]
    fn test_ring_enqueue_slab_exhaustion() {
        // Use tiny pool to trigger slab exhaustion on overflow message.
        // Pool must be large enough for slab metadata but not for overflow pages.
        let ring_size = layout::RING_HEADER_SIZE + 4 * layout::SLOT_SIZE;
        // Slab metadata: 11 spinlocks * 8 + 11 free_heads * 8 + bump_ptr 8 = 184 bytes
        // Plus data area needs at least some space for slab to init, but not enough for 512-byte block
        let pool_size = 512; // Enough for metadata + 1 block of 512, but not 2
        let total_size = ring_size + pool_size;

        let (_buf, region) = make_region(total_size);
        unsafe {
            region.write_u32(layout::HDR_INLINE_SIZE, 16);
        }

        let slab = SlabAllocator::new(ring_size, pool_size);
        slab.init(&region);

        let ring = Ring::new(0);
        unsafe {
            ring.init(&region, 4);
        }

        let large_msg = vec![0xABu8; 100]; // > inline_size=16, needs overflow
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // First enqueue should succeed (allocates a 512-byte overflow block)
            let _ = ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                &large_msg,
                expiry,
                OwnerIdentity { pid, start_time },
            );
            // May succeed if pool has room for one 512-byte block
            // Second enqueue with large message should fail when pool is exhausted
            let _ = ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                &large_msg,
                expiry,
                OwnerIdentity { pid, start_time },
            );
            // At least one of these should fail if pool is truly tiny
            // If both succeed, the pool was big enough — that's OK too
            // The important thing is no crash
        }
    }

    #[test]
    fn test_ring_enqueue_overflow_and_dequeue() {
        // Test overflow path: message > inline_size uses slab.
        let (_buf, region, ring, slab) = setup_ring(4, 16);
        let large_msg = vec![0xCDu8; 200]; // > inline_size=16
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            let result = ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                &large_msg,
                expiry,
                OwnerIdentity { pid, start_time },
            );
            assert_eq!(result, EnqueueResult::Ok);

            let (ch, data) = ring
                .try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time })
                .expect("should dequeue overflow message");
            assert_eq!(ch, b"ch");
            assert_eq!(data, large_msg);
        }
    }

    #[test]
    fn test_ring_recover_slot_with_overflow() {
        // Test recover_slot with overflow page, triggered from dequeue.
        // Need: seq < pos+1 AND owner_pid != 0 AND pid_dead AND overflow_off != 0.
        let (_buf, region, ring, slab) = setup_ring(4, 16);
        let pid = 1u32;
        let start_time = 0u64;

        unsafe {
            // Set up slot with seq=0, dead owner, and an overflow page
            let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.store_u64(slot_off + layout::SLOT_SEQ, 0);
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 999999);
            region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
            region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, 0);

            // Allocate overflow page
            let overflow_off = slab.alloc(&region, 100);
            if overflow_off != 0 {
                region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, overflow_off);
                region.write_u32(slot_off + layout::SLOT_MSG_LEN, 100);
            }

            // Dequeue should recover, free overflow, return None
            let result =
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            assert!(result.is_none());
        }
    }

    #[test]
    fn test_ring_recover_slot_no_overflow() {
        // Test recover_slot with no overflow page (inline message).
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let pid = 1u32;
        let start_time = 0u64;

        unsafe {
            // Set up slot with seq=0, dead owner, no overflow
            let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.store_u64(slot_off + layout::SLOT_SEQ, 0);
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 999999);
            region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
            region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, 0);
            region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, 0);

            // Dequeue should recover and return None
            let result =
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            assert!(result.is_none());
        }
    }

    #[test]
    fn test_ring_dequeue_empty_with_crashed_owner() {
        // Test dequeue when a slot has a crashed owner but is logically empty.
        let (_buf, region, ring, slab) = setup_ring(4, 512);
        let pid = 1u32;
        let start_time = 0u64;

        unsafe {
            // Set up a slot with a crashed owner but seq behind
            let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 999999);
            region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, 0);
            // seq stays at initial value (0), which is < pos+1 (0+1=1)

            // Dequeue should detect crash and recover, then return None
            let _ = ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            // After recovery, the slot is recycled but empty
        }
    }

    #[test]
    fn test_ring_enqueue_after_full_recovery() {
        // Test that after recovering from a full ring, new messages can be enqueued.
        // Capacity=2, so after 2 enqueues the ring is full.
        // After recovering a crashed slot, the ring should accept new messages.
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // Enqueue 2 messages (fills ring at capacity=2)
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg1",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg2",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            assert_eq!(
                ring.try_enqueue(
                    &region,
                    &slab,
                    b"ch",
                    b"msg3",
                    expiry,
                    OwnerIdentity { pid, start_time }
                ),
                EnqueueResult::Full
            );

            // Simulate crash on slot 0 (pos=0 maps to idx=0)
            let slot0_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.write_u32(slot0_off + layout::SLOT_OWNER_PID, 999999);
            region.store_u64(slot0_off + layout::SLOT_OWNER_TICKET, 0);
            region.store_u64(slot0_off + layout::SLOT_OWNER_START_TIME, 0);

            // Dequeue slot 0 — should detect crashed owner and recover
            let _ = ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });

            // Dequeue slot 1 — normal message
            let _ = ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });

            // After consuming both slots, ring should be ready for new messages
            // The enqueue_pos is at 2, so the next enqueue uses pos=2
            // After recovery, slot 0's seq was set to ticket+cap = 0+2 = 2
            // So pos=2 should match seq=2 (EMPTY) → enqueue succeeds
            let result = ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg3",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            assert_eq!(result, EnqueueResult::Ok, "should enqueue after recovery");
        }
    }

    #[test]
    fn test_ring_enqueue_full_with_live_owner() {
        // Test enqueue path where seq < pos and owner is alive (line 125-128).
        // This should return Full without recovering.
        let cap = 2u32;
        let (_buf, region, ring, slab) = setup_ring(cap, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // Fill the ring
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg1",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg2",
                expiry,
                OwnerIdentity { pid, start_time },
            );

            // Third enqueue should fail (ring full, owner is live)
            let result = ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg3",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            assert_eq!(
                result,
                EnqueueResult::Full,
                "should return Full when ring is full"
            );
        }
    }

    #[test]
    fn test_ring_expired_overflow_message_skipped() {
        // Test dequeue with expired overflow message (lines 242-244).
        // When a message is expired and has an overflow page, the overflow page should be freed.
        let (_buf, region, ring, slab) = setup_ring(4, 16); // Small inline_size
        let pid = 1u32;
        let start_time = 0u64;

        unsafe {
            // Enqueue a large message with past expiry (uses overflow)
            let large_msg = vec![0xCDu8; 100]; // > inline_size=16
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                &large_msg,
                0.0,
                OwnerIdentity { pid, start_time },
            ); // expiry=0 (past)

            // Dequeue with now=100 > expiry=0 → expired, should free overflow
            let result = ring.try_dequeue(&region, &slab, 100.0, OwnerIdentity { pid, start_time });
            assert!(result.is_none(), "expired message should be skipped");

            // Verify the slot was recycled (can enqueue again)
            let result = ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"new",
                f64::MAX,
                OwnerIdentity { pid, start_time },
            );
            assert_eq!(result, EnqueueResult::Ok);
        }
    }

    #[test]
    fn test_ring_reset_with_overflow() {
        // Test reset when slots have overflow pages (lines 345-347).
        let (_buf, region, ring, slab) = setup_ring(4, 16); // Small inline_size
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // Enqueue a large message (uses overflow)
            let large_msg = vec![0xEFu8; 100]; // > inline_size=16
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                &large_msg,
                expiry,
                OwnerIdentity { pid, start_time },
            );

            // Reset should handle the overflow page
            ring.reset(&region);

            // After reset, ring should be empty
            let result =
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            assert!(result.is_none(), "ring should be empty after reset");
        }
    }

    #[test]
    fn test_ring_dequeue_cas_retry() {
        // Test the CAS retry path in try_dequeue (lines 220-221).
        // When seq > pos+1, the consumer tries to advance dequeue_pos via CAS.
        let cap = 4u32;
        let (_buf, region, ring, slab) = setup_ring(cap, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // Enqueue 3 messages
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg1",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg2",
                expiry,
                OwnerIdentity { pid, start_time },
            );
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg3",
                expiry,
                OwnerIdentity { pid, start_time },
            );

            // Manually advance dequeue_pos past the first message
            // This simulates another consumer having already consumed it
            let deq_pos = region.load_u64(ring.ring_offset + layout::RING_DEQUEUE_POS);
            region.store_u64(ring.ring_offset + layout::RING_DEQUEUE_POS, deq_pos + 1);

            // Now try_dequeue should see seq > pos+1 for the first slot,
            // try CAS to advance, and eventually get msg2
            let (_ch, data) = ring
                .try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time })
                .expect("should dequeue msg2");
            assert_eq!(data, b"msg2");
        }
    }

    #[test]
    fn test_ring_compact_stuck_slot_with_overflow() {
        // Test compact fixing a stuck slot that has an overflow page (lines 389-391).
        let cap = 2u32;
        let (_buf, region, ring, slab) = setup_ring(cap, 16); // Small inline_size
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // Enqueue a large message (uses overflow)
            let large_msg = vec![0xABu8; 100]; // > inline_size=16
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                &large_msg,
                expiry,
                OwnerIdentity { pid, start_time },
            );

            // Dequeue it to recycle the slot
            ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });

            // Advance positions enough for compact to act
            for _ in 0..6 {
                ring.try_enqueue(
                    &region,
                    &slab,
                    b"ch",
                    b"small",
                    expiry,
                    OwnerIdentity { pid, start_time },
                );
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
            }

            // Force a stuck slot at index 0 with overflow page
            let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            // Set seq behind with owner_pid=0 and an overflow page
            region.store_u64(slot_off + layout::SLOT_SEQ, 0);
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
            region.write_u8(slot_off + layout::SLOT_COMPACT_MARK, 0);
            // Allocate an overflow page for this slot
            let overflow_off = slab.alloc(&region, 100);
            if overflow_off != 0 {
                region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, overflow_off);
                region.write_u32(slot_off + layout::SLOT_MSG_LEN, 100);
            }

            // First compact: mark
            ring.compact(&region, &slab, start_time);
            let mark = region.read_u8(slot_off + layout::SLOT_COMPACT_MARK);
            assert_eq!(mark, 1, "first compact should mark");

            // Reset baseline for second compact
            region.store_u64(ring.ring_offset + layout::RING_LAST_COMPACT_ENQ, 0);
            region.store_u64(ring.ring_offset + layout::RING_LAST_COMPACT_DEQ, 0);

            // Second compact: should free overflow and reset
            ring.compact(&region, &slab, start_time);
            let mark = region.read_u8(slot_off + layout::SLOT_COMPACT_MARK);
            assert_eq!(mark, 0, "second compact should clear mark");
        }
    }

    #[test]
    fn test_ring_dequeue_recover_in_loop() {
        // Test the recover path in try_dequeue (line 211).
        // When seq < pos+1 and owner is dead, recover and continue.
        let cap = 4u32;
        let (_buf, region, ring, slab) = setup_ring(cap, 512);
        let pid = 1u32;
        let start_time = 0u64;
        let expiry = f64::MAX;

        unsafe {
            // Enqueue a message
            ring.try_enqueue(
                &region,
                &slab,
                b"ch",
                b"msg1",
                expiry,
                OwnerIdentity { pid, start_time },
            );

            // Simulate a crashed consumer: set owner on slot 0
            let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 999999);
            region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
            region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, 0);

            // Dequeue should detect the dead owner, recover the slot, and then
            // observe the recycled slot (seq = ticket + cap) as dequeuable.
            // The recovered slot holds stale payload; the test only exercises
            // the recover path inside try_dequeue without panicking/double-free.
            let _result =
                ring.try_dequeue(&region, &slab, f64::MAX, OwnerIdentity { pid, start_time });
        }
    }

    #[test]
    fn test_ring_mpmc_no_loss_no_dup() {
        // 2 producers + 2 consumers, each producer enqueues 50 messages.
        // Verify total dequeued == total enqueued, no message lost/duplicated.
        // Use the real process pid + starttime so pid_dead() returns false for
        // live owners (bogus PIDs would trigger spurious recovery → corruption).
        let (_buf, region, ring, slab) = setup_ring(256, 512);
        let (region_ptr, len) = region.ptr_and_len();
        let region_ptr = region_ptr as usize;
        let ring_offset = ring.ring_offset;
        let pool_offset = slab.pool_offset;
        let pool_size = slab.pool_size;
        let self_pid = std::process::id();
        let self_st = crate::layout::read_self_starttime();

        let enqueued = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
        let dequeued = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));

        let mut handles = vec![];
        // 2 producers
        for p in 0..2u8 {
            let enq = enqueued.clone();
            let h = std::thread::spawn(move || {
                let non_null = std::ptr::NonNull::new(region_ptr as *mut u8).unwrap();
                let region = unsafe { ShmRegion::new(non_null, len) };
                let slab = SlabAllocator::new(pool_offset, pool_size);
                let ring = Ring::new(ring_offset);
                for i in 0..50u8 {
                    let msg = [p, i];
                    loop {
                        let owner = OwnerIdentity {
                            pid: self_pid,
                            start_time: self_st,
                        };
                        if unsafe { ring.try_enqueue(&region, &slab, b"ch", &msg, f64::MAX, owner) }
                            == EnqueueResult::Ok
                        {
                            enq.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                            break;
                        }
                        std::thread::yield_now();
                    }
                }
            });
            handles.push(h);
        }
        // 2 consumers
        for _ in 0..2 {
            let deq = dequeued.clone();
            let enq = enqueued.clone();
            let h = std::thread::spawn(move || {
                let non_null = std::ptr::NonNull::new(region_ptr as *mut u8).unwrap();
                let region = unsafe { ShmRegion::new(non_null, len) };
                let slab = SlabAllocator::new(pool_offset, pool_size);
                let ring = Ring::new(ring_offset);
                loop {
                    let owner = OwnerIdentity {
                        pid: self_pid,
                        start_time: self_st,
                    };
                    match unsafe { ring.try_dequeue(&region, &slab, f64::MAX, owner) } {
                        Some(_) => {
                            deq.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                        }
                        None => {
                            // Stop once all produced messages have been consumed.
                            let done = enq.load(std::sync::atomic::Ordering::Relaxed) >= 100
                                && deq.load(std::sync::atomic::Ordering::Relaxed)
                                    >= enq.load(std::sync::atomic::Ordering::Relaxed);
                            if done {
                                break;
                            }
                            std::thread::yield_now();
                        }
                    }
                }
            });
            handles.push(h);
        }
        for h in handles {
            h.join().unwrap();
        }
        assert_eq!(enqueued.load(std::sync::atomic::Ordering::Relaxed), 100);
        assert_eq!(dequeued.load(std::sync::atomic::Ordering::Relaxed), 100);
    }

    #[test]
    fn test_recover_cas_no_double_free() {
        // A single dead slot (owner=dead pid, seq behind, overflow_off=valid).
        // Multiple threads recover it simultaneously → all hit recover's CAS.
        // Verify the slab free-list has NO duplicate (the freed overflow
        // offset appears at most once).
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let slot_off = ring.ring_offset + layout::RING_HEADER_SIZE;

        unsafe {
            // Allocate an overflow page, attach to slot as a dead slot's overflow.
            let overflow = slab.alloc(&region, 100);
            assert!(overflow != 0);
            region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, overflow);
            region.write_u32(slot_off + layout::SLOT_MSG_LEN, 100);
            // Make slot a dead slot: seq behind, owner = dead pid 999999.
            region.store_u64(slot_off + layout::SLOT_SEQ, 0);
            region.write_u32(slot_off + layout::SLOT_OWNER_PID, 999999);
            region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);

            // Spawn 4 threads all calling recover_slot on the same dead slot.
            let (region_ptr, len) = region.ptr_and_len();
            let region_ptr = region_ptr as usize;
            let ring_offset = ring.ring_offset;
            let pool_offset = slab.pool_offset;
            let pool_size = slab.pool_size;
            let barrier = std::sync::Arc::new(std::sync::Barrier::new(4));
            let mut handles = vec![];
            for _ in 0..4u8 {
                let b = barrier.clone();
                let h = std::thread::spawn(move || {
                    let non_null = std::ptr::NonNull::new(region_ptr as *mut u8).unwrap();
                    let region = ShmRegion::new(non_null, len);
                    let slab = SlabAllocator::new(pool_offset, pool_size);
                    let ring = Ring::new(ring_offset);
                    b.wait();
                    // SAFETY: dead_owner_pid = 999999 (confirmed dead, matches slot).
                    ring.recover_slot(
                        &region,
                        &slab,
                        ring_offset + layout::RING_HEADER_SIZE,
                        1,
                        999999,
                    );
                });
                handles.push(h);
            }
            for h in handles {
                h.join().unwrap();
            }

            // Verify no double-free: walk the 512-class free-list, ensure no duplicate offset.
            // The dead slot's overflow (512-class) should be freed exactly once.
            let free_head_off = slab.free_heads_offset_for_test();
            let mut seen = std::collections::HashSet::new();
            let mut cur = region.load_u64(free_head_off);
            while cur != 0 {
                assert!(
                    seen.insert(cur),
                    "double-free detected: offset {cur} appears twice in free-list"
                );
                cur = region.load_u64(cur as usize);
            }
            // The overflow page must have been freed (present in free-list).
            assert!(
                seen.contains(&overflow),
                "overflow page {overflow} should be in free-list after recovery"
            );
        }
    }

    #[test]
    fn test_ring_full_does_not_advance_enqueue_pos() {
        // The core CAS-claim invariant: a Full return consumed no ticket.
        // Under the old fetch_add protocol every Full burned a ticket, and
        // cap burnt tickets killed the whole ring (head-of-line block on the
        // consumer, permanent ChannelFull even after a drain).
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let owner = OwnerIdentity {
            pid: 1,
            start_time: 0,
        };
        unsafe {
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"a", f64::MAX, owner),
                EnqueueResult::Ok
            );
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"b", f64::MAX, owner),
                EnqueueResult::Ok
            );
            let enq_off = ring.ring_offset + layout::RING_ENQUEUE_POS;
            for _ in 0..8 {
                assert_eq!(
                    ring.try_enqueue(&region, &slab, b"ch", b"x", f64::MAX, owner),
                    EnqueueResult::Full
                );
                assert_eq!(region.load_u64(enq_off), 2, "Full must not burn a ticket");
            }
            // Drain → the very next send lands on ticket 2 and succeeds.
            assert!(ring.try_dequeue(&region, &slab, f64::MAX, owner).is_some());
            assert!(ring.try_dequeue(&region, &slab, f64::MAX, owner).is_some());
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"c", f64::MAX, owner),
                EnqueueResult::Ok
            );
            assert_eq!(region.load_u64(enq_off), 3);
        }
    }

    #[test]
    fn test_ring_enqueue_recovers_dead_owner_then_claims() {
        // A crashed previous-round owner on the slot the next ticket targets:
        // enqueue sees seq < pos with a dead owner, repairs the slot (seq
        // jumps one full round), and claims the repaired ticket in the same
        // call. Under the old code the enqueue's own owner write clobbered
        // the dead owner before the check, making this path unreachable.
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let owner = OwnerIdentity {
            pid: 1,
            start_time: 0,
        };
        unsafe {
            // Simulate: ticket 0 claimed by a process that crashed before
            // publishing; ticket 1 already claimed elsewhere; enq = 2.
            region.store_u64(ring.ring_offset + layout::RING_ENQUEUE_POS, 2);
            let slot0 = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.write_u32(slot0 + layout::SLOT_OWNER_PID, 999999);
            region.store_u64(slot0 + layout::SLOT_OWNER_TICKET, 0);

            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"m", f64::MAX, owner),
                EnqueueResult::Ok
            );
            assert_eq!(region.load_u64(slot0 + layout::SLOT_SEQ), 3); // published 2+1
            assert_eq!(region.read_u32(slot0 + layout::SLOT_OWNER_PID), 0);
        }
    }

    #[test]
    fn test_ring_enqueue_live_owner_returns_full() {
        // seq < pos with a LIVE owner (a consumer mid-copy of the previous
        // round): no recovery, just Full — and no ticket consumed.
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let owner = OwnerIdentity {
            pid: std::process::id(),
            start_time: crate::layout::read_self_starttime(),
        };
        unsafe {
            region.store_u64(ring.ring_offset + layout::RING_ENQUEUE_POS, 2);
            let slot0 = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.store_u64(slot0 + layout::SLOT_SEQ, 0);
            region.write_u32(slot0 + layout::SLOT_OWNER_PID, owner.pid);
            region.store_u64(slot0 + layout::SLOT_OWNER_TICKET, 0);
            region.store_u64(slot0 + layout::SLOT_OWNER_START_TIME, owner.start_time);

            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"m", f64::MAX, owner),
                EnqueueResult::Full
            );
            assert_eq!(
                region.load_u64(ring.ring_offset + layout::RING_ENQUEUE_POS),
                2
            );
        }
    }

    #[test]
    fn test_ring_recovering_sentinel_parks_not_recovers() {
        // A slot parked in SLOT_RECOVERING (repair in progress) must NOT be
        // "recovered": pid_dead(u32::MAX) used to return true (kill(-1,0)
        // broadcast "succeeds" + /proc/4294967295 missing), which turned
        // recover's CAS into a no-op (RECOVERING→RECOVERING) and admitted a
        // second repairer (deterministic overflow double-free). Now: enqueue
        // → Full, dequeue → None, and compact finishes the parked repair.
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let owner = OwnerIdentity {
            pid: 1,
            start_time: 0,
        };
        unsafe {
            region.store_u64(ring.ring_offset + layout::RING_ENQUEUE_POS, 2);
            let slot0 = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.store_u64(slot0 + layout::SLOT_SEQ, 0);
            region.write_u32(slot0 + layout::SLOT_OWNER_PID, layout::SLOT_RECOVERING);
            region.store_u64(slot0 + layout::SLOT_OWNER_TICKET, 0);

            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"m", f64::MAX, owner),
                EnqueueResult::Full
            );
            assert!(ring.try_dequeue(&region, &slab, f64::MAX, owner).is_none());

            // compact two-pass takes over the parked repair (idempotent body).
            ring.compact(&region, &slab, 0);
            assert_eq!(region.read_u8(slot0 + layout::SLOT_COMPACT_MARK), 1);
            ring.compact(&region, &slab, 0);
            assert_eq!(region.load_u64(slot0 + layout::SLOT_SEQ), 2); // ticket+cap
            assert_eq!(region.read_u32(slot0 + layout::SLOT_OWNER_PID), 0);
            // The ring is alive again: ticket 2 enqueues.
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"m", f64::MAX, owner),
                EnqueueResult::Ok
            );
        }
    }

    #[test]
    fn test_ring_compact_finishes_crashed_recoverer_exactly_once() {
        // A recoverer that died mid-repair leaves owner=SLOT_RECOVERING.
        // compact's two-pass takeover must release the overflow page EXACTLY
        // once (claim-then-free; the old free-then-zero order double-freed on
        // re-entry and could self-ring the free-list).
        let (_buf, region, ring, slab) = setup_ring(2, 16);
        unsafe {
            let slot0 = ring.ring_offset + layout::RING_HEADER_SIZE;
            let overflow = slab.alloc(&region, 100);
            assert!(overflow != 0);
            region.store_u64(ring.ring_offset + layout::RING_ENQUEUE_POS, 2);
            region.store_u64(slot0 + layout::SLOT_SEQ, 0);
            region.write_u32(slot0 + layout::SLOT_OWNER_PID, layout::SLOT_RECOVERING);
            region.store_u64(slot0 + layout::SLOT_OWNER_TICKET, 0);
            region.store_u64(slot0 + layout::SLOT_OVERFLOW_OFF, overflow);
            region.write_u32(slot0 + layout::SLOT_MSG_LEN, 100);

            // Two passes mark + confirm; two more passes must NOT re-repair.
            ring.compact(&region, &slab, 0);
            ring.compact(&region, &slab, 0);
            ring.compact(&region, &slab, 0);
            ring.compact(&region, &slab, 0);
            assert_eq!(region.load_u64(slot0 + layout::SLOT_SEQ), 2);
            assert_eq!(region.read_u32(slot0 + layout::SLOT_OWNER_PID), 0);

            // Free-list: the page appears exactly once, no self-ring.
            let free_head_off = slab.free_heads_offset_for_test();
            let mut seen = std::collections::HashSet::new();
            let mut cur = region.load_u64(free_head_off);
            let mut count = 0;
            while cur != 0 {
                assert!(seen.insert(cur), "double-free: offset {cur} twice");
                cur = region.load_u64(cur as usize);
                count += 1;
                assert!(count < 64, "free-list self-ring detected");
            }
            assert!(seen.contains(&overflow));
            assert_eq!(region.load_u64(slot0 + layout::SLOT_OVERFLOW_OFF), 0);
        }
    }

    #[test]
    fn test_ring_compact_never_drops_live_messages() {
        // Regression (compact misjudging live messages): burnt tickets used
        // to inflate enqueue_pos past deq+cap, making compact's condition (a)
        // match live unconsumed messages and delete them after two passes.
        // With honest counters (Full never advances enqueue_pos) a live
        // message's seq always exceeds max_pos - cap, so it cannot match.
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let owner = OwnerIdentity {
            pid: 1,
            start_time: 0,
        };
        unsafe {
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"m1", f64::MAX, owner),
                EnqueueResult::Ok
            );
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"m2", f64::MAX, owner),
                EnqueueResult::Ok
            );
            // The old pathology needed burnt tickets to advance the frontier
            // past deq+cap; now they cannot move positions at all.
            for _ in 0..10 {
                assert_eq!(
                    ring.try_enqueue(&region, &slab, b"ch", b"x", f64::MAX, owner),
                    EnqueueResult::Full
                );
            }
            ring.compact(&region, &slab, 0);
            ring.compact(&region, &slab, 0);

            let (ch, d) = ring.try_dequeue(&region, &slab, f64::MAX, owner).unwrap();
            assert_eq!(
                (ch.as_slice(), d.as_slice()),
                (b"ch".as_slice(), b"m1".as_slice())
            );
            let (ch, d) = ring.try_dequeue(&region, &slab, f64::MAX, owner).unwrap();
            assert_eq!(
                (ch.as_slice(), d.as_slice()),
                (b"ch".as_slice(), b"m2".as_slice())
            );
            assert!(ring.try_dequeue(&region, &slab, f64::MAX, owner).is_none());
        }
    }

    #[test]
    fn test_ring_enqueue_seq_ahead_returns_full_bounded() {
        // seq > pos is only reachable via a compact repair racing an in-flight
        // producer; the enqueue must give up as Full after a bounded spin —
        // never spin forever while pyo3 holds the GIL (whole-process freeze).
        let (_buf, region, ring, slab) = setup_ring(2, 512);
        let owner = OwnerIdentity {
            pid: 1,
            start_time: 0,
        };
        unsafe {
            let slot0 = ring.ring_offset + layout::RING_HEADER_SIZE;
            region.store_u64(slot0 + layout::SLOT_SEQ, 1_000);

            // Thread + timeout so an unbounded-spin regression fails fast.
            use std::sync::mpsc;
            let (tx, rx) = mpsc::channel();
            let handle = std::thread::spawn(move || {
                let r = ring.try_enqueue(&region, &slab, b"ch", b"m", f64::MAX, owner);
                let _ = tx.send(r);
            });
            match rx.recv_timeout(std::time::Duration::from_secs(2)) {
                Ok(r) => assert_eq!(r, EnqueueResult::Full),
                Err(_) => panic!("enqueue spins forever on seq > pos"),
            }
            let _ = handle.join();
        }
    }

    #[test]
    fn test_ring_skip_tombstone_keeps_ring_alive() {
        // Slab exhaustion must not leave a dead slot: the claimed round is
        // recycled with a SKIP tombstone, the caller still sees Full, and
        // later messages enqueue on subsequent tickets. Under the old code
        // one exhaustion killed a cap=1 ring permanently and degraded larger
        // rings by one slot per event.
        let ring_size = layout::RING_HEADER_SIZE + 4 * layout::SLOT_SIZE;
        // Metadata (12 classes x 16B + bump) + exactly ONE 512-class block.
        let pool_size = layout::SIZE_CLASSES.len() * 16 + 8 + 512;
        let total_size = layout::HDR_SIZE + ring_size + pool_size;
        let (_buf, region) = make_region(total_size);
        unsafe {
            region.write_u32(layout::HDR_INLINE_SIZE, 16);
            let slab = SlabAllocator::new(layout::HDR_SIZE + ring_size, pool_size);
            slab.init(&region);
            let ring = Ring::new(layout::HDR_SIZE);
            ring.init(&region, 4);
            let owner = OwnerIdentity {
                pid: 1,
                start_time: 0,
            };

            let big = vec![0xABu8; 100]; // > inline 16 → 512-class overflow
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", &big, f64::MAX, owner),
                EnqueueResult::Ok
            );
            // Pool exhausted → SKIP tombstone + Full; the ticket is spent on
            // the tombstone (enqueue_pos advances by exactly 1).
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", &big, f64::MAX, owner),
                EnqueueResult::Full
            );
            assert_eq!(
                region.load_u64(ring.ring_offset + layout::RING_ENQUEUE_POS),
                2
            );

            // The ring is NOT dead: an inline message takes the next ticket…
            assert_eq!(
                ring.try_enqueue(&region, &slab, b"ch", b"m", f64::MAX, owner),
                EnqueueResult::Ok
            );
            // …and the consumer sees exactly the two live messages, in FIFO
            // order, with the skipped round protocol-jumped (no garbage).
            let (ch, d) = ring.try_dequeue(&region, &slab, f64::MAX, owner).unwrap();
            assert_eq!(
                (ch.as_slice(), d.as_slice()),
                (b"ch".as_slice(), big.as_slice())
            );
            let (ch, d) = ring.try_dequeue(&region, &slab, f64::MAX, owner).unwrap();
            assert_eq!(
                (ch.as_slice(), d.as_slice()),
                (b"ch".as_slice(), b"m".as_slice())
            );
            assert!(ring.try_dequeue(&region, &slab, f64::MAX, owner).is_none());
        }
    }

    #[test]
    fn test_ring_reset_bumps_generation() {
        let (_buf, region, ring, _slab) = setup_ring(2, 512);
        unsafe {
            let gen_off = ring.ring_offset + layout::RING_GENERATION;
            assert_eq!(region.load_u64(gen_off), 0);
            ring.reset(&region);
            assert_eq!(region.load_u64(gen_off), 1);
            ring.reset(&region);
            assert_eq!(region.load_u64(gen_off), 2);
        }
    }

    #[test]
    fn test_ring_flush_race_stress_no_wedge() {
        // Producers + a reset storm: with the fence, raced operations retract
        // instead of publishing into the wiped pool. The invariants asserted
        // are the ones the fence guarantees outright — no hang (bounded
        // spins), no crash, and full functionality after the storm.
        let (_buf, region, ring, slab) = setup_ring(8, 512);
        let (region_ptr, len) = region.ptr_and_len();
        let region_ptr = region_ptr as usize;
        let ring_offset = ring.ring_offset;
        let pool_offset = slab.pool_offset;
        let pool_size = slab.pool_size;

        use std::sync::atomic::{AtomicBool, Ordering};
        use std::sync::Arc;
        let producer_done = Arc::new(AtomicBool::new(false));

        let producer = {
            let done = producer_done.clone();
            std::thread::spawn(move || {
                let non_null = std::ptr::NonNull::new(region_ptr as *mut u8).unwrap();
                let region = unsafe { ShmRegion::new(non_null, len) };
                let slab = SlabAllocator::new(pool_offset, pool_size);
                let ring = Ring::new(ring_offset);
                let owner = OwnerIdentity {
                    pid: 1,
                    start_time: 0,
                };
                let mut sent = 0;
                while sent < 50 {
                    if unsafe { ring.try_enqueue(&region, &slab, b"ch", b"m", f64::MAX, owner) }
                        == EnqueueResult::Ok
                    {
                        sent += 1;
                    }
                }
                done.store(true, Ordering::Release);
            })
        };
        let storm = std::thread::spawn(move || {
            let non_null = std::ptr::NonNull::new(region_ptr as *mut u8).unwrap();
            let region = unsafe { ShmRegion::new(non_null, len) };
            let ring = Ring::new(ring_offset);
            for _ in 0..200 {
                unsafe { ring.reset(&region) };
                std::thread::sleep(std::time::Duration::from_micros(500));
            }
        });

        // Consumer on this thread with a deadline: the fence's bounded spins
        // must keep every call short.
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(15);
        let owner = OwnerIdentity {
            pid: 1,
            start_time: 0,
        };
        let mut received = 0;
        while !producer_done.load(Ordering::Acquire) || received < 1 {
            assert!(
                std::time::Instant::now() < deadline,
                "flush-race stress wedged"
            );
            if unsafe { ring.try_dequeue(&region, &slab, f64::MAX, owner) }.is_some() {
                received += 1;
            }
        }
        storm.join().unwrap();
        producer.join().unwrap();
        assert!(received >= 1);

        // Fully functional after the storm.
        assert_eq!(
            unsafe { ring.try_enqueue(&region, &slab, b"ch", b"final", f64::MAX, owner) },
            EnqueueResult::Ok
        );
        let (_ch, data) = unsafe { ring.try_dequeue(&region, &slab, f64::MAX, owner) }
            .expect("roundtrip after storm");
        assert_eq!(data, b"final");
    }

    #[test]
    fn test_ring_flush_aliasing_fresh_round_intact() {
        // Cross-generation ticket aliasing, the exact F1' shape: a producer
        // parked mid-payload on a first-round ticket, a flush resetting the
        // ring, then a FRESH round re-claiming the same slot under the same
        // ticket number. The fence's unpublished-round retract must write
        // NOTHING (owner-CAS only) — the old retract zeroed the fresh round's
        // overflow pointer and tombstoned its seq, delivering inline garbage.
        // Each cycle aliases by construction: post-reset ticket 0 targets the
        // slot the parked producer still holds.
        let cap = 2u32;
        let inline = 16u32;
        // Pool: metadata + two 2MiB blocks (the parked producer's payload)
        // + slack for the fresh round's 512-class page.
        let pool_size = layout::SIZE_CLASSES.len() * 16 + 8 + 2 * (2 << 20) + 64 * 1024;
        let ring_size = layout::RING_HEADER_SIZE + cap as usize * layout::SLOT_SIZE;
        let total = layout::HDR_SIZE + ring_size + pool_size;
        let (_buf, region) = make_region(total);
        unsafe { region.write_u32(layout::HDR_INLINE_SIZE, inline) };
        let slab = SlabAllocator::new(layout::HDR_SIZE + ring_size, pool_size);
        slab.init(&region);
        let ring = Ring::new(layout::HDR_SIZE);
        // SAFETY: region is large enough.
        unsafe { ring.init(&region, cap) };
        let owner = OwnerIdentity {
            pid: 1,
            start_time: 0,
        };

        let big = vec![0xABu8; 2 << 20]; // > inline → 2MiB overflow class
        let fresh: Vec<u8> = (0..100u8).collect();

        for cycle in 0..5 {
            std::thread::scope(|scope| {
                let (region_ptr, len) = region.ptr_and_len();
                let region_ptr = region_ptr as usize;
                let ring_offset = ring.ring_offset;
                let pool_offset = slab.pool_offset;
                let pool_size2 = slab.pool_size;
                let big_slice: &[u8] = &big;
                let handle = scope.spawn(move || {
                    let non_null = std::ptr::NonNull::new(region_ptr as *mut u8).unwrap();
                    // SAFETY: same backing buffer, in bounds.
                    let region = unsafe { ShmRegion::new(non_null, len) };
                    let slab = SlabAllocator::new(pool_offset, pool_size2);
                    let ring = Ring::new(ring_offset);
                    // SAFETY: ring is initialized.
                    unsafe { ring.try_enqueue(&region, &slab, b"ch", big_slice, f64::MAX, owner) }
                });

                // Park the producer inside its ~2MiB payload copy, then flush
                // and immediately run a fresh round on the SAME slot
                // (post-reset ticket 0 == the parked producer's ticket 0).
                std::thread::sleep(std::time::Duration::from_micros(50 + cycle * 25));
                // SAFETY: ring is initialized; single flusher.
                unsafe { ring.reset(&region) };
                // SAFETY: ring is initialized.
                let fresh_res =
                    unsafe { ring.try_enqueue(&region, &slab, b"ch", &fresh, f64::MAX, owner) };
                assert_eq!(fresh_res, EnqueueResult::Ok, "cycle {cycle}: fresh enqueue");

                // Let the parked producer run its retract BEFORE the fresh
                // round is consumed — the retract must not tombstone a
                // published round: the fresh dequeue is REQUIRED to see it.
                // (Byte equality is not asserted: the parked producer's own
                // late payload stores can tear a re-claimed round — the
                // documented instructions-wide residual, caught at the
                // layer by the pump's unpack tolerance.)
                let _ = handle.join();

                let got = unsafe { ring.try_dequeue(&region, &slab, f64::MAX, owner) };
                assert!(
                    got.is_some(),
                    "cycle {cycle}: fresh published round vanished — retract tombstoned it"
                );
            });
        }
    }
}
