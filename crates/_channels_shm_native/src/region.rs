// AtomicU64 operations on mmap'd shared memory regions.
// All operations use appropriate orderings for cross-process correctness.

use std::ptr::NonNull;
use std::sync::atomic::{AtomicU64, Ordering};

/// A borrowed reference to a shared memory region.
/// Lifetime `'a` is tied to the mmap owner, preventing use-after-munmap.
pub struct ShmRegion {
    base: NonNull<u8>,
    len: usize,
}

// SAFETY: ShmRegion is a thin wrapper around a pointer to shared memory.
// The shared memory is mapped MAP_SHARED and persists across processes.
// Atomic operations on the memory are safe to perform from any thread.
unsafe impl Send for ShmRegion {}
unsafe impl Sync for ShmRegion {}

impl ShmRegion {
    /// Create a new ShmRegion from a raw pointer and length.
    ///
    /// # Safety
    /// - `base` must point to a valid mmap'd region of at least `len` bytes.
    /// - The region must remain valid for the lifetime of this struct.
    pub unsafe fn new(base: NonNull<u8>, len: usize) -> Self {
        Self { base, len }
    }

    /// Get the base pointer and length.
    pub fn ptr_and_len(&self) -> (*mut u8, usize) {
        (self.base.as_ptr(), self.len)
    }

    /// Get a reference to an AtomicU64 at the given offset.
    ///
    /// # Safety
    /// - `offset + 8 <= self.len`
    /// - `offset` must be 8-byte aligned.
    /// - The field at `offset` must be initialized.
    #[inline]
    unsafe fn atomic_at(&self, offset: usize) -> &AtomicU64 {
        debug_assert!(offset + 8 <= self.len, "atomic access out of bounds");
        debug_assert!(offset % 8 == 0, "atomic access not aligned");
        &*(self.base.as_ptr().add(offset) as *const AtomicU64)
    }

    /// Load a u64 at `offset` with Acquire ordering.
    ///
    /// # Safety
    /// - `offset + 8 <= self.len`, `offset` 8-byte aligned, field initialized.
    #[inline]
    pub unsafe fn load_u64(&self, offset: usize) -> u64 {
        self.atomic_at(offset).load(Ordering::Acquire)
    }

    /// Store a u64 at `offset` with Release ordering.
    ///
    /// # Safety
    /// - `offset + 8 <= self.len`, `offset` 8-byte aligned.
    #[inline]
    pub unsafe fn store_u64(&self, offset: usize, value: u64) {
        self.atomic_at(offset).store(value, Ordering::Release);
    }

    /// Compare-and-swap at `offset`. Returns Ok(desired) on success, Err(actual) on failure.
    ///
    /// # Safety
    /// - `offset + 8 <= self.len`, `offset` 8-byte aligned, field initialized.
    #[inline]
    pub unsafe fn cas_u64(&self, offset: usize, expected: u64, desired: u64) -> Result<u64, u64> {
        self.atomic_at(offset).compare_exchange(
            expected,
            desired,
            Ordering::AcqRel,
            Ordering::Acquire,
        )
    }

    /// Fetch-add at `offset`. Returns the previous value.
    ///
    /// # Safety
    /// - `offset + 8 <= self.len`, `offset` 8-byte aligned, field initialized.
    #[inline]
    pub unsafe fn fetch_add_u64(&self, offset: usize, delta: u64) -> u64 {
        self.atomic_at(offset).fetch_add(delta, Ordering::AcqRel)
    }

    /// Copy `data` into the region at `offset`.
    ///
    /// # Safety
    /// - `offset + data.len() <= self.len`
    pub unsafe fn copy_in(&self, offset: usize, data: &[u8]) {
        debug_assert!(offset + data.len() <= self.len, "copy_in out of bounds");
        std::ptr::copy_nonoverlapping(data.as_ptr(), self.base.as_ptr().add(offset), data.len());
    }

    /// Copy `length` bytes from the region at `offset` into a new Vec.
    ///
    /// # Safety
    /// - `offset + length <= self.len`
    pub unsafe fn copy_out(&self, offset: usize, length: usize) -> Vec<u8> {
        debug_assert!(offset + length <= self.len, "copy_out out of bounds");
        let mut buf = vec![0u8; length];
        std::ptr::copy_nonoverlapping(self.base.as_ptr().add(offset), buf.as_mut_ptr(), length);
        buf
    }

