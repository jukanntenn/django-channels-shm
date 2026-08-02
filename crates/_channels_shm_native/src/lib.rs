// channels_shm._native — Rust extension for shared memory channel layer.
// Exposes: ShmRegion (atomic ops), Ring (Vyukov MPMC), SlabAllocator (size-class pool),
// plus layout helpers and the full shm initialization sequence.

pub mod index;
pub mod layout;
pub mod member;
mod metrics;
pub(crate) mod py_bindings;
mod region;
pub mod ring;
pub mod slab;

use py_bindings::{PyRing, PyShmRegion, PySlabAllocator};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

// Re-export for use in py_bindings and benchmarks
pub use region::ShmRegion;

// ────────────────────────────────────────────────────────────────
// Helper functions
// ────────────────────────────────────────────────────────────────

/// Hash a name using FNV-1a.
#[pyfunction]
fn fnv1a_hash(name: &[u8]) -> u64 {
    layout::fnv1a_hash(name)
}

/// Read /proc/self/stat starttime field.
#[pyfunction]
fn read_self_starttime() -> u64 {
    layout::read_self_starttime()
}

/// Determine if a process is dead (two-layer: kill + starttime).
#[pyfunction]
fn pid_dead(pid: u32, start_time: u64) -> bool {
    layout::pid_dead(pid, start_time)
}

/// Compute shared memory layout offsets.
/// Returns (channel_index_off, group_index_off, group_members_off,
///          wakeup_registry_off, metrics_counters_off, dynamic_pool_off)
#[pyfunction]
fn compute_offsets(
    max_channels: u32,
    max_groups: u32,
    max_members_per_group: u32,
    max_processes: u32,
) -> (u64, u64, u64, u64, u64, u64) {
    layout::compute_offsets(
        max_channels,
        max_groups,
        max_members_per_group,
        max_processes,
    )
}

/// Find the size class for a given size. Returns None if too large.
#[pyfunction]
fn size_class_for(size: usize) -> Option<usize> {
    layout::size_class_for(size)
}

/// Initialize the shared memory header and all regions.
#[pyfunction]
#[allow(clippy::too_many_arguments)] // shm config has 10+ fields; PyO3 pyfunction, a config struct would harm Python ergonomics
fn shm_init(
    region: &PyShmRegion,
    total_size: u64,
    inline_size: u32,
    default_capacity: u32,
    expiry: u32,
    group_expiry: u32,
    max_channels: u32,
    max_groups: u32,
    max_members_per_group: u32,
    max_processes: u32,
    slab: &PySlabAllocator,
) {
    let (ch_off, grp_off, members_off, reg_off, metrics_off, pool_off) = layout::compute_offsets(
        max_channels,
        max_groups,
        max_members_per_group,
        max_processes,
    );

    let r = &region.inner;
    unsafe {
        // Write config
        r.write_u32(layout::HDR_INLINE_SIZE, inline_size);
        r.write_u32(layout::HDR_DEFAULT_CAPACITY, default_capacity);
        r.write_u32(layout::HDR_EXPIRY, expiry);
        r.write_u32(layout::HDR_GROUP_EXPIRY, group_expiry);
        r.write_u32(layout::HDR_MAX_CHANNELS, max_channels);
        r.write_u32(layout::HDR_MAX_GROUPS, max_groups);
        r.write_u32(layout::HDR_MAX_PROCESSES, max_processes);
        r.write_u32(layout::HDR_MAX_MEMBERS_PER_GROUP, max_members_per_group);

        // Write region offsets
        r.store_u64(layout::HDR_CHANNEL_INDEX_OFF, ch_off);
        r.store_u64(layout::HDR_GROUP_INDEX_OFF, grp_off);
        r.store_u64(layout::HDR_GROUP_MEMBERS_OFF, members_off);
        r.store_u64(layout::HDR_WAKEUP_REGISTRY_OFF, reg_off);
        r.store_u64(layout::HDR_METRICS_COUNTERS_OFF, metrics_off);
        r.store_u64(layout::HDR_DYNAMIC_POOL_OFF, pool_off);

        r.store_u64(layout::HDR_TOTAL_SIZE, total_size);
        r.store_u64(layout::HDR_SEQ, 0);

        // Initialize channel index: all slots empty
        for i in 0..max_channels as usize {
            let slot_off = ch_off as usize + i * layout::CH_SLOT_SIZE;
            r.write_u16(slot_off + layout::CH_SLOT_NAME_LEN, 0);
            r.store_u64(slot_off + layout::CH_SLOT_RING_OFFSET, 0);
            r.write_u32(slot_off + layout::CH_SLOT_CAPACITY, 0);
            r.write_u8(slot_off + layout::CH_SLOT_NON_LOCAL, 0);
            r.store_u64(slot_off + layout::CH_SLOT_VERSION, 0);
        }

        // Initialize group index
        for i in 0..max_groups as usize {
            let slot_off = grp_off as usize + i * layout::GRP_SLOT_SIZE;
            r.write_u16(slot_off + layout::GRP_SLOT_NAME_LEN, 0);
            r.store_u64(slot_off + layout::GRP_SLOT_MEMBERS_OFFSET, 0);
            r.write_u32(slot_off + layout::GRP_SLOT_MEMBER_COUNT, 0);
            r.store_u64(slot_off + layout::GRP_SLOT_VERSION, 0);
            r.write_u8(slot_off + layout::GRP_SLOT_ACTIVE, 0);
        }

        // NOTE: no pre-reserved group-members region to initialize (R-01).
        // Member arrays are allocated from the dynamic pool on demand by
        // group_index_create_or_find (§7.2 V4.1).

        // Initialize wakeup registry
        for i in 0..max_processes as usize {
            let slot_off = reg_off as usize + i * layout::REG_SLOT_SIZE;
            r.write_u8(slot_off + layout::REG_VALID, 0);
            r.write_u32(slot_off + layout::REG_PID, 0);
            r.store_u64(slot_off + layout::REG_START_TIME, 0);
            r.store_u64(slot_off + layout::REG_VERSION, 0);
        }

        // Initialize slab allocator
        slab.inner.init(r);

        // Write magic LAST with Release ordering — ARM64 needs this so that
        // a later process acquiring flock and reading magic (Acquire) also
        // sees all preceding config stores. x86 TSO makes this a no-op.
        r.store_u32(layout::HDR_MAGIC, layout::MAGIC);
        r.store_u32(layout::HDR_VERSION, layout::VERSION);
    }
}

