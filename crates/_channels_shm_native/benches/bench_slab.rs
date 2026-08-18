//! Slab allocator microbenchmarks: alloc+free cycles across representative
//! size classes, and multi-thread contention on a per-class spinlock.
//!
//! The alloc/free pair is measured together because the product always uses
//! them in a pair (overflow page alloc on enqueue, free on dequeue); the
//! paired cycle is self-sustaining — each free returns the block to the free
//! list, so no state accumulates and no per-iteration reset is needed.
//! A failed alloc (`0`) still ends the cycle safely, since `free(0)` is a
//! no-op, so an out-of-memory regression cannot hang or crash the bench.
//!
//! The contended variant uses `run_contended` (see common.rs): one persistent
//! worker pool per sample, cyclic barriers, and constant total work across
//! thread counts. The contended resource is the per-size-class spinlock plus
//! the free-list head — the hot path for multi-process overflow traffic.

mod common;

use common::{make_region, run_contended};

use std::sync::Arc;

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};

use _channels_shm_native::slab::SlabAllocator;

const POOL_OFFSET: usize = 4096;
const POOL_SIZE: usize = 16 * 1024 * 1024;

/// Ops per iteration for the contended bench, constant across thread counts.
const CONTENDED_OPS_PER_ITER: u64 = 4096;

/// Size classes probed by the sweep: the smallest, a mid-size overflow page,
/// and the class used for group-member arrays.
const SWEEP_SIZES: [usize; 3] = [512, 8192, 262_144];

fn bench_slab_alloc_free(c: &mut Criterion) {
    let mut group = c.benchmark_group("slab_alloc_free");
    for &size in &SWEEP_SIZES {
        group.bench_with_input(BenchmarkId::from_parameter(size), &size, |b, &size| {
            let (_buf, region) = make_region(POOL_OFFSET + POOL_SIZE);
            let slab = SlabAllocator::new(POOL_OFFSET, POOL_SIZE);
            slab.init(&region);
            b.iter(|| unsafe {
                // SAFETY: slab initialized; region valid. free(0) is a no-op.
                slab.free(&region, slab.alloc(&region, size), size);
            });
        });
    }
    group.finish();
}

fn bench_slab_alloc_free_contended(c: &mut Criterion) {
    let thread_counts = [2usize, 4, 8];
    let mut group = c.benchmark_group("slab_alloc_free_contended");
    group.throughput(Throughput::Elements(CONTENDED_OPS_PER_ITER));
    for &n in &thread_counts {
        let per_thread = (CONTENDED_OPS_PER_ITER / n as u64) as usize;
        group.bench_with_input(BenchmarkId::from_parameter(n), &n, |b, &n| {
            let (_buf, region) = make_region(POOL_OFFSET + POOL_SIZE);
            let region = Arc::new(region);
            let worker_region = Arc::clone(&region);
            let worker_slab = SlabAllocator::new(POOL_OFFSET, POOL_SIZE);
            worker_slab.init(&region);
            let worker: Arc<dyn Fn() + Send + Sync> = Arc::new(move || unsafe {
                // SAFETY: region alive via Arc; slab initialized.
                let off = worker_slab.alloc(&worker_region, 512);
                worker_slab.free(&worker_region, off, 512);
            });
            // The alloc/free pair keeps the free list non-empty, so the pool
            // is at steady state across iterations — nothing to reset.
            let mut reset = || {};
            b.iter_custom(|iters| run_contended(worker.clone(), &mut reset, n, per_thread, iters));
        });
    }
    group.finish();
}

criterion_group!(
    benches,
    bench_slab_alloc_free,
    bench_slab_alloc_free_contended,
);
criterion_main!(benches);
