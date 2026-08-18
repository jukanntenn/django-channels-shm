//! Index microbenchmarks: seqlock hash-table lookups for channels and groups.
//!
//! The lookup is a linear probe over fixed-size slots comparing a 64-bit
//! hash, a length, and (only on a hash match) the name under a seqlock. The
//! interesting costs are the slot-scan length (best case = slot 0, worst =
//! full table) and the fnv1a hash of the name. The hit-position sweep
//! `{0, 50, 99}` measures the probe at three fill levels of the channel and
//! group tables, plus a miss bench that scans the entire table.
//!
//! Setup (table fill + assert) runs outside the timed window; the timed op
//! only looks up and black-boxes the result, so the compiler cannot elide
//! the probe.

mod common;

use common::make_region;

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion};

use _channels_shm_native::index::{channel_index_create, channel_index_lookup};
use _channels_shm_native::index::{group_index_create_or_find, group_index_lookup};
use _channels_shm_native::layout;
use _channels_shm_native::slab::SlabAllocator;

const MAX_CHANNELS: u32 = 100;
const MAX_GROUPS: u32 = 100;
const MAX_MEMBERS: u32 = 32;
const POOL_SIZE: usize = 1024 * 1024;

/// Slot positions probed by the hit sweep: first slot, mid-table, last slot.
const HIT_POSITIONS: [usize; 3] = [0, 50, 99];

const TARGET: &str = "bench_target";

struct BenchSetup {
    _buf: Vec<u64>,
    region: _channels_shm_native::ShmRegion,
    slab: SlabAllocator,
}

/// Build a region with a channel/group index header (no rings, no wakeup
/// registry — lookups only touch the index tables and the slab pool).
fn setup() -> BenchSetup {
    let (ch_off, grp_off, _, _, _, pool_off) =
        layout::compute_offsets(MAX_CHANNELS, MAX_GROUPS, MAX_MEMBERS, 0);
    let total = pool_off as usize + POOL_SIZE;
    let (buf, region) = make_region(total);
    // SAFETY: offsets within bounds.
    unsafe {
        region.store_u64(layout::HDR_CHANNEL_INDEX_OFF, ch_off);
        region.store_u64(layout::HDR_GROUP_INDEX_OFF, grp_off);
    }
    let slab = SlabAllocator::new(pool_off as usize, POOL_SIZE);
    slab.init(&region);
    BenchSetup {
        _buf: buf,
        region,
        slab,
    }
}

fn bench_channel_lookup_hit(c: &mut Criterion) {
    let mut group = c.benchmark_group("index_channel_lookup_hit");
    for &pos in &HIT_POSITIONS {
        group.bench_with_input(BenchmarkId::from_parameter(pos), &pos, |b, &pos| {
            let s = setup();
            for i in 0..pos {
                let (slot, existed) = channel_index_create(
                    &s.region,
                    &format!("bench_dummy_{i}"),
                    4096 + i as u64,
                    512,
                    false,
                    MAX_CHANNELS,
                );
                assert!(slot != 0 && !existed);
            }
            let (slot, existed) =
                channel_index_create(&s.region, TARGET, 9999, 512, false, MAX_CHANNELS);
            assert!(slot != 0 && !existed);
            b.iter(|| {
                let (found, _, _, _, _) = channel_index_lookup(&s.region, TARGET, MAX_CHANNELS);
                std::hint::black_box(found);
            });
        });
    }
    group.finish();
}

fn bench_channel_lookup_miss(c: &mut Criterion) {
    c.bench_function("index_channel_lookup_miss", |b| {
        let s = setup();
        for i in 0..MAX_CHANNELS as usize {
            let (slot, existed) = channel_index_create(
                &s.region,
                &format!("bench_dummy_{i}"),
                4096 + i as u64,
                512,
                false,
                MAX_CHANNELS,
            );
            assert!(slot != 0 && !existed);
        }
        b.iter(|| {
            let (found, _, _, _, _) = channel_index_lookup(&s.region, TARGET, MAX_CHANNELS);
            std::hint::black_box(found);
        });
    });
}

fn bench_group_lookup_hit(c: &mut Criterion) {
    let mut group = c.benchmark_group("index_group_lookup_hit");
    for &pos in &HIT_POSITIONS {
        group.bench_with_input(BenchmarkId::from_parameter(pos), &pos, |b, &pos| {
            let s = setup();
            for i in 0..pos {
                let (slot, members) = group_index_create_or_find(
                    &s.region,
                    &s.slab,
                    &format!("bench_group_{i}"),
                    MAX_GROUPS,
                    MAX_MEMBERS,
                );
                assert!(slot != 0 && members != 0);
            }
            let (slot, members) =
                group_index_create_or_find(&s.region, &s.slab, TARGET, MAX_GROUPS, MAX_MEMBERS);
            assert!(slot != 0 && members != 0);
            b.iter(|| {
                let (found, _, _, _, _) = group_index_lookup(&s.region, TARGET, MAX_GROUPS);
                std::hint::black_box(found);
            });
        });
    }
    group.finish();
}

fn bench_group_lookup_miss(c: &mut Criterion) {
    c.bench_function("index_group_lookup_miss", |b| {
        let s = setup();
        for i in 0..MAX_GROUPS as usize {
            let (slot, members) = group_index_create_or_find(
                &s.region,
                &s.slab,
                &format!("bench_group_{i}"),
                MAX_GROUPS,
                MAX_MEMBERS,
            );
            assert!(slot != 0 && members != 0);
        }
        b.iter(|| {
            let (found, _, _, _, _) = group_index_lookup(&s.region, TARGET, MAX_GROUPS);
            std::hint::black_box(found);
        });
    });
}

criterion_group!(
    benches,
    bench_channel_lookup_hit,
    bench_channel_lookup_miss,
    bench_group_lookup_hit,
    bench_group_lookup_miss,
);
criterion_main!(benches);
