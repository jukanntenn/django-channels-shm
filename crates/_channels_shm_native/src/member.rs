// member.rs — group member array operations (pure logic, no pyo3).
// Mirrors the index.rs pattern: core functions take &ShmRegion so they are
// unit-testable without the Python layer.

use crate::layout;
use crate::region::ShmRegion;

/// Read a group member at a specific index.
/// Returns (active, name_bytes, join_time).
pub fn group_member_read(
    region: &ShmRegion,
    members_offset: u64,
    index: u32,
) -> (bool, Vec<u8>, u64) {
    let entry_off = members_offset as usize + index as usize * layout::MEMBER_ENTRY_SIZE;
    // SAFETY: callers guarantee members_offset + index*ENTRY_SIZE is in-bounds.
    unsafe {
        let active = region.read_u8(entry_off + layout::MEMBER_ACTIVE) != 0;
        let mut name_buf = [0u8; 128];
        region.read_bytes(entry_off + layout::MEMBER_CHANNEL_NAME, &mut name_buf);
        let name_len = name_buf.iter().position(|&b| b == 0).unwrap_or(128);
        let join_time = region.load_u64(entry_off + layout::MEMBER_JOIN_TIME);
        (active, name_buf[..name_len].to_vec(), join_time)
    }
}

/// Add a member to a group. Returns true on success.
///
/// # Safety
/// - Caller must hold the global flock.
pub unsafe fn group_member_add(
    region: &ShmRegion,
    grp_slot_off: usize,
    members_offset: u64,
    channel_name: &[u8],
    now: u64,
    max_members: u32,
    group_expiry: u32,
) -> bool {
    let name_bytes = channel_name;
    let name_len = name_bytes.len().min(128);

    let mut empty_slot: Option<usize> = None;
    for i in 0..max_members as usize {
        let entry_off = members_offset as usize + i * layout::MEMBER_ENTRY_SIZE;
        let active = region.read_u8(entry_off + layout::MEMBER_ACTIVE) != 0;

        if active {
            let mut existing_name = [0u8; 128];
            region.read_bytes(entry_off + layout::MEMBER_CHANNEL_NAME, &mut existing_name);
            let existing_len = existing_name.iter().position(|&b| b == 0).unwrap_or(128);
            if existing_len == name_len && existing_name[..name_len] == name_bytes[..name_len] {
                region.store_u64(entry_off + layout::MEMBER_JOIN_TIME, now);
                return true;
            }
            let join_time = region.load_u64(entry_off + layout::MEMBER_JOIN_TIME);
            if join_time + (u64::from(group_expiry)) < now {
                region.write_u8(entry_off + layout::MEMBER_ACTIVE, 0);
                if empty_slot.is_none() {
                    empty_slot = Some(i);
                }
            }
        } else if empty_slot.is_none() {
            empty_slot = Some(i);
        }
    }

    if let Some(idx) = empty_slot {
        let entry_off = members_offset as usize + idx * layout::MEMBER_ENTRY_SIZE;
        let mut name_buf = [0u8; 128];
        name_buf[..name_len].copy_from_slice(&name_bytes[..name_len]);
        region.copy_in(entry_off + layout::MEMBER_CHANNEL_NAME, &name_buf);
        region.store_u64(entry_off + layout::MEMBER_JOIN_TIME, now);
        region.write_u8(entry_off + layout::MEMBER_ACTIVE, 1);
        let v = region.load_u64(grp_slot_off + layout::GRP_SLOT_VERSION);
        region.store_u64(grp_slot_off + layout::GRP_SLOT_VERSION, v + 1);
        let count = region.read_u32(grp_slot_off + layout::GRP_SLOT_MEMBER_COUNT);
        region.write_u32(grp_slot_off + layout::GRP_SLOT_MEMBER_COUNT, count + 1);
        region.store_u64(grp_slot_off + layout::GRP_SLOT_VERSION, v + 2);
        return true;
    }

    false
}

