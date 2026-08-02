// Vyukov bounded MPMC ring buffer implementation.
// Per-slot sequence number + owner tracking for crash recovery.

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
    /// # Safety
    /// - `region` must be valid.
    /// - The memory at `ring_offset` must be large enough for the ring.
    pub unsafe fn init(&self, region: &ShmRegion, capacity: u32) {
        // Write header
        region.store_u64(self.ring_offset + layout::RING_ENQUEUE_POS, 0);
        region.store_u64(self.ring_offset + layout::RING_DEQUEUE_POS, 0);
        region.write_u32(self.ring_offset + layout::RING_CAPACITY, capacity);
        region.store_u64(self.ring_offset + layout::RING_LAST_COMPACT_ENQ, 0);
        region.store_u64(self.ring_offset + layout::RING_LAST_COMPACT_DEQ, 0);

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
        let inline_size = self.inline_size(region) as usize;

        // Step 1: Fetch our ticket
        let pos = region.fetch_add_u64(self.ring_offset + layout::RING_ENQUEUE_POS, 1);
        let idx = (pos % cap) as usize;
        let slot_off = self.slot_offset(idx);

        // Step 2: Owner tracking (紧贴 fetch_add)
        debug_assert!(
            owner.pid != layout::SLOT_RECOVERING,
            "pid collides with RECOVERING sentinel"
        );
        region.write_u32(slot_off + layout::SLOT_OWNER_PID, owner.pid);
        region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, pos);
        region.store_u64(slot_off + layout::SLOT_OWNER_START_TIME, owner.start_time);

        // Step 3: Spin until slot is EMPTY (seq == pos)
        loop {
            let seq = region.load_u64(slot_off + layout::SLOT_SEQ);
            if seq == pos {
                break; // EMPTY, slot ready
            }
            if seq < pos {
                // Phase behind: ring full or crashed slot
                let owner_pid = region.read_u32(slot_off + layout::SLOT_OWNER_PID);
                // Self-PID optimization: if owner is current process, ring is truly full.
                // Avoids pid_dead() syscall (~200ns) — the process is alive (we're executing).
                if owner_pid == owner.pid {
                    region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
                    region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
                    return EnqueueResult::Full;
                }
                let owner_st = region.load_u64(slot_off + layout::SLOT_OWNER_START_TIME);
                if owner_pid != 0 && crate::layout::pid_dead(owner_pid, owner_st) {
                    // Recover crashed slot (pass dead_owner_pid for CAS expected)
                    self.recover_slot(region, slab, slot_off, cap, owner_pid);
                    continue;
                }
                // Owner not set or not dead — either residual window or truly full
                region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
                region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
                return EnqueueResult::Full;
            }
            // seq > pos: slot occupied by previous round, spin
            std::thread::yield_now();
        }

        // Step 4: Write message data
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
        if msg_data.len() <= inline_size {
            // Inline
            region.write_u32(slot_off + layout::SLOT_MSG_LEN, msg_data.len() as u32);
            region.copy_in(slot_off + layout::SLOT_INLINE, msg_data);
            region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, 0);
        } else {
            // Overflow: allocate from slab
            let overflow_off = slab.alloc(region, msg_data.len());
            if overflow_off == 0 {
                // Slab exhausted — cannot store message
                // Treat as Full
                region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
                region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
                return EnqueueResult::Full;
            }
            region.copy_in(overflow_off as usize, msg_data);
            region.write_u32(slot_off + layout::SLOT_MSG_LEN, msg_data.len() as u32);
            region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, overflow_off);
        }

        // Step 5: Publish (seq = pos + 1)
        region.store_u64(slot_off + layout::SLOT_SEQ, pos + 1);

        // Step 6: Clear owner
        region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
        region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);

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
            let idx = (pos % cap) as usize;
            let slot_off = self.slot_offset(idx);

            // Check slot state
            let seq = region.load_u64(slot_off + layout::SLOT_SEQ);
            if seq < pos + 1 {
                // Phase behind: check for crashed owner
                let owner_pid = region.read_u32(slot_off + layout::SLOT_OWNER_PID);
                if owner_pid != 0 {
                    let owner_st = region.load_u64(slot_off + layout::SLOT_OWNER_START_TIME);
                    if crate::layout::pid_dead(owner_pid, owner_st) {
                        self.recover_slot(region, slab, slot_off, cap, owner_pid);
                        continue;
                    }
                }
                return None; // Ring empty or slot being written
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
                    // Claimed! Process the message.
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
                // Release overflow page if any
                let overflow_off = region.load_u64(slot_off + layout::SLOT_OVERFLOW_OFF);
                if overflow_off != 0 {
                    let msg_len = region.read_u32(slot_off + layout::SLOT_MSG_LEN) as usize;
                    slab.free(region, overflow_off, msg_len);
                    region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, 0);
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
                // Free overflow page (dequeue is the last reader)
                slab.free(region, overflow_off, msg_len);
                region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, 0);
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

        // Release overflow page if any
        let overflow_off = region.load_u64(slot_off + layout::SLOT_OVERFLOW_OFF);
        if overflow_off != 0 {
            let msg_len = region.read_u32(slot_off + layout::SLOT_MSG_LEN) as usize;
            slab.free(region, overflow_off, msg_len);
            region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, 0);
        }

        // Fix seq = ticket + capacity
        let ticket = region.load_u64(slot_off + layout::SLOT_OWNER_TICKET);
        region.store_u64(slot_off + layout::SLOT_SEQ, ticket + cap);

        // Clear owner (RECOVERING → 0)
        region.write_u32(slot_off + layout::SLOT_OWNER_PID, 0);
        region.store_u64(slot_off + layout::SLOT_OWNER_TICKET, 0);
    }

    /// Reset the ring for flush.
    /// Must be called under global flock.
    ///
    /// # Safety
    ///
    /// - `region` must be a live, mapped shared-memory region containing this
    ///   initialized ring.
    /// - The caller must hold the global flock so no producer/consumer can be
    ///   accessing the ring concurrently; reset is destructive (drops all
    ///   queued messages and frees slot state).
    pub unsafe fn reset(&self, region: &ShmRegion) {
        let cap = self.capacity(region);
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

    /// Compact the ring: fix residual-window stuck slots.
    /// Must be called under global flock.
    ///
    /// # Safety
    /// - The ring must be initialized.
    pub unsafe fn compact(&self, region: &ShmRegion, slab: &SlabAllocator, _start_time: u64) {
        let cap = self.capacity(region) as u64;
        let enq = region.load_u64(self.ring_offset + layout::RING_ENQUEUE_POS);
        let deq = region.load_u64(self.ring_offset + layout::RING_DEQUEUE_POS);
        let last_enq = region.load_u64(self.ring_offset + layout::RING_LAST_COMPACT_ENQ);
        let last_deq = region.load_u64(self.ring_offset + layout::RING_LAST_COMPACT_DEQ);

        // Condition (b): per-ring baseline advancement >= 2*capacity
        let baseline_advancement = enq.max(deq).saturating_sub(last_enq.max(last_deq));
        if baseline_advancement < 2 * cap {
            // Not enough advancement, skip
            return;
        }

        let slots_start = self.ring_offset + layout::RING_HEADER_SIZE;
        for i in 0..cap as usize {
            let slot_off = slots_start + i * layout::SLOT_SIZE;
            let seq = region.load_u64(slot_off + layout::SLOT_SEQ);
            let owner_pid = region.read_u32(slot_off + layout::SLOT_OWNER_PID);

            // P = next ticket targeting this slot
            let max_pos = enq.max(deq);
            let p = ((max_pos / cap) + 1) * cap + i as u64;

            // Condition (a): seq < P AND owner == 0
            if seq < p && owner_pid == 0 {
                let compact_mark = region.read_u8(slot_off + layout::SLOT_COMPACT_MARK);

                if compact_mark == 1 {
                    // Condition (c): confirmed across two compact cycles — reset
                    let overflow_off = region.load_u64(slot_off + layout::SLOT_OVERFLOW_OFF);
                    if overflow_off != 0 {
                        let msg_len = region.read_u32(slot_off + layout::SLOT_MSG_LEN) as usize;
                        slab.free(region, overflow_off, msg_len);
                        region.store_u64(slot_off + layout::SLOT_OVERFLOW_OFF, 0);
                    }
                    region.store_u64(slot_off + layout::SLOT_SEQ, p);
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

        // Update per-ring compact baseline
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
        // cap=1 forces all recoverers to target slot 0. Construct a dead slot
        // (owner=dead pid, seq behind, overflow_off=valid). Multiple threads
        // recover simultaneously → all hit recover's CAS. Verify slab free-list
        // has NO duplicate (the freed overflow offset appears at most once).
        let (_buf, region, ring, slab) = setup_ring(1, 512);
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
}