/// Check if the shm header magic is valid.
#[pyfunction]
fn check_magic(region: &PyShmRegion) -> bool {
    // Acquire load pairs with shm_init's Release store of magic.
    let magic = unsafe { region.inner.load_u32(layout::HDR_MAGIC) };
    magic == layout::MAGIC
}

/// Read the shm header version.
#[pyfunction]
fn read_version(region: &PyShmRegion) -> u32 {
    unsafe { region.inner.read_u32(layout::HDR_VERSION) }
}

/// Validate that the shm config matches expected values.
#[pyfunction]
fn validate_config(
    region: &PyShmRegion,
    inline_size: u32,
    default_capacity: u32,
    max_channels: u32,
    max_groups: u32,
    max_members_per_group: u32,
    max_processes: u32,
) -> bool {
    let r = &region.inner;
    unsafe {
        r.load_u32(layout::HDR_INLINE_SIZE) == inline_size
            && r.load_u32(layout::HDR_DEFAULT_CAPACITY) == default_capacity
            && r.load_u32(layout::HDR_MAX_CHANNELS) == max_channels
            && r.load_u32(layout::HDR_MAX_GROUPS) == max_groups
            && r.load_u32(layout::HDR_MAX_MEMBERS_PER_GROUP) == max_members_per_group
            && r.load_u32(layout::HDR_MAX_PROCESSES) == max_processes
    }
}

/// Lookup a channel by name in the index (seqlock read).
#[pyfunction]
fn channel_index_lookup(
    region: &PyShmRegion,
    name: &str,
    max_channels: u32,
) -> (bool, usize, u64, u32, bool) {
    index::channel_index_lookup(&region.inner, name, max_channels)
}

/// Create a new channel entry in the index.
#[pyfunction]
fn channel_index_create(
    region: &PyShmRegion,
    name: &str,
    ring_offset: u64,
    capacity: u32,
    non_local: bool,
    max_channels: u32,
) -> (usize, bool) {
    index::channel_index_create(
        &region.inner,
        name,
        ring_offset,
        capacity,
        non_local,
        max_channels,
    )
}

/// Lookup a group by name in the index.
#[pyfunction]
fn group_index_lookup(
    region: &PyShmRegion,
    name: &str,
    max_groups: u32,
) -> (bool, usize, u64, u32, bool) {
    index::group_index_lookup(&region.inner, name, max_groups)
}

