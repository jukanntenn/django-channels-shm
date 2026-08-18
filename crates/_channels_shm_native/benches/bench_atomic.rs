//! Shared-memory atomic primitive microbenchmarks (criterion).
//!
//! The ring hot paths are built from exactly four `ShmRegion` primitives —
//! Acquire `load_u64`, Release `store_u64`, AcqRel `fetch_add_u64` and AcqRel
//! `cas_u64` — so this file measures each in isolation, both uncontended (raw
//! single-thread cost) and contended (the cache-line bouncing cost of a
//! counter shared by every process). The contended variants exist because
//! that bouncing is the product's scalability ceiling and must be measured,
//! not assumed.
//!
//! Why these measurement shapes:
//! - Uncontended benches use `iter`: its zero overhead is the only thing
//!   adequate for nanosecond-range ops; `iter_batched`/`PerIteration` would
//!   add more overhead than the op itself.
//! - The "CAS success" pattern is load-then-CAS, not pure CAS: a claim needs
//!   a fresh expected value every try, and `iter_batched` cannot reset the
//!   same shared location (batch inputs are all collected before any routine
//!   runs, so only the first CAS per batch would succeed).
//! - Contended benches use `common::run_contended` (see common.rs): one
//!   persistent pool per sample, cyclic barriers, and a constant number of
//!   attempts per iteration across thread counts so `N` results are
//!   comparable; throughput counts attempts, since a failed CAS still pays
//!   the full RMW cost.

mod common;

use std::hint::black_box;
use std::sync::Arc;

use criterion::{criterion_group, criterion_main, BenchmarkId, Criterion, Throughput};

use common::{make_region, run_contended};

use _channels_shm_native::ShmRegion;

/// Total RMW attempts per iteration for the contended benches, independent of
/// thread count. At ~10-50 ns/op this yields microsecond-scale iterations, so
/// barrier + loop overhead stays a few percent of the measurement.
const CONTENDED_OPS_PER_ITER: u64 = 4096;

/// Doubling progression: enough to expose the cache-line-bounce scaling curve
/// without over-subscribing the host.
const CONTENDED_THREAD_COUNTS: [usize; 3] = [2, 4, 8];

fn bench_atomic_load(c: &mut Criterion) {
    let (_buf, region) = make_region(4096);
    let offset = 0usize;
    // SAFETY: offset valid.
    unsafe { region.store_u64(offset, 42) };
    c.bench_function("atomic_load", |b| {
        b.iter(|| unsafe {
            // black_box the result: an unused load could otherwise be elided.
            black_box(region.load_u64(black_box(offset)))
        });
    });
}

fn bench_atomic_store(c: &mut Criterion) {
    let (_buf, region) = make_region(4096);
    let offset = 0usize;
    // SAFETY: offset valid.
    unsafe { region.store_u64(offset, 42) };
    c.bench_function("atomic_store", |b| {
        b.iter(|| unsafe {
            // The store itself cannot be elided, but black_box stops the
            // compiler from hoisting the address/value out of the loop.
            region.store_u64(black_box(offset), black_box(1u64));
        });
    });
}

fn bench_atomic_fetch_add(c: &mut Criterion) {
    let (_buf, region) = make_region(4096);
    let offset = 0usize;
    // SAFETY: offset valid.
    unsafe { region.store_u64(offset, 0) };
    c.bench_function("atomic_fetch_add", |b| {
        b.iter(|| unsafe {
            // black_box the address: the RMW cannot be elided, but a constant
            // address is what lets the compiler be tempted to fuse iterations.
            region.fetch_add_u64(black_box(offset), 1);
        });
    });
}

fn bench_atomic_cas_success(c: &mut Criterion) {
    let (_buf, region) = make_region(4096);
    let offset = 0usize;
    // SAFETY: offset valid.
    unsafe { region.store_u64(offset, 0) };
    c.bench_function("atomic_cas_success", |b| {
        b.iter(|| unsafe {
            // Using the loaded value as `expected` keeps the CAS always
            // succeeding without a per-iteration reset — see module header.
            let cur = black_box(region.load_u64(black_box(offset)));
            let _ = region.cas_u64(black_box(offset), cur, cur.wrapping_add(1));
        });
    });
}

/// Reset the shared counter while every worker is parked on the start
/// barrier; SAFETY holds because no worker can be mid-RMW at that point.
fn contended_reset(region: &Arc<ShmRegion>, offset: usize) {
    unsafe { region.store_u64(offset, 0) };
}

fn bench_contended(c: &mut Criterion, group_name: &str, use_cas: bool) {
    let (_buf, region) = make_region(4096);
    let offset = 0usize;
    // SAFETY: offset valid.
    unsafe { region.store_u64(offset, 0) };
    let region = Arc::new(region);

    let mut group = c.benchmark_group(group_name);
    group.throughput(Throughput::Elements(CONTENDED_OPS_PER_ITER));
    for &n in &CONTENDED_THREAD_COUNTS {
        let per_thread = (CONTENDED_OPS_PER_ITER / n as u64) as usize;
        group.bench_with_input(BenchmarkId::from_parameter(n), &n, |b, &n| {
            let worker_region = Arc::clone(&region);
            let worker: Arc<dyn Fn() + Send + Sync> = Arc::new(move || {
                if use_cas {
                    // Claim-style load + single-shot CAS: attempts fail under
                    // contention, but every attempt pays the same RMW cost.
                    // SAFETY: offset valid; region alive via Arc.
                    let cur = unsafe { worker_region.load_u64(offset) };
                    let _ = unsafe { worker_region.cas_u64(offset, cur, cur.wrapping_add(1)) };
                } else {
                    // Fetch-add: every attempt succeeds, so throughput counts
                    // completed RMWs. SAFETY: offset valid; region alive via Arc.
                    unsafe { worker_region.fetch_add_u64(offset, 1) };
                }
            });
            let reset_region = Arc::clone(&region);
            let mut reset = move || contended_reset(&reset_region, offset);
            b.iter_custom(|iters| {
                run_contended(Arc::clone(&worker), &mut reset, n, per_thread, iters)
            });
        });
    }
    group.finish();
}

fn bench_atomic_fetch_add_contended(c: &mut Criterion) {
    bench_contended(c, "atomic_fetch_add_contended", false);
}

fn bench_atomic_cas_contended(c: &mut Criterion) {
    bench_contended(c, "atomic_cas_contended", true);
}

criterion_group!(
    benches,
    bench_atomic_load,
    bench_atomic_store,
    bench_atomic_fetch_add,
    bench_atomic_cas_success,
    bench_atomic_fetch_add_contended,
    bench_atomic_cas_contended,
);
criterion_main!(benches);
