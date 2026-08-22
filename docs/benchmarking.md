# Benchmarking

English | [中文](benchmarking.zh.md)

Benchmarks live under `bench/`: `py/` (pytest-benchmark single-process), `xproc/` (cross-process scripts), `docker/` (pinned-env 3-way comparison), `checks/` (anchor/regression gates). Rust criterion benches live in the crate.

| nox session | What |
|--------|---------|
| `nox -s bench_py` | pytest-benchmark, single process |
| `nox -s bench_rust` | criterion benches inside the native crate |
| `nox -s bench_cross` | cross-process scripts |
| `nox -s bench` | all of the above |
| `nox -s check_regression` | regression gates |
| `nox -s check_anchors` | anchor gates |

Published numbers come from the pinned Docker environment (2 CPUs / 2 GB, `bench/docker/`); see [README benchmarks](../README.md#benchmarks) for the current tables and the reproduce commands. Run Python benchmarks with `python -O` so the release-mode layer is measured.
