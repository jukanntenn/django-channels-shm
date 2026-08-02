//! P1 layer 0: seqlock index read microbenchmarks (criterion).

use criterion::{criterion_group, criterion_main, Criterion};
use std::ptr::NonNull;

use _channels_shm_native::index;
use _channels_shm_native::layout;
use _channels_shm_native::ShmRegion;

fn bench_setup(size: usize) -> (Vec<u64>, ShmRegion) {
    let words = size.div_ceil(8);
    let buf = vec![0u64; words];
    let ptr = buf.as_ptr() as *mut u8;
    let non_null = NonNull::new(ptr).unwrap();
    let region = unsafe { ShmRegion::new(non_null, size) };
    (buf, region)
}

fn bench_index_seqlock_read_hit(c: &mut Criterion) {
    let total = 1024 * 1024;
    let (_buf, region) = bench_setup(total);

    // Setup: create a channel index entry
    let ch_off = layout::HDR_SIZE as u64;
    unsafe {
        region.store_u64(layout::HDR_CHANNEL_INDEX_OFF, ch_off);
        for i in 0..100usize {
            let slot_off = ch_off as usize + i * layout::CH_SLOT_SIZE;
            region.write_u16(slot_off + layout::CH_SLOT_NAME_LEN, 0);
            region.store_u64(slot_off + layout::CH_SLOT_VERSION, 0);
        }
    }

    // Create a channel entry
    let (slot_off, _) =
        index::channel_index_create(&region, "bench.test.channel", 0x2000, 100, false, 100);
    assert!(slot_off != 0);

    c.bench_function("index_seqlock_read_hit", |b| {
        b.iter(|| {
            let (found, _, _, _, _) =
                index::channel_index_lookup(&region, "bench.test.channel", 100);
            assert!(found);
        });
    });
}

fn bench_index_seqlock_read_miss(c: &mut Criterion) {
    let total = 1024 * 1024;
    let (_buf, region) = bench_setup(total);

    let ch_off = layout::HDR_SIZE as u64;
    unsafe {
        region.store_u64(layout::HDR_CHANNEL_INDEX_OFF, ch_off);
        for i in 0..100usize {
            let slot_off = ch_off as usize + i * layout::CH_SLOT_SIZE;
            region.write_u16(slot_off + layout::CH_SLOT_NAME_LEN, 0);
            region.store_u64(slot_off + layout::CH_SLOT_VERSION, 0);
        }
    }

    c.bench_function("index_seqlock_read_miss", |b| {
        b.iter(|| {
            let (found, _, _, _, _) =
                index::channel_index_lookup(&region, "bench.nonexistent", 100);
            assert!(!found);
        });
    });
}

criterion_group!(
    benches,
    bench_index_seqlock_read_hit,
    bench_index_seqlock_read_miss
);
criterion_main!(benches);
