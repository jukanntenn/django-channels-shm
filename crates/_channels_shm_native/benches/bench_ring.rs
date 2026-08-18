//! Vyukov ring microbenchmarks: uncontended enqueue/dequeue, a message-size
//! sweep across the inline/overflow boundary, and multi-thread contended
//! enqueue/dequeue.
//!
//! The ring hot paths are: enqueue = ticket fetch-add + slot write + publish,
//! dequeue = claim CAS + slot read + recycle. Large messages spill into the
//! slab, so the size sweep also covers the overflow alloc/free cost.
//!
//! Measurement rules:
//! - Uncontended benches use `chunked_iter` (see common.rs): the ring/slab
//!   reset runs once per chunk, outside the timed window. `iter_batched`
//!   cannot express this — its batch inputs are all collected before any
//!   routine runs, so a side-effect reset lands once per batch and the ring
//!   fills up mid-batch, silently turning the measured op into the Full/None
//!   return path.
//! - Contended benches use `run_contended`: one persistent worker pool per
//!   sample, spawned before the timed loop, cyclic barriers for a
//!   simultaneous start, and a constant number of ops per iteration across
//!   thread counts so `N` results are directly comparable.
//! - The message sweep reports `Throughput::Bytes`; the contended groups
//!   report attempts per second.

mod common;

use common::{chunked_iter, make_region, run_contended};

use std::sync::Arc;

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};

use _channels_shm_native::layout;
use _channels_shm_native::ring::{EnqueueResult, OwnerIdentity, Ring};
use _channels_shm_native::slab::SlabAllocator;
use _channels_shm_native::ShmRegion;

const RING_OFFSET: usize = 4096;
const POOL_OFFSET: usize = 4 * 1024 * 1024;
const POOL_SIZE: usize = 32 * 1024 * 1024;

/// Ops per iteration for the contended benches, constant across thread counts.
const CONTENDED_OPS_PER_ITER: u64 = 4096;

/// Ring capacity for the contended benches: must exceed
/// `CONTENDED_OPS_PER_ITER` so producers never hit Full, and the dequeue
/// bench can hold a full prefill.
const CONTENDED_CAPACITY: u32 = 8192;

/// Ops per timed window for the uncontended benches. The ring capacity is
/// sized to the chunk so a reset stays cheap (reset cost scales with
/// capacity, not with the chunk).
const UNCONTENDED_CHUNK: u64 = 256;

/// Chunk for the message sweep: smaller than the uncontended chunk so the
/// ring prefill of the largest messages still fits the pool.
const SWEEP_CHUNK: u64 = 64;

/// Message sizes for the sweep: two inline (≤512) and two overflow (>512).
const SWEEP_SIZES: [usize; 4] = [32, 512, 4096, 65_536];

struct BenchSetup {
    _buf: Vec<u64>,
    region: ShmRegion,
    ring: Ring,
    slab: SlabAllocator,
}

/// Build a region + ring + slab. Writes HDR_INLINE_SIZE, which try_enqueue
/// reads to decide the inline/overflow boundary.
fn setup(inline_size: u32, capacity: u32) -> BenchSetup {
    let total = POOL_OFFSET + POOL_SIZE;
    let (buf, region) = make_region(total);
    // SAFETY: offset within bounds; header field is 4 bytes at offset 24.
    unsafe {
        region.write_u32(layout::HDR_INLINE_SIZE, inline_size);
    }
    let slab = SlabAllocator::new(POOL_OFFSET, POOL_SIZE);
    slab.init(&region);
    let ring = Ring::new(RING_OFFSET);
    // SAFETY: region is large enough.
    unsafe {
        ring.init(&region, capacity);
    }
    BenchSetup {
        _buf: buf,
        region,
        ring,
        slab,
    }
}

fn owner() -> OwnerIdentity {
    OwnerIdentity {
        pid: std::process::id(),
        start_time: 100,
    }
}

fn bench_ring_enqueue_uncontended(c: &mut Criterion) {
    let s = setup(512, 512);
    c.bench_function("ring_enqueue_uncontended", |b| {
        chunked_iter(
            b,
            UNCONTENDED_CHUNK,
            || unsafe { s.ring.reset(&s.region) },
            || unsafe {
                let r = s.ring.try_enqueue(
                    &s.region,
                    &s.slab,
                    b"bench.test",
                    b"hello",
                    f64::MAX,
                    owner(),
                );
                debug_assert_eq!(r, EnqueueResult::Ok);
            },
        );
    });
}

fn bench_ring_dequeue_uncontended(c: &mut Criterion) {
    let s = setup(512, 512);
    c.bench_function("ring_dequeue_uncontended", |b| {
        chunked_iter(
            b,
            UNCONTENDED_CHUNK,
            || unsafe {
                // Refill outside the timed window; capacity > chunk so no
                // dequeue can hit the empty ring.
                s.ring.reset(&s.region);
                for _ in 0..UNCONTENDED_CHUNK {
                    let r = s.ring.try_enqueue(
                        &s.region,
                        &s.slab,
                        b"bench.test",
                        b"hello",
                        f64::MAX,
                        owner(),
                    );
                    debug_assert_eq!(r, EnqueueResult::Ok);
                }
            },
            || unsafe {
                let r = s.ring.try_dequeue(&s.region, &s.slab, f64::MAX, owner());
                debug_assert!(r.is_some());
            },
        );
    });
}

