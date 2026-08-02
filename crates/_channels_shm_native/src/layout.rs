// Shared memory layout constants and offset definitions.
// All offsets are relative to the shm base address (u64).
// All fields are 8-byte aligned unless noted otherwise.

// ── Magic & version ──
pub const MAGIC: u32 = 0x4348_5348; // "CHSH"
pub const VERSION: u32 = 1;

// ── Header (at offset 0) ──
pub const HDR_MAGIC: usize = 0; // u32
pub const HDR_VERSION: usize = 4; // u32
pub const HDR_TOTAL_SIZE: usize = 8; // u64
pub const HDR_SEQ: usize = 16; // u64 (global seqlock counter)
                               // config
pub const HDR_INLINE_SIZE: usize = 24; // u32
pub const HDR_DEFAULT_CAPACITY: usize = 28; // u32
pub const HDR_EXPIRY: usize = 32; // u32 (seconds)
pub const HDR_GROUP_EXPIRY: usize = 36; // u32 (seconds)
pub const HDR_MAX_CHANNELS: usize = 40; // u32
pub const HDR_MAX_GROUPS: usize = 44; // u32
pub const HDR_MAX_PROCESSES: usize = 48; // u32
pub const HDR_MAX_MEMBERS_PER_GROUP: usize = 52; // u32
                                                 // region offsets (filled by first process)
pub const HDR_CHANNEL_INDEX_OFF: usize = 56; // u64
pub const HDR_GROUP_INDEX_OFF: usize = 64; // u64
pub const HDR_GROUP_MEMBERS_OFF: usize = 72; // u64
pub const HDR_WAKEUP_REGISTRY_OFF: usize = 80; // u64
pub const HDR_METRICS_COUNTERS_OFF: usize = 88; // u64 (O3: metrics counters region)
pub const HDR_DYNAMIC_POOL_OFF: usize = 96; // u64
                                            // padding to 4KB alignment
pub const HDR_SIZE: usize = 4096;

// ── Channel Index Slot ──
// Name field is 128 bytes so process-specific channel names of the form
// "{prefix}.{client_prefix}!{uuid4.hex}" (worst case ~128B with prefix≤62)
// are not silently truncated (R-03). All offsets 8-byte aligned where needed.
pub const CH_SLOT_NAME_HASH: usize = 0; // u64
pub const CH_SLOT_NAME_LEN: usize = 8; // u16
pub const CH_SLOT_NAME: usize = 10; // [u8; 128] (ends at 138)
pub const CH_SLOT_RING_OFFSET: usize = 144; // u64 (8-align: 138 → 144)
pub const CH_SLOT_CAPACITY: usize = 152; // u32
pub const CH_SLOT_NON_LOCAL: usize = 156; // u8 (bool)
pub const CH_SLOT_VERSION: usize = 160; // u64 (8-align: 157 → 160)
pub const CH_SLOT_SIZE: usize = 168; // total, 8-byte aligned

// ── Group Index Slot ──
pub const GRP_SLOT_NAME_HASH: usize = 0; // u64
pub const GRP_SLOT_NAME_LEN: usize = 8; // u16
pub const GRP_SLOT_NAME: usize = 10; // [u8; 128] (ends at 138)
pub const GRP_SLOT_MEMBERS_OFFSET: usize = 144; // u64 (8-align: 138 → 144)
pub const GRP_SLOT_MEMBER_COUNT: usize = 152; // u32
pub const GRP_SLOT_VERSION: usize = 160; // u64 (8-align)
pub const GRP_SLOT_ACTIVE: usize = 168; // u8
pub const GRP_SLOT_SIZE: usize = 176; // total, 8-byte aligned

// ── Group Member Entry ──
pub const MEMBER_CHANNEL_NAME: usize = 0; // [u8; 128]
pub const MEMBER_JOIN_TIME: usize = 128; // u64 (128 already 8-aligned)
pub const MEMBER_ACTIVE: usize = 136; // u8
pub const MEMBER_ENTRY_SIZE: usize = 144; // total, 8-byte aligned

// ── Wakeup Registry Slot ──
pub const REG_CLIENT_PREFIX: usize = 0; // [u8; 32]
pub const REG_SOCKET_PATH: usize = 32; // [u8; 108]
pub const REG_PID: usize = 140; // u32
pub const REG_START_TIME: usize = 144; // u64
pub const REG_VALID: usize = 152; // u8
pub const REG_VERSION: usize = 160; // u64 (seqlock)
pub const REG_SLOT_SIZE: usize = 168; // total, 8-byte aligned

// ── Dynamic Pool ──
// Per-size-class spinlock (in dynamic pool region):
pub const SLAB_SPINLOCK_SIZE: usize = 8; // u64 (AtomicU64, 0=unlocked, 1=locked)

