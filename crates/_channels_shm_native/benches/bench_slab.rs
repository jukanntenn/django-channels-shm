//! P1 layer 0: slab allocator microbenchmarks (criterion).

use criterion::{criterion_group, criterion_main, Criterion};
use std::ptr::NonNull;

use _channels_shm_native::slab::SlabAllocator;
use _channels_shm_native::ShmRegion;

fn bench_setup(size: usize) -> (Vec<u64>, ShmRegion) {
    let words = size.div_ceil(8);
    let buf = vec![0u64; words];
    let ptr = buf.as_ptr() as *mut u8;
    let non_null = NonNull::new(ptr).unwrap();
    let region = unsafe { ShmRegion::new(non_null, size) };
    (buf, region)
}

const POOL_OFFSET: usize = 4096;
const POOL_SIZE: usize = 16 * 1024 * 1024; // 16MB pool

fn bench_slab_alloc_free_hot(c: &mut Criterion) {
    let (_buf, region) = bench_setup(POOL_OFFSET + POOL_SIZE);
    let slab = SlabAllocator::new(POOL_OFFSET, POOL_SIZE);
    slab.init(&region);

    c.bench_function("slab_alloc_free_hot", |b| {
        b.iter(|| unsafe {
            let offset = slab.alloc(&region, 256);
            if offset != 0 {
                slab.free(&region, offset, 256);
            }
        });
    });
}

fn bench_slab_alloc_free_large(c: &mut Criterion) {
    let (_buf, region) = bench_setup(POOL_OFFSET + POOL_SIZE);
    let slab = SlabAllocator::new(POOL_OFFSET, POOL_SIZE);
    slab.init(&region);

    c.bench_function("slab_alloc_free_large", |b| {
        b.iter(|| unsafe {
            let offset = slab.alloc(&region, 8192);
            if offset != 0 {
                slab.free(&region, offset, 8192);
            }
        });
    });
}

criterion_group!(
    benches,
    bench_slab_alloc_free_hot,
    bench_slab_alloc_free_large
);
criterion_main!(benches);