    /// Read a u32 at `offset` (non-atomic, for config reads).
    ///
    /// # Safety
    /// - `offset + 4 <= self.len`
    pub unsafe fn read_u32(&self, offset: usize) -> u32 {
        debug_assert!(offset + 4 <= self.len);
        (self.base.as_ptr().add(offset) as *const u32).read_unaligned()
    }

    /// Write a u32 at `offset` (non-atomic, for init).
    ///
    /// # Safety
    /// - `offset + 4 <= self.len`
    pub unsafe fn write_u32(&self, offset: usize, value: u32) {
        debug_assert!(offset + 4 <= self.len);
        (self.base.as_ptr().add(offset) as *mut u32).write_unaligned(value);
    }

    /// Store a u32 at `offset` with Release ordering (for cross-process publish).
    ///
    /// Use this for fields that publish readiness across processes (e.g. magic).
    /// For flock-serialized config writes, prefer `write_u32` (non-atomic, faster).
    ///
    /// # Safety
    /// - `offset + 4 <= self.len`
    /// - `offset` must be 4-byte aligned (AtomicU32 requirement).
    #[inline]
    pub unsafe fn store_u32(&self, offset: usize, value: u32) {
        debug_assert!(offset + 4 <= self.len);
        debug_assert!(offset % 4 == 0, "store_u32 not aligned");
        (*(self.base.as_ptr().add(offset) as *mut std::sync::atomic::AtomicU32))
            .store(value, std::sync::atomic::Ordering::Release);
    }

    /// Load a u32 at `offset` with Acquire ordering (paired with store_u32).
    ///
    /// # Safety
    /// - `offset + 4 <= self.len`
    /// - `offset` must be 4-byte aligned.
    #[inline]
    pub unsafe fn load_u32(&self, offset: usize) -> u32 {
        debug_assert!(offset + 4 <= self.len);
        debug_assert!(offset % 4 == 0, "load_u32 not aligned");
        (*(self.base.as_ptr().add(offset) as *const std::sync::atomic::AtomicU32))
            .load(std::sync::atomic::Ordering::Acquire)
    }

    /// Read a u16 at `offset`.
    ///
    /// # Safety
    /// - `offset + 2 <= self.len`
    pub unsafe fn read_u16(&self, offset: usize) -> u16 {
        debug_assert!(offset + 2 <= self.len);
        (self.base.as_ptr().add(offset) as *const u16).read_unaligned()
    }

    /// Write a u16 at `offset`.
    ///
    /// # Safety
    /// - `offset + 2 <= self.len`
    pub unsafe fn write_u16(&self, offset: usize, value: u16) {
        debug_assert!(offset + 2 <= self.len);
        (self.base.as_ptr().add(offset) as *mut u16).write_unaligned(value);
    }

    /// Read a u8 at `offset`.
    ///
    /// # Safety
    /// - `offset + 1 <= self.len`
    pub unsafe fn read_u8(&self, offset: usize) -> u8 {
        debug_assert!(offset < self.len);
        self.base.as_ptr().add(offset).read()
    }

    /// Write a u8 at `offset`.
    ///
    /// # Safety
    /// - `offset + 1 <= self.len`
    pub unsafe fn write_u8(&self, offset: usize, value: u8) {
        debug_assert!(offset < self.len);
        self.base.as_ptr().add(offset).write(value);
    }