/// Create or find a group entry.
#[pyfunction]
fn group_index_create_or_find(
    region: &PyShmRegion,
    name: &str,
    slab: &PySlabAllocator,
    max_groups: u32,
    max_members_per_group: u32,
) -> (usize, u64) {
    index::group_index_create_or_find(
        &region.inner,
        &slab.inner,
        name,
        max_groups,
        max_members_per_group,
    )
}

/// Register a process in the wakeup registry.
#[pyfunction]
fn registry_register(
    region: &PyShmRegion,
    client_prefix: &str,
    socket_path: &str,
    pid: u32,
    start_time: u64,
    max_processes: u32,
) -> usize {
    let reg_off = unsafe { region.inner.load_u64(layout::HDR_WAKEUP_REGISTRY_OFF) } as usize;
    let prefix_bytes = client_prefix.as_bytes();
    let path_bytes = socket_path.as_bytes();

    for i in 0..max_processes as usize {
        let slot_off = reg_off + i * layout::REG_SLOT_SIZE;
        let valid = unsafe { region.inner.read_u8(slot_off + layout::REG_VALID) };
        if valid == 0 {
            unsafe {
                region.inner.store_u64(slot_off + layout::REG_VERSION, 1);
                let mut prefix_buf = [0u8; 32];
                let mut path_buf = [0u8; 108];
                let plen = prefix_bytes.len().min(32);
                let slen = path_bytes.len().min(107);
                prefix_buf[..plen].copy_from_slice(&prefix_bytes[..plen]);
                path_buf[..slen].copy_from_slice(&path_bytes[..slen]);
                region
                    .inner
                    .copy_in(slot_off + layout::REG_CLIENT_PREFIX, &prefix_buf);
                region
                    .inner
                    .copy_in(slot_off + layout::REG_SOCKET_PATH, &path_buf);
                region.inner.write_u32(slot_off + layout::REG_PID, pid);
                region
                    .inner
                    .store_u64(slot_off + layout::REG_START_TIME, start_time);
                region.inner.write_u8(slot_off + layout::REG_VALID, 1);
                region.inner.store_u64(slot_off + layout::REG_VERSION, 2);
            }
            return slot_off;
        }
    }
    0
}

/// Mark a registry slot as dead.
#[pyfunction]
fn registry_mark_dead(region: &PyShmRegion, slot_offset: usize) {
    unsafe {
        region.inner.write_u8(slot_offset + layout::REG_VALID, 0);
    }
}

/// Get all valid registry entries.
#[pyfunction]
fn registry_get_valid<'py>(
    py: Python<'py>,
    region: &PyShmRegion,
    max_processes: u32,
) -> Vec<(usize, Bound<'py, PyBytes>)> {
    let reg_off = unsafe { region.inner.load_u64(layout::HDR_WAKEUP_REGISTRY_OFF) } as usize;
    let mut result = Vec::new();

    for i in 0..max_processes as usize {
        let slot_off = reg_off + i * layout::REG_SLOT_SIZE;
        let valid = unsafe { region.inner.read_u8(slot_off + layout::REG_VALID) };
        if valid != 0 {
            let mut path_buf = [0u8; 108];
            unsafe {
                region
                    .inner
                    .read_bytes(slot_off + layout::REG_SOCKET_PATH, &mut path_buf)
            };
            let path_len = path_buf.iter().position(|&b| b == 0).unwrap_or(108);
            result.push((slot_off, PyBytes::new(py, &path_buf[..path_len])));
        }
    }
    result
}

