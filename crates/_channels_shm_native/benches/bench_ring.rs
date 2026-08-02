//! P1 layer 0: Vyukov ring microbenchmarks (criterion).
//!
//! Uncontended (single-thread) + contended (2-producer) variants. The
//! contended bench (B-10) measures fetch_add cache-line bouncing under MPMC
//! contention, which the spec §5.4 anchor (uncontended < 100ns) does not
//! capture on its own.

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use std::ptr::NonNull;
use std::sync::Arc;

use _channels_shm_native::layout;
use _channels_shm_native::ring::{EnqueueResult, OwnerIdentity, Ring};
use _channels_shm_native::slab::SlabAllocator;
use _channels_shm_native::ShmRegion;

const RING_OFFSET: usize = 4096;
const POOL_OFFSET: usize = 1024 * 1024;
const POOL_SIZE: usize = 4 * 1024 * 1024;

fn make_region(size: usize) -> (Vec<u64>, ShmRegion) {
    let words = size.div_ceil(8);
    let buf = vec![0u64; words];
    let ptr = buf.as_ptr() as *mut u8;
    let non_null = NonNull::new(ptr).unwrap();
    // SAFETY: buf is 8-byte aligned (Vec<u64>), valid for `size` bytes.
    let region = unsafe { ShmRegion::new(non_null, size) };
    (buf, region)
}

/// Build a region + ring + slab for benchmarking. Mirrors ring.rs's
/// `setup_ring`: writes HDR_INLINE_SIZE into the header (try_enqueue reads it).
fn setup(inline_size: u32, capacity: u32) -> (Vec<u64>, ShmRegion, Ring, SlabAllocator) {
    let total = POOL_OFFSET + POOL_SIZE;
    let (buf, region) = make_region(total);
    // SAFETY: offset 24 + 4 <= total; inline_size field is in the header.
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
    (buf, region, ring, slab)
}

fn bench_ring_enqueue_uncontended(c: &mut Criterion) {
    let (_buf, region, ring, slab) = setup(512, 256);
    let channel_name = b"bench.test";
    let msg_data = b"hello";
    let now = 1_000_000.0f64;
    let owner = OwnerIdentity {
        pid: std::process::id(),
        start_time: 100,
    };

    c.bench_function("ring_enqueue_uncontended", |b| {
        b.iter_batched(
            // Reset the ring each batch so we measure enqueue, not "ring full
            // → retry". A fresh ring at capacity 256 holds plenty per batch.
            || unsafe { ring.reset(&region) },
            |_| unsafe {
                ring.try_enqueue(
                    &region,
                    &slab,
                    black_box(channel_name),
                    black_box(msg_data),
                    black_box(now + 60.0),
                    owner,
                )
            },
            criterion::BatchSize::SmallInput,
        )
    });
}

fn bench_ring_dequeue_uncontended(c: &mut Criterion) {
    let (_buf, region, ring, slab) = setup(512, 256);
    let channel_name = b"bench.test";
    let msg_data = b"hello";
    let now = 1_000_000.0f64;
    let owner = OwnerIdentity {
        pid: std::process::id(),
        start_time: 100,
    };

    c.bench_function("ring_dequeue_uncontended", |b| {
        b.iter_batched(
            || unsafe {
                // Pre-fill one slot per measurement.
                ring.try_enqueue(&region, &slab, channel_name, msg_data, now + 60.0, owner)
            },
            |_| unsafe { ring.try_dequeue(&region, &slab, now, owner) },
            criterion::BatchSize::SmallInput,
        )
    });
}

/// Contended enqueue: N producer threads compete on the same ring (B-10).
/// Measures fetch_add cache-line bouncing — the cost the uncontended anchor
/// (§5.4) deliberately excludes. Uses iter_custom (manual timing) so we can
/// run N scoped threads per sample and report their wall-clock duration.
fn bench_ring_enqueue_contended(c: &mut Criterion) {
    // Capacity must exceed n_producers * per_thread, else producers spin
    // forever on a full ring (no consumer in this bench). Keep numbers small
    // so the ring fits in the bench's modest pool (4MiB). capacity 1024 with
    // per_thread 256 → max 4*256=1024 enqueues, exactly at the cap (yield-free).
    let capacity = 1024;
    let inline_size = 512;
    let per_thread = 256u64;
    let producer_counts = [2usize, 4];

    let mut group = c.benchmark_group("ring_enqueue_contended");
    for &n in &producer_counts {
        group.bench_with_input(BenchmarkId::from_parameter(n), &n, |b, &n| {
            b.iter_custom(|iters| {
                // Set up ONCE (not per iter — the 5MiB region zeroing would
                // dominate the measurement). The ring is reset between iters
                // so each sample starts empty.
                let (_buf, region, ring, slab) = setup(inline_size, capacity);
                let (region_addr, region_len) = region.ptr_and_len();
                let region_addr = region_addr as usize;
                let slab_addr = &slab as *const SlabAllocator as usize;
                let ring_offset = ring.offset();
                let owner = OwnerIdentity {
                    pid: std::process::id(),
                    start_time: 100,
                };

                let mut total = std::time::Duration::ZERO;
                for _ in 0..iters {
                    // Reset the ring so producers don't see leftover state.
                    // SAFETY: ring points into the live `_buf`.
                    unsafe { ring.reset(&region) };

                    let started = Arc::new(std::sync::Barrier::new(n + 1));
                    let done = Arc::new(std::sync::Barrier::new(n + 1));

                    let dur = std::thread::scope(|s| {
                        for _ in 0..n {
                            let started = started.clone();
                            let done = done.clone();
                            s.spawn(move || {
                                // SAFETY: reconstruct thread-local handles into
                                // the shared buffer (kept alive by `_buf` in the
                                // outer scope). usize is Send. Same pattern as
                                // ring.rs test_ring_mpmc_no_loss_no_dup.
                                let non_null = NonNull::new(region_addr as *mut u8).unwrap();
                                let region = unsafe { ShmRegion::new(non_null, region_len) };
                                let slab = unsafe { &*(slab_addr as *const SlabAllocator) };
                                let ring = Ring::new(ring_offset);
                                started.wait();
                                for _ in 0..per_thread {
                                    // SAFETY: ring/slab/region valid; buf alive.
                                    unsafe {
                                        while ring.try_enqueue(
                                            &region,
                                            slab,
                                            b"bench.test",
                                            b"hello",
                                            f64::MAX,
                                            owner,
                                        ) != EnqueueResult::Ok
                                        {
                                            std::thread::yield_now();
                                        }
                                    }
                                }
                                done.wait();
                            });
                        }
                        let start = std::time::Instant::now();
                        started.wait();
                        done.wait();
                        start.elapsed()
                    });
                    total += dur;
                }
                total
            })
        });
    }
    group.finish();
}

criterion_group!(
    benches,
    bench_ring_enqueue_uncontended,
    bench_ring_dequeue_uncontended,
    bench_ring_enqueue_contended,
);
criterion_main!(benches);