    /// Read bytes into a slice at `offset`.
    ///
    /// # Safety
    /// - `offset + dest.len() <= self.len`
    pub unsafe fn read_bytes(&self, offset: usize, dest: &mut [u8]) {
        debug_assert!(offset + dest.len() <= self.len);
        std::ptr::copy_nonoverlapping(
            self.base.as_ptr().add(offset),
            dest.as_mut_ptr(),
            dest.len(),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Create a zeroed 8-byte-aligned region backed by a Vec<u64>.
    /// The Vec must stay alive as long as the region is used.
    fn make_region(size: usize) -> (Vec<u64>, ShmRegion) {
        let words = size.div_ceil(8);
        let buf = vec![0u64; words];
        let ptr = buf.as_ptr() as *mut u8;
        let non_null = NonNull::new(ptr).unwrap();
        // SAFETY: buf is 8-byte aligned (Vec<u64>), valid for `size` bytes.
        let region = unsafe { ShmRegion::new(non_null, size) };
        (buf, region)
    }

    #[test]
    fn test_load_store_u64() {
        let (_buf, region) = make_region(64);
        // SAFETY: offsets are 8-byte aligned and within bounds.
        unsafe {
            region.store_u64(0, 42);
            assert_eq!(region.load_u64(0), 42);
            region.store_u64(8, 0xDEAD_BEEF_CAFE_BABE);
            assert_eq!(region.load_u64(8), 0xDEAD_BEEF_CAFE_BABE);
            region.store_u64(16, u64::MAX);
            assert_eq!(region.load_u64(16), u64::MAX);
        }
    }

    #[test]
    fn test_cas_u64() {
        let (_buf, region) = make_region(64);
        // SAFETY: offsets are 8-byte aligned and within bounds.
        unsafe {
            region.store_u64(0, 10);
            // CAS success
            let result = region.cas_u64(0, 10, 20);
            assert!(result.is_ok());
            assert_eq!(region.load_u64(0), 20);
            // CAS failure (expected mismatch)
            let result = region.cas_u64(0, 10, 30);
            assert!(result.is_err());
            assert_eq!(result.unwrap_err(), 20);
            assert_eq!(region.load_u64(0), 20);
        }
    }

    #[test]
    fn test_fetch_add_u64() {
        let (_buf, region) = make_region(64);
        // SAFETY: offsets are 8-byte aligned and within bounds.
        unsafe {
            region.store_u64(0, 100);
            assert_eq!(region.fetch_add_u64(0, 1), 100);
            assert_eq!(region.load_u64(0), 101);
            assert_eq!(region.fetch_add_u64(0, 10), 101);
            assert_eq!(region.load_u64(0), 111);
            // Overflow wraps
            region.store_u64(8, u64::MAX - 5);
            assert_eq!(region.fetch_add_u64(8, 10), u64::MAX - 5);
            assert_eq!(region.load_u64(8), 4); // wrapped
        }
    }

    #[test]
    fn test_copy_in_out() {
        let (_buf, region) = make_region(256);
        let data = b"hello world";
        // SAFETY: offset + data.len() <= 256.
        unsafe {
            region.copy_in(0, data);
            let out = region.copy_out(0, data.len());
            assert_eq!(&out, data);
            // Non-overlapping copy at different offset
            region.copy_in(64, b"foobar");
            assert_eq!(&region.copy_out(64, 6), b"foobar");
            // Original data at 0 is still intact
            assert_eq!(&region.copy_out(0, data.len()), data);
        }
    }

    #[test]
    fn test_read_write_u32_u16_u8() {
        let (_buf, region) = make_region(64);
        // SAFETY: offsets within bounds, properly sized.
        unsafe {
            region.write_u32(0, 0x1234_5678);
            assert_eq!(region.read_u32(0), 0x1234_5678);
            region.write_u16(4, 0xABCD);
            assert_eq!(region.read_u16(4), 0xABCD);
            region.write_u8(6, 0xFF);
            assert_eq!(region.read_u8(6), 0xFF);
        }
    }

    #[test]
    fn test_read_bytes() {
        let (_buf, region) = make_region(128);
        let data = b"test data";
        // SAFETY: offset + dest.len() <= 128.
        unsafe {
            region.copy_in(0, data);
            let mut buf = [0u8; 9];
            region.read_bytes(0, &mut buf);
            assert_eq!(&buf, data);
        }
    }

    #[test]
    fn test_ptr_and_len() {
        let (_buf, region) = make_region(128);
        let (ptr, len) = region.ptr_and_len();
        assert_eq!(len, 128);
        assert!(!ptr.is_null());
    }

    #[test]
    fn test_fetch_add_concurrency() {
        // Two threads each fetch_add 10000 times; final value must be 20000.
        // Catches atomicity regression (if fetch_add were non-atomic, lost updates).
        let (_buf, region) = make_region(64);
        let region_ptr = region.base.as_ptr() as usize;
        let len = region.len;

        let h = std::thread::spawn(move || {
            let non_null = std::ptr::NonNull::new(region_ptr as *mut u8).unwrap();
            let r = unsafe { ShmRegion::new(non_null, len) };
            for _ in 0..10000 {
                unsafe { r.fetch_add_u64(0, 1) };
            }
        });
        for _ in 0..10000 {
            unsafe { region.fetch_add_u64(0, 1) };
        }
        h.join().unwrap();
        assert_eq!(unsafe { region.load_u64(0) }, 20000);
    }
}