/// Look up a process's socket path by its client_prefix (targeted unicast, §4.2.4).
/// Returns the socket_path bytes for the matching valid slot, or None if no
/// valid slot has this prefix (process dead / not yet registered / slot recycled).
/// This replaces the O(N) broadcast in _wakeup_by_prefix with O(N) scan-but-
/// single-target sendto (L-03). Prefix is a fixed 32-byte field (uuid4 hex).
#[pyfunction]
fn registry_lookup_socket<'py>(
    py: Python<'py>,
    region: &PyShmRegion,
    target_prefix: &str,
    max_processes: u32,
) -> Option<Bound<'py, PyBytes>> {
    let reg_off = unsafe { region.inner.load_u64(layout::HDR_WAKEUP_REGISTRY_OFF) } as usize;
    let prefix_bytes = target_prefix.as_bytes();
    for i in 0..max_processes as usize {
        let slot_off = reg_off + i * layout::REG_SLOT_SIZE;
        let valid = unsafe { region.inner.read_u8(slot_off + layout::REG_VALID) };
        if valid == 0 {
            continue;
        }
        let mut prefix_buf = [0u8; 32];
        unsafe {
            region
                .inner
                .read_bytes(slot_off + layout::REG_CLIENT_PREFIX, &mut prefix_buf)
        };
        let plen = prefix_buf.iter().position(|&b| b == 0).unwrap_or(32);
        if plen == prefix_bytes.len() && prefix_buf[..plen] == prefix_bytes[..plen] {
            let mut path_buf = [0u8; 108];
            unsafe {
                region
                    .inner
                    .read_bytes(slot_off + layout::REG_SOCKET_PATH, &mut path_buf)
            };
            let path_len = path_buf.iter().position(|&b| b == 0).unwrap_or(108);
            return Some(PyBytes::new(py, &path_buf[..path_len]));
        }
    }
    None
}

/// Read a group member at a specific index.
#[pyfunction]
fn group_member_read(
    region: &PyShmRegion,
    members_offset: u64,
    index: u32,
) -> (bool, Vec<u8>, u64) {
    member::group_member_read(&region.inner, members_offset, index)
}

/// Read all active, non-expired group members in one pass (§7.4 hot path).
/// Rust-side expiry filter; returns decoded channel names. (G-03)
#[pyfunction]
fn group_members_read_all(
    region: &PyShmRegion,
    members_offset: u64,
    max_members: u32,
    now: u64,
    group_expiry: u32,
) -> Vec<String> {
    member::group_members_read_all(
        &region.inner,
        members_offset,
        max_members,
        now,
        group_expiry,
    )
}

/// Flush the channel layer to blank state (§9.6). Caller holds the global flock.
/// Replaces the Python implementation with hardcoded layout magic numbers.
#[pyfunction]
fn flush(region: &PyShmRegion, slab: &PySlabAllocator, max_channels: u32, max_groups: u32) {
    index::flush(&region.inner, &slab.inner, max_channels, max_groups)
}

/// Non-destructive stuck-slot repair (§9.7). Caller holds the global flock.
#[pyfunction]
fn compact(region: &PyShmRegion, slab: &PySlabAllocator, max_channels: u32, start_time: u64) {
    index::compact(&region.inner, &slab.inner, max_channels, start_time)
}

/// Add a member to a group.
#[pyfunction]
fn group_member_add(
    region: &PyShmRegion,
    grp_slot_off: usize,
    members_offset: u64,
    channel_name: &str,
    now: u64,
    max_members: u32,
    group_expiry: u32,
) -> bool {
    // SAFETY: caller (Python layer) holds the global flock.
    unsafe {
        member::group_member_add(
            &region.inner,
            grp_slot_off,
            members_offset,
            channel_name.as_bytes(),
            now,
            max_members,
            group_expiry,
        )
    }
}

/// Remove a member from a group.
#[pyfunction]
fn group_member_remove(
    region: &PyShmRegion,
    grp_slot_off: usize,
    members_offset: u64,
    channel_name: &str,
    max_members: u32,
) -> bool {
    // SAFETY: caller holds the global flock.
    unsafe {
        member::group_member_remove(
            &region.inner,
            grp_slot_off,
            members_offset,
            channel_name.as_bytes(),
            max_members,
        )
    }
}

