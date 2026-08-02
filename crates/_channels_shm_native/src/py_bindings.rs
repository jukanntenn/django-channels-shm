// PyO3 binding wrappers for Python interop.
// This file contains #[pymethods] and #[pyclass] attributes that generate
// macro code which LLVM coverage cannot properly instrument.
// Coverage for the actual logic is in the core modules (atomic, ring, slab, index, layout).

use crate::region::ShmRegion;
use crate::ring;
use crate::slab::SlabAllocator;
use pyo3::buffer::PyBuffer; // true zero-copy for buffer-protocol objects (§6.1, L-19)
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::ptr::NonNull;

#[pyclass(name = "ShmRegion")]
pub struct PyShmRegion {
    pub inner: ShmRegion,
}

#[pymethods]
impl PyShmRegion {
    #[new]
    pub fn new(ptr: usize, len: usize) -> PyResult<Self> {
        let non_null =
            NonNull::new(ptr as *mut u8).ok_or_else(|| PyValueError::new_err("null pointer"))?;
        Ok(Self {
            inner: unsafe { ShmRegion::new(non_null, len) },
        })
    }

    pub fn load_u64(&self, offset: usize) -> u64 {
        unsafe { self.inner.load_u64(offset) }
    }

    pub fn store_u64(&self, offset: usize, value: u64) {
        unsafe { self.inner.store_u64(offset, value) }
    }

    pub fn cas_u64(&self, offset: usize, expected: u64, desired: u64) -> (bool, u64) {
        match unsafe { self.inner.cas_u64(offset, expected, desired) } {
            Ok(v) => (true, v),
            Err(v) => (false, v),
        }
    }

    pub fn fetch_add_u64(&self, offset: usize, delta: u64) -> u64 {
        unsafe { self.inner.fetch_add_u64(offset, delta) }
    }

    pub fn copy_in(&self, offset: usize, data: &[u8]) {
        unsafe { self.inner.copy_in(offset, data) }
    }

    pub fn copy_out<'py>(
        &self,
        py: Python<'py>,
        offset: usize,
        length: usize,
    ) -> Bound<'py, PyBytes> {
        let data = unsafe { self.inner.copy_out(offset, length) };
        PyBytes::new(py, &data)
    }

    pub fn read_u32(&self, offset: usize) -> u32 {
        unsafe { self.inner.read_u32(offset) }
    }

    pub fn write_u32(&self, offset: usize, value: u32) {
        unsafe { self.inner.write_u32(offset, value) }
    }

    pub fn read_u16(&self, offset: usize) -> u16 {
        unsafe { self.inner.read_u16(offset) }
    }

    pub fn write_u16(&self, offset: usize, value: u16) {
        unsafe { self.inner.write_u16(offset, value) }
    }

    pub fn read_u8(&self, offset: usize) -> u8 {
        unsafe { self.inner.read_u8(offset) }
    }

    pub fn write_u8(&self, offset: usize, value: u8) {
        unsafe { self.inner.write_u8(offset, value) }
    }

    pub fn read_bytes<'py>(
        &self,
        py: Python<'py>,
        offset: usize,
        length: usize,
    ) -> Bound<'py, PyBytes> {
        let mut buf = vec![0u8; length];
        unsafe { self.inner.read_bytes(offset, &mut buf) };
        PyBytes::new(py, &buf)
    }

    pub fn len(&self) -> usize {
        self.inner.ptr_and_len().1
    }
}

#[pyclass(name = "Ring")]
pub struct PyRing {
    pub inner: ring::Ring,
}

#[pymethods]
impl PyRing {
    #[new]
    pub fn new(ring_offset: usize) -> Self {
        Self {
            inner: ring::Ring::new(ring_offset),
        }
    }

    pub fn init(&self, region: &PyShmRegion, capacity: u32) {
        unsafe { self.inner.init(&region.inner, capacity) }
    }

