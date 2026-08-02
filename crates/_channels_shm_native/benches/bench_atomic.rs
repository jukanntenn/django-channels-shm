//! P1 layer 0: atomic primitive microbenchmarks (criterion).

use criterion::{black_box, criterion_group, criterion_main, Criterion};
use std::ptr::NonNull;

use _channels_shm_native::ShmRegion;

fn bench_setup_region(size: usize) -> (Vec<u64>, ShmRegion) {
    let words = size.div_ceil(8);
    let buf = vec![0u64; words];
    let ptr = buf.as_ptr() as *mut u8;
    let non_null = NonNull::new(ptr).unwrap();
    let region = unsafe { ShmRegion::new(non_null, size) };
    (buf, region)
}

fn bench_atomic_load_store(c: &mut Criterion) {
    let (_buf, region) = bench_setup_region(4096);
    let offset = 0usize;
    unsafe { region.store_u64(offset, 42) };
    c.bench_function("atomic_load_store", |b| {
        b.iter(|| unsafe {
            let v = region.load_u64(black_box(offset));
            region.store_u64(black_box(offset), v.wrapping_add(1));
        });
    });
}

fn bench_atomic_cas_uncontended(c: &mut Criterion) {
    let (_buf, region) = bench_setup_region(4096);
    let offset = 0usize;
    unsafe { region.store_u64(offset, 0) };
    c.bench_function("atomic_cas_uncontended", |b| {
        b.iter(|| unsafe {
            let cur = region.load_u64(offset);
            let _ = region.cas_u64(offset, cur, cur.wrapping_add(1));
        });
    });
}

fn bench_atomic_fetch_add(c: &mut Criterion) {
    let (_buf, region) = bench_setup_region(4096);
    let offset = 0usize;
    unsafe { region.store_u64(offset, 0) };
    c.bench_function("atomic_fetch_add", |b| {
        b.iter(|| unsafe {
            region.fetch_add_u64(black_box(offset), 1);
        });
    });
}

fn bench_atomic_cas_contended(c: &mut Criterion) {
    let (buf, region) = bench_setup_region(4096);
    let offset = 0usize;
    unsafe { region.store_u64(offset, 0) };
    c.bench_function("atomic_cas_contended_4threads", |b| {
        b.iter(|| {
            unsafe { region.store_u64(offset, 0) };
            let base_addr = buf.as_ptr() as usize;
            let handles: Vec<_> = (0..4)
                .map(|_| {
                    let addr = base_addr;
                    std::thread::spawn(move || {
                        let non_null = NonNull::new(addr as *mut u8).unwrap();
                        let r = unsafe { ShmRegion::new(non_null, 4096) };
                        for _ in 0..1000 {
                            unsafe { r.fetch_add_u64(offset, 1) };
                        }
                    })
                })
                .collect();
            for h in handles {
                h.join().unwrap();
            }
        });
    });
}

criterion_group!(
    benches,
    bench_atomic_load_store,
    bench_atomic_cas_uncontended,
    bench_atomic_fetch_add,
    bench_atomic_cas_contended
);
criterion_main!(benches);
