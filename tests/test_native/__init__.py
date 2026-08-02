"""Pytest integration tests for the Rust native extension (`channels_shm._native`).

These tests exercise the PyO3 bindings end-to-end from Python. They complement
the Rust-side `cargo test` unit tests by verifying type conversions, the
Python-visible API surface, and the contract of each exposed function.
"""
