// Size-class slab allocator for shared memory dynamic pool.
//
// Two lock modes:
// - Global flock (cold path): ring/group structure allocation
// - Per-size-class atomic spinlock (hot path): overflow page alloc/free
//
// Free list: intrusive linked list using the first 8 bytes of each free block
// to store the offset of the next free block (0 = end of list).

use crate::layout;
use crate::region::ShmRegion;

/// Slab allocator operating on the dynamic pool region of shared memory.
pub struct SlabAllocator {
    /// Offset of the dynamic pool region within the shm.
    pub(crate) pool_offset: usize,
    /// Total size of the dynamic pool region.
    pub(crate) pool_size: usize,
    /// Offset of the per-size-class spinlocks within the pool.
    spinlocks_offset: usize,
    /// Number of size classes.
    num_classes: usize,
    /// Offset of the free-list heads (one u64 per size class).
    free_heads_offset: usize,
    /// Total size of the metadata area (spinlocks + free heads + bump pointer).
    metadata_size: usize,
}

impl SlabAllocator {
    /// Create a new SlabAllocator.
    ///
    /// The dynamic pool layout:
    /// [spinlocks (num_classes * 8)] [free_heads (num_classes * 8)] [bump_ptr (8)] [free blocks...]
    pub fn new(pool_offset: usize, pool_size: usize) -> Self {
        let num_classes = layout::SIZE_CLASSES.len();
        let spinlocks_offset = pool_offset;
        let free_heads_offset = spinlocks_offset + num_classes * layout::SLAB_SPINLOCK_SIZE;
        let bump_ptr_offset = free_heads_offset + num_classes * 8;
        let metadata_size = bump_ptr_offset + 8 - pool_offset;
        Self {
            pool_offset,
            pool_size,
            spinlocks_offset,
            num_classes,
            free_heads_offset,
            metadata_size,
        }
    }

    /// Initialize the slab allocator (first process only).
    /// Must be called under global flock.
    pub fn init(&self, region: &ShmRegion) {
        // Zero all spinlocks, free heads, and bump pointer
        for i in 0..self.num_classes {
            unsafe {
                region.store_u64(self.spinlocks_offset + i * 8, 0);
                region.store_u64(self.free_heads_offset + i * 8, 0);
            }
        }
        // Zero bump pointer
        unsafe {
            region.store_u64(self.bump_ptr_offset(), 0);
        }
    }

    /// Get the bump pointer offset.
    fn bump_ptr_offset(&self) -> usize {
        self.free_heads_offset + self.num_classes * 8
    }

    /// Get the data area start offset.
    fn data_start(&self) -> usize {
        self.pool_offset + self.metadata_size
    }

    /// Find the size class index for a given size.
    fn size_class_idx(&self, size: usize) -> Option<usize> {
        layout::SIZE_CLASSES.iter().position(|&sc| sc >= size)
    }

    /// Get the spinlock offset for a size class.
    fn spinlock_offset(&self, class_idx: usize) -> usize {
        self.spinlocks_offset + class_idx * 8
    }

    /// Get the free-list head offset for a size class.
    fn free_head_offset(&self, class_idx: usize) -> usize {
        self.free_heads_offset + class_idx * 8
    }

    /// Acquire the per-size-class spinlock.
    fn spinlock_acquire(&self, region: &ShmRegion, class_idx: usize) {
        let off = self.spinlock_offset(class_idx);
        loop {
            match unsafe { region.cas_u64(off, 0, 1) } {
                Ok(_) => return,
                Err(_) => std::thread::yield_now(),
            }
        }
    }

    /// Release the per-size-class spinlock.
    fn spinlock_release(&self, region: &ShmRegion, class_idx: usize) {
        let off = self.spinlock_offset(class_idx);
        unsafe {
            region.store_u64(off, 0);
        }
    }

    /// Allocate a block of at least `size` bytes from the dynamic pool.
    /// Returns the offset of the allocated block, or 0 on failure.
    /// Uses per-size-class spinlock (hot path for overflow pages).
    ///
    /// # Safety
    ///
    /// - `region` must point to a live, mapped shared-memory region whose slab
    ///   metadata matches this allocator (same pool_offset/pool_size).
    /// - The caller must ensure no other thread holds this class's spinlock
    ///   and that `size` is at most the largest size-class block.
    pub unsafe fn alloc(&self, region: &ShmRegion, size: usize) -> u64 {
        let class_idx = match self.size_class_idx(size) {
            Some(idx) => idx,
            None => return 0,
        };
        let block_size = layout::SIZE_CLASSES[class_idx];

        self.spinlock_acquire(region, class_idx);
        let result = self.alloc_from_class(region, class_idx, block_size);
        self.spinlock_release(region, class_idx);
        result
    }

