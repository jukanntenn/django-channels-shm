"""Nox sessions for benchmarks + observability (P3 unified entry).

Run modes:
  release (default): python -O + Rust no features -> real production performance
  dev: Rust --features metrics,tracing_events + no -O
"""

from __future__ import annotations

import nox

nox.options.default_venv_backend = "none"


@nox.session(venv_backend="none")
def bench_python(session: nox.Session) -> None:
    """P1 layer 1-2: pytest-benchmark, RELEASE build (python -O, no observability)."""
    session.run(
        "python",
        "-O",
        "-m",
        "pytest",
        "bench/python/",
        "--benchmark-autosave",
        "--benchmark-disable-gc",
        *session.posargs,
    )


@nox.session(venv_backend="none")
def bench_rust(session: nox.Session) -> None:
    """P1 layer 0: criterion Rust microbenchmarks, release build."""
    session.run(
        "cargo",
        "bench",
        "--manifest-path",
        "crates/_channels_shm_native/Cargo.toml",
        "--",
        "--save-baseline",
        "main",
        *session.posargs,
        external=True,
    )


@nox.session(venv_backend="none")
def bench_cross(session: nox.Session) -> None:
    """P1 layer 3: cross-process multiprocessing benchmark."""
    session.run("python", "bench/cross_process/run_cross_process.py", *session.posargs)


@nox.session(venv_backend="none")
def bench_crash_injection(session: nox.Session) -> None:
    """X1 §15: crash injection + observability assertions. DEV build (observability ON)."""
    session.run(
        "python", "-m", "pytest", "bench/crash_injection/", "-v", *session.posargs
    )


@nox.session(venv_backend="none")
def test_observability(session: nox.Session) -> None:
    """X1: verify observability instrumentation itself works. DEV build."""
    session.run(
        "python", "-m", "pytest", "bench/", "-k", "observability", *session.posargs
    )


@nox.session(venv_backend="none")
def bench(session: nox.Session) -> None:
    """Aggregate: run all release benchmarks."""
    session.notify("bench_python")
    session.notify("bench_rust")
    session.notify("bench_cross")


@nox.session(venv_backend="none")
def check_regression(session: nox.Session) -> None:
    """P3 layer B: check criterion regression."""
    session.run("python", "bench/check_criterion_regression.py")


@nox.session(venv_backend="none")
def check_anchors(session: nox.Session) -> None:
    """P3 layer C: spec absolute anchor assertions."""
    session.run("python", "bench/check_anchors.py")