// ────────────────────────────────────────────────────────────────
// Python module entry point
// ────────────────────────────────────────────────────────────────

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyShmRegion>()?;
    m.add_class::<PyRing>()?;
    m.add_class::<PySlabAllocator>()?;
    m.add_function(wrap_pyfunction!(fnv1a_hash, m)?)?;
    m.add_function(wrap_pyfunction!(read_self_starttime, m)?)?;
    m.add_function(wrap_pyfunction!(pid_dead, m)?)?;
    m.add_function(wrap_pyfunction!(compute_offsets, m)?)?;
    m.add_function(wrap_pyfunction!(size_class_for, m)?)?;
    m.add_function(wrap_pyfunction!(shm_init, m)?)?;
    m.add_function(wrap_pyfunction!(check_magic, m)?)?;
    m.add_function(wrap_pyfunction!(read_version, m)?)?;
    m.add_function(wrap_pyfunction!(validate_config, m)?)?;
    m.add_function(wrap_pyfunction!(channel_index_lookup, m)?)?;
    m.add_function(wrap_pyfunction!(channel_index_create, m)?)?;
    m.add_function(wrap_pyfunction!(group_index_lookup, m)?)?;
    m.add_function(wrap_pyfunction!(group_index_create_or_find, m)?)?;
    m.add_function(wrap_pyfunction!(registry_register, m)?)?;
    m.add_function(wrap_pyfunction!(registry_mark_dead, m)?)?;
    m.add_function(wrap_pyfunction!(registry_get_valid, m)?)?;
    m.add_function(wrap_pyfunction!(registry_lookup_socket, m)?)?;
    m.add_function(wrap_pyfunction!(group_member_read, m)?)?;
    m.add_function(wrap_pyfunction!(group_members_read_all, m)?)?;
    m.add_function(wrap_pyfunction!(group_member_add, m)?)?;
    m.add_function(wrap_pyfunction!(group_member_remove, m)?)?;
    m.add_function(wrap_pyfunction!(flush, m)?)?;
    m.add_function(wrap_pyfunction!(compact, m)?)?;

    // Layout constants
    m.add("MAGIC", layout::MAGIC)?;
    m.add("VERSION", layout::VERSION)?;
    m.add("HDR_SIZE", layout::HDR_SIZE)?;
    m.add("CH_SLOT_SIZE", layout::CH_SLOT_SIZE)?;
    m.add("GRP_SLOT_SIZE", layout::GRP_SLOT_SIZE)?;
    m.add("MEMBER_ENTRY_SIZE", layout::MEMBER_ENTRY_SIZE)?;
    m.add("REG_SLOT_SIZE", layout::REG_SLOT_SIZE)?;
    m.add("RING_HEADER_SIZE", layout::RING_HEADER_SIZE)?;
    m.add("SLOT_SIZE", layout::SLOT_SIZE)?;
    m.add("SIZE_CLASSES", layout::SIZE_CLASSES)?;

    Ok(())
}

