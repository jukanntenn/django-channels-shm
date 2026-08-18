//! Shared helpers for the criterion microbenchmark suite.

use std::ptr::NonNull;
use std::sync::{Arc, Barrier};
use std::time::{Duration, Instant};

use criterion::Bencher;

use _channels_shm_native::ShmRegion;

/// Each bench is compiled as its own crate (criterion `harness = false`), so
/// helpers unused by one bench look dead there; the allow is per-crate.
#[allow(dead_code)]
/// Build a `size`-byte region. `buf` is returned alongside so it outlives the
/// region (the `ShmRegion` holds a raw pointer into it).
pub fn make_region(size: usize) -> (Vec<u64>, ShmRegion) {
    let words = size.div_ceil(8);
    let buf = vec![0u64; words];
    let ptr = buf.as_ptr() as *mut u8;
    let non_null = NonNull::new(ptr).unwrap();
    // SAFETY: buf is 8-byte aligned (Vec<u64>) and valid for `size` bytes.
    let region = unsafe { ShmRegion::new(non_null, size) };
    (buf, region)
}

#[allow(dead_code)]
/// Time `op` in chunks of `chunk` iterations, running `reset` once before each
/// chunk, outside the timed window.
///
/// The reset cost amortizes to 1/chunk of the measurement, so a per-chunk
/// ring reset is negligible per op. This exists because `iter_batched`
/// collects all batch inputs before any routine runs: a side-effect reset of
/// shared state cannot be interleaved per-routine there, and running it once
/// per input would dominate the wall-clock time.
pub fn chunked_iter(b: &mut Bencher, chunk: u64, mut reset: impl FnMut(), mut op: impl FnMut()) {
    b.iter_custom(|iters| {
        let mut total = Duration::ZERO;
        let mut remaining = iters;
        while remaining > 0 {
            let n = remaining.min(chunk);
            reset();
            let t0 = Instant::now();
            for _ in 0..n {
                op();
            }
            total += t0.elapsed();
            remaining -= n;
        }
        total
    });
}

#[allow(dead_code)]
/// Run `n` worker threads for `iters` iterations, each calling `worker`
/// `per_thread` times per iteration; returns total wall-clock time.
///
/// The pool is spawned once per sample, before the timed loop, so thread
/// creation never pollutes the measurement. A start barrier releases all
/// workers simultaneously, so they hit the shared cache line at the same
/// time; a done barrier lets the main thread wait for completion. `reset`
/// runs while every worker is parked on the start barrier, so it never races
/// the workers. `n * per_thread` is constant across thread counts, keeping
/// per-iteration work comparable.
pub fn run_contended(
    worker: Arc<dyn Fn() + Send + Sync>,
    reset: &mut dyn FnMut(),
    n: usize,
    per_thread: usize,
    iters: u64,
) -> Duration {
    let start = Arc::new(Barrier::new(n + 1));
    let done = Arc::new(Barrier::new(n + 1));

    let handles: Vec<_> = (0..n)
        .map(|_| {
            let worker = Arc::clone(&worker);
            let start = Arc::clone(&start);
            let done = Arc::clone(&done);
            std::thread::spawn(move || {
                for _ in 0..iters {
                    start.wait();
                    for _ in 0..per_thread {
                        worker();
                    }
                    done.wait();
                }
            })
        })
        .collect();

    let mut total = Duration::ZERO;
    for _ in 0..iters {
        reset();
        let t0 = Instant::now();
        start.wait();
        done.wait();
        total += t0.elapsed();
    }
    for h in handles {
        h.join().unwrap();
    }
    total
}