    pub fn offset(&self) -> usize {
        self.inner.offset()
    }

    pub fn capacity(&self, region: &PyShmRegion) -> u32 {
        unsafe { self.inner.capacity(&region.inner) }
    }

    #[allow(clippy::too_many_arguments)] // PyO3 signature mirrors Python API; bundling into a struct would hurt Python callers
    pub fn try_enqueue(
        &self,
        py: Python<'_>,
        region: &PyShmRegion,
        slab: &PySlabAllocator,
        channel_name: &[u8],
        msg_data: PyBuffer<u8>,
        expiry_ts: f64,
        pid: u32,
        start_time: u64,
    ) -> bool {
        // Zero-copy: slice into the exporter's memory (memoryview from
        // msgpack.Packer.getbuffer() lands here without a Python-side bytes()
        // copy; §6.1 / L-19). try_enqueue copies the data into the shm ring
        // (inline) or a slab overflow page before returning, so the borrow
        // does not escape this call.
        let msg_slice: &[u8] = match msg_data.as_slice(py) {
            // SAFETY: ReadOnlyCell<u8> is a transparent wrapper over
            // UnsafeCell<u8>; the slice of them has identical layout to [u8].
            // try_enqueue only reads the bytes (no mutation) and does not
            // retain the borrow past the call.
            Some(cells) => unsafe {
                std::slice::from_raw_parts(cells.as_ptr().cast::<u8>(), cells.len())
            },
            None => return false, // non-contiguous / incompatible — treat as failure
        };
        let owner = ring::OwnerIdentity { pid, start_time };
        let result = unsafe {
            self.inner.try_enqueue(
                &region.inner,
                &slab.inner,
                channel_name,
                msg_slice,
                expiry_ts,
                owner,
            )
        };
        result == ring::EnqueueResult::Ok
    }

    pub fn try_dequeue<'py>(
        &self,
        py: Python<'py>,
        region: &PyShmRegion,
        slab: &PySlabAllocator,
        now: f64,
        pid: u32,
        start_time: u64,
    ) -> Option<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let owner = ring::OwnerIdentity { pid, start_time };
        let result = unsafe {
            self.inner
                .try_dequeue(&region.inner, &slab.inner, now, owner)
        };
        result.map(|(ch, msg)| (PyBytes::new(py, &ch), PyBytes::new(py, &msg)))
    }

    pub fn reset(&self, region: &PyShmRegion) {
        unsafe { self.inner.reset(&region.inner) }
    }

    pub fn compact(&self, region: &PyShmRegion, slab: &PySlabAllocator, start_time: u64) {
        unsafe { self.inner.compact(&region.inner, &slab.inner, start_time) }
    }
}

#[pyclass(name = "SlabAllocator")]
pub struct PySlabAllocator {
    pub inner: SlabAllocator,
}

#[pymethods]
impl PySlabAllocator {
    #[new]
    pub fn new(pool_offset: usize, pool_size: usize) -> Self {
        Self {
            inner: SlabAllocator::new(pool_offset, pool_size),
        }
    }

    pub fn init(&self, region: &PyShmRegion) {
        self.inner.init(&region.inner);
    }

    pub fn alloc(&self, region: &PyShmRegion, size: usize) -> u64 {
        unsafe { self.inner.alloc(&region.inner, size) }
    }

    pub fn free(&self, region: &PyShmRegion, offset: u64, size: usize) {
        unsafe { self.inner.free(&region.inner, offset, size) }
    }

    pub fn alloc_cold(&self, region: &PyShmRegion, size: usize) -> u64 {
        unsafe { self.inner.alloc_cold(&region.inner, size) }
    }

    pub fn free_cold(&self, region: &PyShmRegion, offset: u64, size: usize) {
        unsafe { self.inner.free_cold(&region.inner, offset, size) }
    }

    pub fn reset(&self, region: &PyShmRegion) {
        self.inner.reset(&region.inner);
    }
}