/// Read all active, non-expired members of a group in one pass (§7.4 hot path).
/// Returns the decoded channel names. Expiry check is `join_time + group_expiry < now`
/// (strictly less-than, matching §7.1). Used by group_send to avoid 1024 FFI
/// round-trips of group_member_read (G-03).
///
/// # Safety
/// - Caller must guarantee `members_offset` points to a valid members array
///   (allocated under flock by group_index_create_or_find).
pub fn group_members_read_all(
    region: &ShmRegion,
    members_offset: u64,
    max_members: u32,
    now: u64,
    group_expiry: u32,
) -> Vec<String> {
    let mut out = Vec::new();
    for i in 0..max_members as usize {
        let entry_off = members_offset as usize + i * layout::MEMBER_ENTRY_SIZE;
        // SAFETY: caller guarantees members_offset + i*ENTRY_SIZE in bounds.
        unsafe {
            let active = region.read_u8(entry_off + layout::MEMBER_ACTIVE) != 0;
            if !active {
                continue;
            }
            let join_time = region.load_u64(entry_off + layout::MEMBER_JOIN_TIME);
            if join_time + u64::from(group_expiry) < now {
                continue;
            }
            let mut name_buf = [0u8; 128];
            region.read_bytes(entry_off + layout::MEMBER_CHANNEL_NAME, &mut name_buf);
            let name_len = name_buf.iter().position(|&b| b == 0).unwrap_or(128);
            if let Ok(s) = std::str::from_utf8(&name_buf[..name_len]) {
                out.push(s.to_owned());
            }
        }
    }
    out
}