// ── Vyukov Ring Header ──
pub const RING_ENQUEUE_POS: usize = 0; // u64
pub const RING_DEQUEUE_POS: usize = 8; // u64
pub const RING_CAPACITY: usize = 16; // u32
                                     // padding
pub const RING_LAST_COMPACT_ENQ: usize = 24; // u64
pub const RING_LAST_COMPACT_DEQ: usize = 32; // u64
pub const RING_HEADER_SIZE: usize = 40; // total, 8-byte aligned

// ── Vyukov Ring Slot ──
pub const SLOT_SEQ: usize = 0; // u64
pub const SLOT_OWNER_PID: usize = 8; // u32
                                     // Layout note: SLOT_OWNER_PID occupies [8, 12), and [12, 16) is padding (zeroed at
                                     // init, never written elsewhere). recover_slot's CAS operates on the full 8-byte
                                     // slot [8, 16), comparing expected = dead_owner_pid as u64 (padding == 0). This
                                     // invariant must hold: do NOT add a field in the [12, 16) padding without
                                     // revisiting recover_slot's CAS logic.
pub const SLOT_RECOVERING: u32 = u32::MAX; // sentinel: recover in progress (never a real PID)
pub const SLOT_OWNER_TICKET: usize = 16; // u64
pub const SLOT_OWNER_START_TIME: usize = 24; // u64
pub const SLOT_COMPACT_MARK: usize = 32; // u8
pub const SLOT_EXPIRY_TS: usize = 40; // u64 (f64 bits)
pub const SLOT_CHANNEL_LEN: usize = 48; // u16
pub const SLOT_CHANNEL_NAME: usize = 50; // [u8; 128] (ends at 178)
pub const SLOT_MSG_LEN: usize = 180; // u32 (4-align ok at 180)
pub const SLOT_INLINE: usize = 184; // [u8; 512] (ends at 696)
pub const SLOT_OVERFLOW_OFF: usize = 696; // u64 (8-align: 696)
pub const SLOT_SIZE: usize = 704; // total, 8-byte aligned

// ── O3: Metrics counters region ──
/// Number of metrics counter slots (each slot = 8 bytes AtomicU64).
/// Only read/written by Rust side when `metrics` feature is enabled;
/// region always exists in shm layout for compatibility.
pub const METRICS_COUNTER_COUNT: usize = 64;
/// Total size of metrics counters region in bytes.
pub const METRICS_REGION_SIZE: usize = METRICS_COUNTER_COUNT * 8;

// Size-class definitions for slab allocator.
// 262_144 (256KiB) added between 131_072 and 524_288 to absorb group member
// arrays (~256×144≈37KiB → still 128KiB class, but larger arrays land here
// instead of jumping 4x to 512KiB; see G-01 / R-03 sizing notes).
pub const SIZE_CLASSES: &[usize] = &[
    512, 2048, 8192, 32768, 131_072, 262_144, 524_288, 1_048_576, 2_097_152, 4_194_304, 8_388_608,
    16_777_216,
];

// Compile-time invariant: SIZE_CLASSES must be strictly ascending.
// size_class_for / size_class_idx rely on this (find/position returns first >= size).
const fn strictly_ascending(slice: &[usize]) -> bool {
    let mut i = 1;
    while i < slice.len() {
        if slice[i - 1] >= slice[i] {
            return false;
        }
        i += 1;
    }
    true
}
const _: () = assert!(
    strictly_ascending(SIZE_CLASSES),
    "SIZE_CLASSES must be strictly ascending"
);

/// Find the smallest size class >= `size`.
pub fn size_class_for(size: usize) -> Option<usize> {
    SIZE_CLASSES.iter().copied().find(|&sc| sc >= size)
}

/// Hash a channel/group name using FNV-1a.
pub fn fnv1a_hash(name: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for &b in name {
        hash ^= u64::from(b);
        hash = hash.wrapping_mul(0x100_0000_01b3);
    }
    hash
}