    /// Allocate from a specific size class. Must be called with spinlock held.
    unsafe fn alloc_from_class(
        &self,
        region: &ShmRegion,
        class_idx: usize,
        block_size: usize,
    ) -> u64 {
        let head_off = self.free_head_offset(class_idx);
        let head = region.load_u64(head_off);

        if head != 0 {
            // Pop from free list
            let next = region.load_u64(head as usize);
            region.store_u64(head_off, next);
            return head;
        }

        // Free list empty: bump-allocate from the pool
        let bump_off = self.bump_ptr_offset();
        let current_bump = region.load_u64(bump_off);
        let data_start = self.data_start() as u64;
        let new_bump = if current_bump == 0 {
            data_start
        } else {
            current_bump
        };

        let alloc_offset = new_bump;
        let end = alloc_offset + block_size as u64;
        let pool_end = (self.pool_offset + self.pool_size) as u64;

        if end > pool_end {
            return 0; // Out of memory
        }

        region.store_u64(bump_off, end);
        alloc_offset
    }

    /// Free a block back to the slab pool.
    /// Uses per-size-class spinlock (hot path for overflow page deallocation).
    ///
    /// # Safety
    ///
    /// - `region` must match the region the block was allocated from.
    /// - `offset` must be a live block previously returned by `alloc`, must not
    ///   be freed twice, and `size` must round to the same size class it was
    ///   allocated with.
    pub unsafe fn free(&self, region: &ShmRegion, offset: u64, size: usize) {
        if offset == 0 {
            return;
        }
        let class_idx = match self.size_class_idx(size) {
            Some(idx) => idx,
            None => return,
        };

        self.spinlock_acquire(region, class_idx);
        // Push to front of free list
        let head_off = self.free_head_offset(class_idx);
        let old_head = region.load_u64(head_off);
        region.store_u64(offset as usize, old_head); // next pointer at start of block
        region.store_u64(head_off, offset);
        self.spinlock_release(region, class_idx);
    }

    /// Allocate a block using global flock (cold path).
    /// Caller is expected to hold the global flock.
    ///
    /// # Safety
    ///
    /// Same as [`SlabAllocator::alloc`], except the per-class spinlock is not
    /// taken: the caller must hold the global flock so no other process can be
    /// inside `alloc`/`free` on this pool concurrently.
    pub unsafe fn alloc_cold(&self, region: &ShmRegion, size: usize) -> u64 {
        let class_idx = match self.size_class_idx(size) {
            Some(idx) => idx,
            None => return 0,
        };
        let block_size = layout::SIZE_CLASSES[class_idx];
        self.alloc_from_class(region, class_idx, block_size)
    }

    /// Free a block using global flock (cold path).
    ///
    /// # Safety
    ///
    /// Same as [`SlabAllocator::free`], except the per-class spinlock is not
    /// taken: the caller must hold the global flock so no other process can be
    /// inside `alloc`/`free` on this pool concurrently.
    pub unsafe fn free_cold(&self, region: &ShmRegion, offset: u64, size: usize) {
        if offset == 0 {
            return;
        }
        let class_idx = match self.size_class_idx(size) {
            Some(idx) => idx,
            None => return,
        };
        let head_off = self.free_head_offset(class_idx);
        let old_head = region.load_u64(head_off);
        region.store_u64(offset as usize, old_head);
        region.store_u64(head_off, offset);
    }

    /// Reset the slab allocator (for flush).
    /// Must be called under global flock.
    pub fn reset(&self, region: &ShmRegion) {
        self.init(region);
    }

