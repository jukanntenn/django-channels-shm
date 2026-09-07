# AGENTS.md — the native crate

`_channels_shm_native` is the Rust hot path of django-channels-shm: lock-free rings, slab, indexes, the shared-memory layout, and the pyo3 bindings. Built as an abi3 extension via maturin; the Python side consumes it through the `_native.pyi` stub.

## Commands (run inside `crates/_channels_shm_native/`)

| Action | Command |
|--------|---------|
| Format check | `cargo fmt --check` |
| Lint | `cargo clippy --all-targets --all-features -- -D warnings` |
| Test | `cargo test` |
| Criterion benches | `cargo bench` |
| Native build (from repo root) | `uvx maturin develop --skip-install` |

`cargo fmt` / `clippy` / `cargo test` also run through the crate's own `prek.toml`. After changing Rust, rebuild the native module before Python tests run.

## Code style (Rust)

- `rustfmt` default style + `clippy -D warnings`.
- Target module size under 500 LoC; split if a file exceeds ~800 LoC.
- No `#[allow(...)]` without an inline comment justifying it.

## Contracts

- `src/layout.rs` is the shared-memory ABI — a single source of truth. Changing it is ask-first (AGENTS.md boundaries); migration-free changes corrupt every process sharing the region.
- The binding contract is `py_bindings.rs` + the `_native.pyi` stub in the Python package; the two change together with the Python call sites.
- Read both against their live source before relying on a signature — an ABI mismatch fails silently (see [PRINCIPLES](../../PRINCIPLES.md)).