/// Remove a member from a group. Returns true if found and removed.
///
/// # Safety
/// - Caller must hold the global flock.
pub unsafe fn group_member_remove(
    region: &ShmRegion,
    grp_slot_off: usize,
    members_offset: u64,
    channel_name: &[u8],
    max_members: u32,
) -> bool {
    let name_bytes = channel_name;
    let name_len = name_bytes.len().min(128);

    for i in 0..max_members as usize {
        let entry_off = members_offset as usize + i * layout::MEMBER_ENTRY_SIZE;
        let active = region.read_u8(entry_off + layout::MEMBER_ACTIVE) != 0;
        if !active {
            continue;
        }

        let mut existing_name = [0u8; 128];
        region.read_bytes(entry_off + layout::MEMBER_CHANNEL_NAME, &mut existing_name);
        let existing_len = existing_name.iter().position(|&b| b == 0).unwrap_or(128);
        if existing_len == name_len && existing_name[..name_len] == name_bytes[..name_len] {
            region.write_u8(entry_off + layout::MEMBER_ACTIVE, 0);
            let v = region.load_u64(grp_slot_off + layout::GRP_SLOT_VERSION);
            region.store_u64(grp_slot_off + layout::GRP_SLOT_VERSION, v + 1);
            let count = region.read_u32(grp_slot_off + layout::GRP_SLOT_MEMBER_COUNT);
            if count > 0 {
                region.write_u32(grp_slot_off + layout::GRP_SLOT_MEMBER_COUNT, count - 1);
            }
            if count <= 1 {
                region.write_u8(grp_slot_off + layout::GRP_SLOT_ACTIVE, 0);
            }
            region.store_u64(grp_slot_off + layout::GRP_SLOT_VERSION, v + 2);
            return true;
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::region::ShmRegion;
    use std::ptr::NonNull;

    struct Harness {
        _buf: Vec<u64>,
        region: ShmRegion,
        grp_slot_off: usize,
        members_off: u64,
        max_members: u32,
    }

    /// Region layout: [fake group slot][members array]. The group slot is
    /// zeroed (version=0, count=0, active=0); entries start inactive.
    fn setup(max_members: u32) -> Harness {
        let members_size = max_members as usize * layout::MEMBER_ENTRY_SIZE;
        let total = layout::GRP_SLOT_SIZE + members_size;
        let words = total.div_ceil(8);
        let buf = vec![0u64; words];
        let ptr = buf.as_ptr() as *mut u8;
        let non_null = NonNull::new(ptr).unwrap();
        // SAFETY: buf is 8-byte aligned and valid for `total` bytes.
        let region = unsafe { ShmRegion::new(non_null, total) };
        let grp_slot_off = 0usize;
        let members_off = layout::GRP_SLOT_SIZE as u64;
        // SAFETY: offsets are within bounds.
        unsafe {
            for j in 0..max_members as usize {
                let entry_off = members_off as usize + j * layout::MEMBER_ENTRY_SIZE;
                region.write_u8(entry_off + layout::MEMBER_ACTIVE, 0);
                region.store_u64(entry_off + layout::MEMBER_JOIN_TIME, 0);
            }
        }
        Harness {
            _buf: buf,
            region,
            grp_slot_off,
            members_off,
            max_members,
        }
    }

    #[test]
    fn test_read_all_filters_inactive_expired_and_invalid_utf8() {
        let h = setup(8);
        let expiry = 50u32;
        // SAFETY: caller holds no flock in a single-threaded test.
        unsafe {
            // Active, fresh member.
            assert!(group_member_add(
                &h.region,
                h.grp_slot_off,
                h.members_off,
                b"ch.fresh",
                100,
                h.max_members,
                expiry
            ));
            // Active but expired member (join 50 + expiry 50 < now 150).
            assert!(group_member_add(
                &h.region,
                h.grp_slot_off,
                h.members_off,
                b"ch.stale",
                50,
                h.max_members,
                expiry
            ));
            // Inactive entry with a valid name — must be skipped.
            let entry1 = h.members_off as usize + layout::MEMBER_ENTRY_SIZE;
            h.region
                .copy_in(entry1 + layout::MEMBER_CHANNEL_NAME, b"ch.inactive\0");

            // Active entry with invalid UTF-8 — must be skipped.
            let entry2 = h.members_off as usize + 2 * layout::MEMBER_ENTRY_SIZE;
            h.region
                .copy_in(entry2 + layout::MEMBER_CHANNEL_NAME, &[0xFF, 0xFE, 0x00]);
            h.region.write_u8(entry2 + layout::MEMBER_ACTIVE, 1);
        }
        let names = group_members_read_all(&h.region, h.members_off, h.max_members, 150, expiry);
        assert_eq!(names, vec!["ch.fresh".to_owned()]);
    }

    #[test]
    fn test_read_all_returns_all_fresh_members_in_order() {
        let h = setup(8);
        // SAFETY: single-threaded test.
        unsafe {
            for name in ["a.x", "b.x", "c.x"] {
                assert!(group_member_add(
                    &h.region,
                    h.grp_slot_off,
                    h.members_off,
                    name.as_bytes(),
                    100,
                    h.max_members,
                    50
                ));
            }
        }
        let names = group_members_read_all(&h.region, h.members_off, h.max_members, 120, 50);
        assert_eq!(
            names,
            vec!["a.x".to_owned(), "b.x".to_owned(), "c.x".to_owned()]
        );
    }

    #[test]
    fn test_remove_last_member_deactivates_group() {
        let h = setup(4);
        // SAFETY: single-threaded test.
        unsafe {
            assert!(group_member_add(
                &h.region,
                h.grp_slot_off,
                h.members_off,
                b"only",
                100,
                h.max_members,
                50
            ));
            assert_eq!(
                h.region
                    .read_u32(h.grp_slot_off + layout::GRP_SLOT_MEMBER_COUNT),
                1
            );
            assert!(group_member_remove(
                &h.region,
                h.grp_slot_off,
                h.members_off,
                b"only",
                h.max_members
            ));
            assert_eq!(
                h.region
                    .read_u32(h.grp_slot_off + layout::GRP_SLOT_MEMBER_COUNT),
                0
            );
            // Last member removed → group slot goes inactive.
            assert_eq!(
                h.region.read_u8(h.grp_slot_off + layout::GRP_SLOT_ACTIVE),
                0
            );
        }
    }

    #[test]
    fn test_read_full_width_name_without_nul() {
        // A name occupying all 128 bytes (no NUL) must read back whole —
        // covers the `unwrap_or(128)` length fallback.
        let h = setup(4);
        let entry = h.members_off as usize;
        let name: Vec<u8> = std::iter::repeat_n(b'x', 128).collect();
        // SAFETY: entry is within bounds.
        unsafe {
            h.region.copy_in(entry + layout::MEMBER_CHANNEL_NAME, &name);
            h.region.write_u8(entry + layout::MEMBER_ACTIVE, 1);
            let (active, read, _join) = group_member_read(&h.region, h.members_off, 0);
            assert!(active);
            assert_eq!(read.len(), 128);
            assert_eq!(read, name);
        }
    }
}