// ────────────────────────────────────────────────────────────────
// Tests
// ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_region(size: usize) -> (Vec<u64>, ShmRegion) {
        let words = size.div_ceil(8);
        let buf = vec![0u64; words];
        let ptr = buf.as_ptr() as *mut u8;
        let non_null = std::ptr::NonNull::new(ptr).unwrap();
        let region = unsafe { ShmRegion::new(non_null, size) };
        (buf, region)
    }

    #[test]
    fn test_fnv1a_hash_binding() {
        let hash = fnv1a_hash(b"test");
        assert!(hash > 0);
    }

    #[test]
    fn test_read_self_starttime_binding() {
        let st = read_self_starttime();
        assert!(st > 0);
    }

    #[test]
    fn test_pid_dead_binding() {
        // pid=0 is never dead (special case).
        assert!(!pid_dead(0, 0));
        // Nonexistent PID → dead.
        assert!(pid_dead(999999, 12345));
        // Self is alive with correct starttime.
        let self_pid = std::process::id();
        let self_st = read_self_starttime();
        assert!(!pid_dead(self_pid, self_st));
        // Self with wrong starttime → dead (PID reuse detection).
        assert!(pid_dead(self_pid, 0));
    }

    #[test]
    fn test_compute_offsets_binding() {
        let (ch, grp, mem, reg, metrics, pool) = compute_offsets(10, 5, 100, 4);
        assert!(ch > 0);
        assert!(grp > ch);
        // `mem` is a vestigial placeholder equal to `reg` (no group-members
        // region is reserved; R-01). Registry follows the group index.
        assert_eq!(mem, reg);
        assert!(reg > grp);
        assert!(metrics > reg);
        assert!(pool > metrics);
    }

    #[test]
    fn test_size_class_for_binding() {
        assert_eq!(size_class_for(100), Some(512));
        assert_eq!(size_class_for(16_777_217), None);
    }

    #[test]
    fn test_check_magic_binding() {
        let (_buf, region) = make_region(4096);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();
        assert!(!check_magic(&py_region));
    }

    #[test]
    fn test_read_version_binding() {
        let (_buf, region) = make_region(4096);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();
        assert_eq!(read_version(&py_region), 0);
    }

    #[test]
    fn test_validate_config_binding() {
        let (_buf, region) = make_region(4096);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();
        assert!(validate_config(&py_region, 0, 0, 0, 0, 0, 0));
    }

    #[test]
    fn test_shm_init_binding() {
        let total_size = 1024 * 1024;
        let (_buf, region) = make_region(total_size);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();

        let (_, _, _, _, _, pool_off) = compute_offsets(10, 5, 100, 4);
        let pool_size = total_size as u64 - pool_off;
        let slab = PySlabAllocator::new(pool_off as usize, pool_size as usize);

        shm_init(
            &py_region,
            total_size as u64,
            512,
            100,
            60,
            86400,
            10,
            5,
            100,
            4,
            &slab,
        );

        assert!(check_magic(&py_region));
        assert_eq!(read_version(&py_region), 1);
    }

    #[test]
    fn test_channel_index_lookup_create_binding() {
        let total_size = 1024 * 1024;
        let (_buf, region) = make_region(total_size);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();

        let (ch_off, _, _, _, _, pool_off) = compute_offsets(10, 5, 100, 4);
        let pool_size = total_size as u64 - pool_off;
        let _slab = PySlabAllocator::new(pool_off as usize, pool_size as usize);

        py_region.store_u64(layout::HDR_CHANNEL_INDEX_OFF, ch_off);

        for i in 0..10 {
            let slot_off = ch_off as usize + i * layout::CH_SLOT_SIZE;
            py_region.write_u16(slot_off + layout::CH_SLOT_NAME_LEN, 0);
            py_region.store_u64(slot_off + layout::CH_SLOT_VERSION, 0);
        }

        let (found, _, _, _, _) = channel_index_lookup(&py_region, "test.ch", 10);
        assert!(!found);

        let (slot_off, existed) =
            channel_index_create(&py_region, "test.ch", 0x1000, 16, false, 10);
        assert!(slot_off != 0);
        assert!(!existed);

        let (found, _, ring_off, cap, _) = channel_index_lookup(&py_region, "test.ch", 10);
        assert!(found);
        assert_eq!(ring_off, 0x1000);
        assert_eq!(cap, 16);
    }

    #[test]
    fn test_group_index_lookup_create_binding() {
        let total_size = 1024 * 1024;
        let (_buf, region) = make_region(total_size);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();

        let (_, grp_off, _, _, _, pool_off) = compute_offsets(10, 5, 100, 4);
        let pool_size = total_size as u64 - pool_off;
        let slab = PySlabAllocator::new(pool_off as usize, pool_size as usize);

        py_region.store_u64(layout::HDR_GROUP_INDEX_OFF, grp_off);

        for i in 0..5 {
            let slot_off = grp_off as usize + i * layout::GRP_SLOT_SIZE;
            py_region.write_u16(slot_off + layout::GRP_SLOT_NAME_LEN, 0);
            py_region.write_u8(slot_off + layout::GRP_SLOT_ACTIVE, 0);
            py_region.store_u64(slot_off + layout::GRP_SLOT_VERSION, 0);
        }

        let (found, _, _, _, _) = group_index_lookup(&py_region, "test.group", 5);
        assert!(!found);

        let (slot_off, members_off) =
            group_index_create_or_find(&py_region, "test.group", &slab, 5, 100);
        assert!(slot_off != 0);
        assert!(members_off != 0);

        let (found, _, _, count, active) = group_index_lookup(&py_region, "test.group", 5);
        assert!(found);
        assert_eq!(count, 0);
        assert!(active);
    }

    #[test]
    fn test_registry_register_mark_dead_binding() {
        pyo3::Python::initialize();
        Python::attach(|py| {
            let total_size = 1024 * 1024;
            let (_buf, region) = make_region(total_size);
            let (ptr, len) = region.ptr_and_len();
            let py_region = PyShmRegion::new(ptr as usize, len).unwrap();

            let (_, _, _, reg_off, _, _) = compute_offsets(10, 5, 100, 4);

            py_region.store_u64(layout::HDR_WAKEUP_REGISTRY_OFF, reg_off);

            for i in 0..4 {
                let slot_off = reg_off as usize + i * layout::REG_SLOT_SIZE;
                py_region.write_u8(slot_off + layout::REG_VALID, 0);
                py_region.store_u64(slot_off + layout::REG_VERSION, 0);
            }

            let slot_off =
                registry_register(&py_region, "test_prefix", "/tmp/test.sock", 12345, 100, 4);
            assert!(slot_off != 0);

            let entries = registry_get_valid(py, &py_region, 4);
            assert_eq!(entries.len(), 1);

            registry_mark_dead(&py_region, slot_off);

            let entries = registry_get_valid(py, &py_region, 4);
            assert_eq!(entries.len(), 0);
        });
    }

    #[test]
    fn test_group_member_add_read_remove_binding() {
        let total_size = 1024 * 1024;
        let (_buf, region) = make_region(total_size);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();

        let (_, grp_off, _, _, _, pool_off) = compute_offsets(10, 5, 2, 4);
        let pool_size = total_size as u64 - pool_off;
        let slab = PySlabAllocator::new(pool_off as usize, pool_size as usize);

        py_region.store_u64(layout::HDR_GROUP_INDEX_OFF, grp_off);

        for i in 0..5 {
            let slot_off = grp_off as usize + i * layout::GRP_SLOT_SIZE;
            py_region.write_u16(slot_off + layout::GRP_SLOT_NAME_LEN, 0);
            py_region.write_u8(slot_off + layout::GRP_SLOT_ACTIVE, 0);
            py_region.store_u64(slot_off + layout::GRP_SLOT_VERSION, 0);
        }

        let (grp_slot_off, members_off) =
            group_index_create_or_find(&py_region, "test.group", &slab, 5, 2);
        assert!(grp_slot_off != 0);
        assert!(members_off != 0);

        let ok = group_member_add(
            &py_region,
            grp_slot_off,
            members_off,
            "test.channel",
            1000,
            2,
            86400,
        );
        assert!(ok);

        let (active, name, join_time) = group_member_read(&py_region, members_off, 0);
        assert!(active);
        assert_eq!(name, b"test.channel");
        assert_eq!(join_time, 1000);

        let ok = group_member_add(
            &py_region,
            grp_slot_off,
            members_off,
            "test.channel",
            2000,
            2,
            86400,
        );
        assert!(ok);

        let (_, _, join_time) = group_member_read(&py_region, members_off, 0);
        assert_eq!(join_time, 2000);

        let ok = group_member_remove(&py_region, grp_slot_off, members_off, "test.channel", 2);
        assert!(ok);

        let (active, _, _) = group_member_read(&py_region, members_off, 0);
        assert!(!active);

        let ok = group_member_remove(&py_region, grp_slot_off, members_off, "nonexistent", 2);
        assert!(!ok);
    }

    #[test]
    fn test_group_member_add_expired_reuse() {
        let total_size = 1024 * 1024;
        let (_buf, region) = make_region(total_size);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();

        let (_, grp_off, _, _, _, pool_off) = compute_offsets(10, 5, 2, 4);
        let pool_size = total_size as u64 - pool_off;
        let slab = PySlabAllocator::new(pool_off as usize, pool_size as usize);

        py_region.store_u64(layout::HDR_GROUP_INDEX_OFF, grp_off);

        for i in 0..5 {
            let slot_off = grp_off as usize + i * layout::GRP_SLOT_SIZE;
            py_region.write_u16(slot_off + layout::GRP_SLOT_NAME_LEN, 0);
            py_region.write_u8(slot_off + layout::GRP_SLOT_ACTIVE, 0);
            py_region.store_u64(slot_off + layout::GRP_SLOT_VERSION, 0);
        }

        let (grp_slot_off, members_off) =
            group_index_create_or_find(&py_region, "test.group", &slab, 5, 2);

        group_member_add(
            &py_region,
            grp_slot_off,
            members_off,
            "channel1",
            100,
            2,
            50,
        );
        group_member_add(
            &py_region,
            grp_slot_off,
            members_off,
            "channel2",
            100,
            2,
            50,
        );

        let ok = group_member_add(
            &py_region,
            grp_slot_off,
            members_off,
            "channel3",
            151,
            2,
            50,
        );
        assert!(ok);
    }

    #[test]
    fn test_group_member_add_full() {
        let total_size = 1024 * 1024;
        let (_buf, region) = make_region(total_size);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();

        let (_, grp_off, _, _, _, pool_off) = compute_offsets(10, 5, 2, 4);
        let pool_size = total_size as u64 - pool_off;
        let slab = PySlabAllocator::new(pool_off as usize, pool_size as usize);

        py_region.store_u64(layout::HDR_GROUP_INDEX_OFF, grp_off);

        for i in 0..5 {
            let slot_off = grp_off as usize + i * layout::GRP_SLOT_SIZE;
            py_region.write_u16(slot_off + layout::GRP_SLOT_NAME_LEN, 0);
            py_region.write_u8(slot_off + layout::GRP_SLOT_ACTIVE, 0);
            py_region.store_u64(slot_off + layout::GRP_SLOT_VERSION, 0);
        }

        let (grp_slot_off, members_off) =
            group_index_create_or_find(&py_region, "test.group", &slab, 5, 2);

        group_member_add(&py_region, grp_slot_off, members_off, "ch1", 1000, 2, 86400);
        group_member_add(&py_region, grp_slot_off, members_off, "ch2", 1000, 2, 86400);

        let ok = group_member_add(&py_region, grp_slot_off, members_off, "ch3", 1000, 2, 86400);
        assert!(!ok);
    }

    #[test]
    fn test_registry_register_full() {
        let total_size = 1024 * 1024;
        let (_buf, region) = make_region(total_size);
        let (ptr, len) = region.ptr_and_len();
        let py_region = PyShmRegion::new(ptr as usize, len).unwrap();

        let (_, _, _, reg_off, _, _) = compute_offsets(10, 5, 100, 2);

        py_region.store_u64(layout::HDR_WAKEUP_REGISTRY_OFF, reg_off);

        for i in 0..2 {
            let slot_off = reg_off as usize + i * layout::REG_SLOT_SIZE;
            py_region.write_u8(slot_off + layout::REG_VALID, 0);
            py_region.store_u64(slot_off + layout::REG_VERSION, 0);
        }

        let slot1 = registry_register(&py_region, "p1", "/tmp/s1.sock", 1, 100, 2);
        assert!(slot1 != 0);
        let slot2 = registry_register(&py_region, "p2", "/tmp/s2.sock", 2, 100, 2);
        assert!(slot2 != 0);

        let slot3 = registry_register(&py_region, "p3", "/tmp/s3.sock", 3, 100, 2);
        assert_eq!(slot3, 0);
    }

    #[test]
    fn test_module_initialization() {
        Python::initialize();
        Python::attach(|py| {
            let module = PyModule::new(py, "_native_test").unwrap();
            let result = _native(&module);
            assert!(result.is_ok());

            assert!(module.getattr("ShmRegion").is_ok());
            assert!(module.getattr("Ring").is_ok());
            assert!(module.getattr("SlabAllocator").is_ok());
            assert!(module.getattr("fnv1a_hash").is_ok());
            assert!(module.getattr("read_self_starttime").is_ok());
            assert!(module.getattr("pid_dead").is_ok());
            assert!(module.getattr("compute_offsets").is_ok());
            assert!(module.getattr("size_class_for").is_ok());
            assert!(module.getattr("shm_init").is_ok());
            assert!(module.getattr("check_magic").is_ok());
            assert!(module.getattr("read_version").is_ok());
            assert!(module.getattr("validate_config").is_ok());
            assert!(module.getattr("channel_index_lookup").is_ok());
            assert!(module.getattr("channel_index_create").is_ok());
            assert!(module.getattr("group_index_lookup").is_ok());
            assert!(module.getattr("group_index_create_or_find").is_ok());
            assert!(module.getattr("registry_register").is_ok());
            assert!(module.getattr("registry_mark_dead").is_ok());
            assert!(module.getattr("registry_get_valid").is_ok());
            assert!(module.getattr("registry_lookup_socket").is_ok());
            assert!(module.getattr("group_member_read").is_ok());
            assert!(module.getattr("group_members_read_all").is_ok());
            assert!(module.getattr("group_member_add").is_ok());
            assert!(module.getattr("group_member_remove").is_ok());
            assert!(module.getattr("flush").is_ok());
            assert!(module.getattr("compact").is_ok());

            assert!(module.getattr("MAGIC").is_ok());
            assert!(module.getattr("VERSION").is_ok());
            assert!(module.getattr("HDR_SIZE").is_ok());
            assert!(module.getattr("CH_SLOT_SIZE").is_ok());
            assert!(module.getattr("GRP_SLOT_SIZE").is_ok());
            assert!(module.getattr("MEMBER_ENTRY_SIZE").is_ok());
            assert!(module.getattr("REG_SLOT_SIZE").is_ok());
            assert!(module.getattr("RING_HEADER_SIZE").is_ok());
            assert!(module.getattr("SLOT_SIZE").is_ok());
            assert!(module.getattr("SIZE_CLASSES").is_ok());
        });
    }
}