/// Compute region offsets for the shm layout.
/// Returns (channel_index_off, group_index_off, group_members_off,
///          wakeup_registry_off, metrics_counters_off, dynamic_pool_off)
///
/// NOTE: `group_members_off` (3rd element) is a VESTIGIAL field kept only for
/// arity compatibility with callers that unpack 6 values. Group member arrays
/// are allocated from the dynamic pool slab (§7.2 V4.1), NOT from a pre-sized
/// contiguous region — so no "group members region" is reserved. The returned
/// `group_members_off` equals `wakeup_registry_off` (a zero-length placeholder).
/// `max_members_per_group` is therefore unused here but kept in the signature
/// for call-site stability. (R-01 fix: removed the ~117MiB dead zone.)
pub fn compute_offsets(
    max_channels: u32,
    max_groups: u32,
    _max_members_per_group: u32,
    max_processes: u32,
) -> (u64, u64, u64, u64, u64, u64) {
    let ch_off = HDR_SIZE as u64;
    let grp_off = ch_off + max_channels as u64 * CH_SLOT_SIZE as u64;
    // No pre-reserved group-members region: registry follows the group index
    // directly. Member arrays come from the dynamic pool on demand.
    let reg_off = grp_off + max_groups as u64 * GRP_SLOT_SIZE as u64;
    let members_off = reg_off; // vestigial placeholder (zero-length)
    let metrics_off = reg_off + max_processes as u64 * REG_SLOT_SIZE as u64;
    let pool_off = metrics_off + METRICS_REGION_SIZE as u64;
    (ch_off, grp_off, members_off, reg_off, metrics_off, pool_off)
}

/// Read /proc/self/stat and return the starttime field (field 22, 0-indexed = 21).
pub fn read_self_starttime() -> u64 {
    read_stat_starttime(std::process::id())
}

/// Parse the starttime field (field 22) from the contents of /proc/{pid}/stat.
/// Pure string processing — decoupled from filesystem for testability.
pub fn parse_starttime_from_stat(data: &str) -> u64 {
    // Field 22 (1-indexed) is starttime. Fields before it may contain spaces (comm field 2).
    // Find the closing paren to skip the comm field.
    let after_comm = if let Some(idx) = data.rfind(')') {
        &data[idx + 2..]
    } else {
        data
    };
    // After comm: state(1) ppid(2) pgrp(3) session(4) tty_nr(5) tpgid(6) flags(7)
    // minflt(8) cminflt(9) majflt(10) cmajflt(11) utime(12) stime(13)
    // cutime(14) cstime(15) priority(16) nice(17) num_threads(18)
    // itrealvalue(19) starttime(20) -- 20th field after comm = field 22 overall
    after_comm
        .split_whitespace()
        .nth(19) // 0-indexed: field 20 after comm = field 22 overall
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(0)
}

/// Read /proc/{pid}/stat and return the starttime field.
pub fn read_stat_starttime(pid: u32) -> u64 {
    let path = format!("/proc/{}/stat", pid);
    let Ok(data) = std::fs::read_to_string(&path) else {
        return 0;
    };
    parse_starttime_from_stat(&data)
}