    /// Test-only accessor: offset of the free-list head for size class 0 (512).
    #[cfg(test)]
    pub(crate) fn free_heads_offset_for_test(&self) -> usize {
        self.free_head_offset(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::region::ShmRegion;
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

    #[test]
    fn test_slab_init_and_alloc() {
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        // Allocate a small block (fits in first size class = 512)
        // SAFETY: slab is initialized, region is valid.
        let off = unsafe { slab.alloc(&region, 100) };
        assert!(off != 0, "alloc should succeed");
    }

    #[test]
    fn test_slab_alloc_cold() {
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        // SAFETY: slab is initialized, region is valid.
        let off = unsafe { slab.alloc_cold(&region, 200) };
        assert!(off != 0, "alloc_cold should succeed");
    }

    #[test]
    fn test_slab_free_and_reuse() {
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        // Alloc, free, then alloc again — should reuse the freed block
        // SAFETY: slab is initialized, region is valid.
        unsafe {
            let off1 = slab.alloc(&region, 100);
            assert!(off1 != 0);
            slab.free(&region, off1, 100);
            let off2 = slab.alloc(&region, 100);
            assert_eq!(off1, off2, "should reuse freed block");
        }
    }

    #[test]
    fn test_slab_free_cold_and_reuse() {
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        // SAFETY: slab is initialized, region is valid.
        unsafe {
            let off1 = slab.alloc_cold(&region, 200);
            assert!(off1 != 0);
            slab.free_cold(&region, off1, 200);
            let off2 = slab.alloc_cold(&region, 200);
            assert_eq!(off1, off2, "should reuse freed block");
        }
    }

    #[test]
    fn test_slab_out_of_memory() {
        // Precise OOM: pool big enough for metadata + exactly N blocks of class 512.
        // metadata = num_classes spinlocks*8 + num_classes free_heads*8 + bump_ptr*8.
        // Derived from SIZE_CLASSES.len() so this test stays correct if classes
        // are added (e.g. the 262_144 class added for group-member arrays).
        let block_size = 512;
        let num_classes = layout::SIZE_CLASSES.len();
        let metadata = num_classes * 8 + num_classes * 8 + 8;
        let n = 3; // 3 blocks fit
        let pool_size = metadata + n * block_size;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        unsafe {
            // First N allocs succeed.
            for i in 0..n {
                let off = slab.alloc(&region, 100); // 100 → class 512
                assert!(off != 0, "alloc #{i} should succeed");
            }
            // (N+1)th alloc must OOM.
            let off = slab.alloc(&region, 100);
            assert_eq!(off, 0, "alloc #{} should OOM (return 0)", n + 1);
        }
    }

    #[test]
    fn test_slab_reset() {
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        // SAFETY: slab is initialized, region is valid.
        unsafe {
            let _ = slab.alloc(&region, 100);
        }
        // Reset should clear all state
        slab.reset(&region);
        // After reset, alloc should still work (bump pointer reset)
        // SAFETY: slab is re-initialized via reset.
        let off = unsafe { slab.alloc(&region, 100) };
        assert!(off != 0, "alloc should work after reset");
    }

    #[test]
    fn test_size_class_selection() {
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        // Different sizes should map to different size classes.
        // SAFETY: slab is initialized, region is valid.
        unsafe {
            // Size 100 → class 512 (index 0)
            let off1 = slab.alloc(&region, 100);
            assert!(off1 != 0);
            // Size 600 → class 2048 (index 1)
            let off2 = slab.alloc(&region, 600);
            assert!(off2 != 0);
            assert_ne!(
                off1, off2,
                "different size classes should have different offsets"
            );
        }
    }

    #[test]
    fn test_slab_alloc_too_large() {
        // Test alloc when size exceeds all size classes (line 116).
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        unsafe {
            // Size larger than max size class (16MB)
            let off = slab.alloc(&region, 16_777_217);
            assert_eq!(off, 0, "should return 0 for size exceeding max class");
        }
    }

    #[test]
    fn test_slab_spinlock_contention() {
        // Test spinlock contention path (line 97 - yield_now).
        // Use two threads competing for the same size class.
        let pool_size = 64 * 1024;
        let buf = vec![0u64; pool_size / 8];
        let ptr = buf.as_ptr() as *mut u8;
        let non_null = std::ptr::NonNull::new(ptr).unwrap();
        let region = unsafe { ShmRegion::new(non_null, pool_size) };
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        // Use raw pointers to share across threads (safe because ShmRegion is Send+Sync)
        let region_ptr = ptr as usize;
        let slab_ptr = &slab as *const SlabAllocator as usize;

        // Use a barrier to synchronize threads
        let barrier = std::sync::Arc::new(std::sync::Barrier::new(2));
        let barrier2 = barrier.clone();

        let handle = std::thread::spawn(move || {
            let region = unsafe {
                ShmRegion::new(
                    std::ptr::NonNull::new(region_ptr as *mut u8).unwrap(),
                    pool_size,
                )
            };
            let slab = unsafe { &*(slab_ptr as *const SlabAllocator) };
            barrier2.wait();
            for _ in 0..10 {
                let _off = unsafe { slab.alloc(&region, 100) };
            }
        });

        barrier.wait();
        for _ in 0..10 {
            let _off = unsafe { slab.alloc(&region, 100) };
        }

        handle.join().unwrap();
    }

    #[test]
    fn test_slab_free_zero_offset() {
        // Test free with offset == 0 (line 169).
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        unsafe {
            // Free with offset 0 should be a no-op
            slab.free(&region, 0, 100);
            // No crash
        }
    }

    #[test]
    fn test_slab_free_too_large() {
        // Test free when size exceeds all size classes (line 173).
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        unsafe {
            // Free with size larger than max size class should be a no-op
            slab.free(&region, 100, 16_777_217);
            // No crash
        }
    }

    #[test]
    fn test_slab_alloc_cold_too_large() {
        // Test alloc_cold when size exceeds all size classes (line 190).
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        unsafe {
            let off = slab.alloc_cold(&region, 16_777_217);
            assert_eq!(off, 0, "should return 0 for size exceeding max class");
        }
    }

    #[test]
    fn test_slab_free_cold_zero_offset() {
        // Test free_cold with offset == 0 (line 199).
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        unsafe {
            slab.free_cold(&region, 0, 100);
            // No crash
        }
    }

    #[test]
    fn test_slab_free_cold_too_large() {
        // Test free_cold when size exceeds all size classes (line 203).
        let pool_size = 64 * 1024;
        let (_buf, region) = make_region(pool_size);
        let slab = SlabAllocator::new(0, pool_size);
        slab.init(&region);

        unsafe {
            slab.free_cold(&region, 100, 16_777_217);
            // No crash
        }
    }
}