fn bench_ring_msg_sweep(c: &mut Criterion) {
    let mut group = c.benchmark_group("ring_enqueue_msg");
    for &msg_size in &SWEEP_SIZES {
        // Throughput is captured per registration, so per-size bytes work.
        group.throughput(Throughput::Bytes(msg_size as u64));
        group.bench_with_input(
            BenchmarkId::from_parameter(msg_size),
            &msg_size,
            |b, &msg_size| {
                let s = setup(512, 256);
                let msg = vec![0xABu8; msg_size];
                chunked_iter(
                    b,
                    SWEEP_CHUNK,
                    || unsafe {
                        s.ring.reset(&s.region);
                        // Free-list/bump state must not accumulate across chunks.
                        s.slab.reset(&s.region);
                    },
                    || unsafe {
                        let r = s.ring.try_enqueue(
                            &s.region,
                            &s.slab,
                            b"bench.test",
                            &msg,
                            f64::MAX,
                            owner(),
                        );
                        debug_assert_eq!(r, EnqueueResult::Ok);
                    },
                );
            },
        );
    }
    group.finish();
}

fn bench_ring_dequeue_msg_sweep(c: &mut Criterion) {
    let mut group = c.benchmark_group("ring_dequeue_msg");
    for &msg_size in &SWEEP_SIZES {
        group.throughput(Throughput::Bytes(msg_size as u64));
        group.bench_with_input(
            BenchmarkId::from_parameter(msg_size),
            &msg_size,
            |b, &msg_size| {
                let s = setup(512, 256);
                let msg = vec![0xABu8; msg_size];
                chunked_iter(
                    b,
                    SWEEP_CHUNK,
                    || unsafe {
                        s.ring.reset(&s.region);
                        s.slab.reset(&s.region);
                        for _ in 0..SWEEP_CHUNK {
                            let r = s.ring.try_enqueue(
                                &s.region,
                                &s.slab,
                                b"bench.test",
                                &msg,
                                f64::MAX,
                                owner(),
                            );
                            debug_assert_eq!(r, EnqueueResult::Ok);
                        }
                    },
                    || unsafe {
                        let r = s.ring.try_dequeue(&s.region, &s.slab, f64::MAX, owner());
                        debug_assert!(r.is_some());
                    },
                );
            },
        );
    }
    group.finish();
}

fn bench_ring_enqueue_contended(c: &mut Criterion) {
    let thread_counts = [2usize, 4, 8];
    let mut group = c.benchmark_group("ring_enqueue_contended");
    group.throughput(Throughput::Elements(CONTENDED_OPS_PER_ITER));
    for &n in &thread_counts {
        let per_thread = (CONTENDED_OPS_PER_ITER / n as u64) as usize;
        group.bench_with_input(BenchmarkId::from_parameter(n), &n, |b, &n| {
            let s = setup(512, CONTENDED_CAPACITY);
            let region = Arc::new(s.region);
            let worker_region = Arc::clone(&region);
            let worker: Arc<dyn Fn() + Send + Sync> = Arc::new(move || {
                // SAFETY: region alive via Arc; ring/slab valid.
                let r = unsafe {
                    s.ring.try_enqueue(
                        &worker_region,
                        &s.slab,
                        b"bench.test",
                        b"hello",
                        f64::MAX,
                        owner(),
                    )
                };
                debug_assert_eq!(r, EnqueueResult::Ok);
            });
            let reset_region = Arc::clone(&region);
            let reset_ring = Ring::new(RING_OFFSET);
            let mut reset = move || unsafe { reset_ring.reset(&reset_region) };
            b.iter_custom(|iters| {
                run_contended(Arc::clone(&worker), &mut reset, n, per_thread, iters)
            });
        });
    }
    group.finish();
}

fn bench_ring_dequeue_contended(c: &mut Criterion) {
    let thread_counts = [2usize, 4, 8];
    let mut group = c.benchmark_group("ring_dequeue_contended");
    group.throughput(Throughput::Elements(CONTENDED_OPS_PER_ITER));
    for &n in &thread_counts {
        let per_thread = (CONTENDED_OPS_PER_ITER / n as u64) as usize;
        group.bench_with_input(BenchmarkId::from_parameter(n), &n, |b, &n| {
            let s = setup(512, CONTENDED_CAPACITY);
            let region = Arc::new(s.region);
            let worker_region = Arc::clone(&region);
            let worker: Arc<dyn Fn() + Send + Sync> = Arc::new(move || {
                // SAFETY: region alive via Arc; ring/slab valid.
                let r = unsafe {
                    s.ring
                        .try_dequeue(&worker_region, &s.slab, f64::MAX, owner())
                };
                debug_assert!(r.is_some());
            });
            let reset_region = Arc::clone(&region);
            let reset_ring = Ring::new(RING_OFFSET);
            let reset_slab = SlabAllocator::new(POOL_OFFSET, POOL_SIZE);
            let mut reset = move || unsafe {
                // Prefill outside the timed window; consumers drain exactly
                // the prefilled messages, so no dequeue can return None.
                reset_ring.reset(&reset_region);
                for _ in 0..CONTENDED_OPS_PER_ITER {
                    let r = reset_ring.try_enqueue(
                        &reset_region,
                        &reset_slab,
                        b"bench.test",
                        b"hello",
                        f64::MAX,
                        owner(),
                    );
                    debug_assert_eq!(r, EnqueueResult::Ok);
                }
            };
            b.iter_custom(|iters| {
                run_contended(Arc::clone(&worker), &mut reset, n, per_thread, iters)
            });
        });
    }
    group.finish();
}

criterion_group!(
    benches,
    bench_ring_enqueue_uncontended,
    bench_ring_dequeue_uncontended,
    bench_ring_msg_sweep,
    bench_ring_dequeue_msg_sweep,
    bench_ring_enqueue_contended,
    bench_ring_dequeue_contended,
);
criterion_main!(benches);