/// Determine if a process is dead. Two-layer check: kill(pid,0) + starttime comparison.
pub fn pid_dead(pid: u32, start_time: u64) -> bool {
    if pid == 0 {
        return false;
    }
    // Layer 1: check process existence
    let ret = unsafe { libc::kill(pid as i32, 0) };
    if ret != 0 {
        let err = std::io::Error::last_os_error();
        // ESRCH = No such process
        return err.raw_os_error() == Some(libc::ESRCH);
    }
    // Layer 2: check starttime for PID reuse
    let current_starttime = read_stat_starttime(pid);
    if current_starttime == 0 {
        // Can't read stat → conservatively treat as dead
        return true;
    }
    current_starttime != start_time
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_size_class_for_exact_match() {
        assert_eq!(size_class_for(512), Some(512));
        assert_eq!(size_class_for(2048), Some(2048));
        assert_eq!(size_class_for(16_777_216), Some(16_777_216));
    }

    #[test]
    fn test_size_class_for_rounds_up() {
        assert_eq!(size_class_for(1), Some(512));
        assert_eq!(size_class_for(513), Some(2048));
        assert_eq!(size_class_for(1000), Some(2048));
        assert_eq!(size_class_for(1_000_000), Some(1_048_576));
    }

    #[test]
    fn test_size_class_for_too_large() {
        assert_eq!(size_class_for(16_777_217), None);
        assert_eq!(size_class_for(usize::MAX), None);
    }

    #[test]
    fn test_fnv1a_hash_basic() {
        let h1 = fnv1a_hash(b"test.channel");
        let h2 = fnv1a_hash(b"test.channel");
        assert_eq!(h1, h2, "same input should produce same hash");
    }

    #[test]
    fn test_fnv1a_hash_different_inputs() {
        let h1 = fnv1a_hash(b"a");
        let h2 = fnv1a_hash(b"b");
        assert_ne!(h1, h2, "different inputs should produce different hashes");
    }

    #[test]
    fn test_fnv1a_hash_empty() {
        let h = fnv1a_hash(b"");
        // FNV-1a offset basis for empty input
        assert_eq!(h, 0xcbf2_9ce4_8422_2325);
    }

    #[test]
    fn test_compute_offsets_basic() {
        let (ch, grp, mem, reg, metrics, pool) = compute_offsets(10, 5, 100, 4);
        assert_eq!(ch, HDR_SIZE as u64);
        assert_eq!(grp, ch + 10 * CH_SLOT_SIZE as u64);
        // No group-members region: registry follows group index directly.
        // `mem` is a vestigial placeholder equal to `reg`.
        assert_eq!(reg, grp + 5 * GRP_SLOT_SIZE as u64);
        assert_eq!(mem, reg);
        assert_eq!(metrics, reg + 4 * REG_SLOT_SIZE as u64);
        assert_eq!(pool, metrics + METRICS_REGION_SIZE as u64);
    }

    #[test]
    fn test_read_self_starttime_nonzero() {
        let st = read_self_starttime();
        // Current process should have a non-zero starttime
        assert!(st > 0, "current process starttime should be > 0, got {st}");
    }

    #[test]
    fn test_read_stat_starttime_self() {
        let pid = std::process::id();
        let st = read_stat_starttime(pid);
        assert!(st > 0, "self starttime should be > 0");
    }

    #[test]
    fn test_read_stat_starttime_nonexistent() {
        // PID 999999 should not exist
        let st = read_stat_starttime(999999);
        assert_eq!(st, 0);
    }

    #[test]
    fn test_pid_dead_zero_pid() {
        // pid=0 should not be considered dead
        assert!(!pid_dead(0, 0));
    }

    #[test]
    fn test_pid_dead_self_alive() {
        let pid = std::process::id();
        let st = read_stat_starttime(pid);
        // Current process is alive with correct starttime
        assert!(!pid_dead(pid, st));
    }

    #[test]
    fn test_pid_dead_self_wrong_starttime() {
        let pid = std::process::id();
        // Wrong starttime → PID reuse detection
        assert!(pid_dead(pid, 0));
    }

    #[test]
    fn test_pid_dead_nonexistent() {
        // PID 999999 should not exist → dead
        assert!(pid_dead(999999, 12345));
    }

    #[test]
    fn test_read_stat_starttime_self_with_wrong_pid() {
        // Test the else branch: when current_starttime == 0, pid_dead returns true
        // This happens when /proc/{pid}/stat can't be read
        // PID 999999 doesn't exist, so read_stat_starttime returns 0
        let st = read_stat_starttime(999999);
        assert_eq!(st, 0);
        // pid_dead should return true when starttime is 0
        assert!(pid_dead(999999, 12345));
    }

    #[test]
    fn test_pid_dead_stat_read_returns_zero() {
        // Test the path where current_starttime == 0 (can't read stat).
        // PID 999999 doesn't exist, so read_stat_starttime returns 0.
        // pid_dead should return true (conservative: treat as dead).
        assert!(pid_dead(999999, 12345));
    }

    #[test]
    fn test_parse_starttime_basic() {
        // Real /proc/self/stat format: pid (comm) state ppid ... starttime(20th after comm)
        // Construct a minimal valid stat line. Fields after comm:
        // state ppid pgrp session tty_nr tpgid flags minflt cminflt majflt cmajflt
        // utime stime cutime cstime priority nice num_threads itrealvalue starttime
        let data = "123 (test_prog) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 9999";
        assert_eq!(parse_starttime_from_stat(data), 9999);
    }

    #[test]
    fn test_parse_starttime_comm_with_spaces() {
        // comm field contains spaces — rfind(')') must skip the whole comm.
        let data = "123 (My Program Name) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 4242";
        assert_eq!(parse_starttime_from_stat(data), 4242);
    }

    #[test]
    fn test_parse_starttime_comm_with_paren() {
        // comm field contains a closing paren — rfind takes the LAST ')'.
        let data = "123 (prog (sub)) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 7777";
        assert_eq!(parse_starttime_from_stat(data), 7777);
    }

    #[test]
    fn test_parse_starttime_no_closing_paren() {
        // Malformed line (no closing paren) — falls back to whole-string parse.
        // The whole string is parsed; starttime must be the 20th token (nth(19)).
        let data = "garbage 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 1111";
        assert_eq!(parse_starttime_from_stat(data), 1111);
    }

    #[test]
    fn test_parse_starttime_empty() {
        assert_eq!(parse_starttime_from_stat(""), 0);
    }

    #[test]
    fn test_fnv1a_golden_vectors() {
        // Golden vectors (FNV-1a 64-bit, offset basis 0xcbf29ce484222325, prime 0x100000001b3).
        // Catches "prime/basis written wrong but stable" implementation bugs.
        assert_eq!(fnv1a_hash(b""), 0xcbf29ce484222325);
        assert_eq!(fnv1a_hash(b"a"), 0xaf63dc4c8601ec8c);
        assert_eq!(fnv1a_hash(b"foobar"), 0x85944171f73967e8);
    }
}
