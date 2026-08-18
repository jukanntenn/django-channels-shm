"""Nox sessions for benchmarks + recovery/observability tests.

Run modes:
  release (default): python -O + Rust no features -> real production performance
  dev: Rust --features metrics,tracing_events + no -O
"""

from __future__ import annotations

import nox

nox.options.default_venv_backend = "none"


@nox.session(venv_backend="none")
def bench_py(session: nox.Session) -> None:
    """Single-process pytest-benchmark suite, RELEASE build (python -O)."""
    session.run(
        "python",
        "-O",
        "-m",
        "pytest",
        "bench/py/",
        # -O strips asserts; pytest warns about that, which filterwarnings=error
        # would otherwise turn into a hard failure at collection time.
        "-W",
        "ignore::pytest.PytestConfigWarning",
        "--benchmark-autosave",
        "--benchmark-disable-gc",
        "--benchmark-json",
        "bench/results/py_latest.json",
        *session.posargs,
    )


@nox.session(venv_backend="none")
def bench_rust(session: nox.Session) -> None:
    """Criterion Rust microbenchmarks, release build."""
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
    """Cross-process scenario benchmarks (multiprocessing, slow)."""
    session.run("python", "-m", "bench.xproc.run_send_recv", *session.posargs)
    session.run("python", "-m", "bench.xproc.run_group_fanout", *session.posargs)


@nox.session(venv_backend="none")
def test_recovery(session: nox.Session) -> None:
    """Fault-injection recovery + observability assertions. DEV build (observability ON)."""
    session.run("python", "-m", "pytest", "tests/recovery/", "-v", *session.posargs)


@nox.session(venv_backend="none")
def test_observability(session: nox.Session) -> None:
    """Verify the observability instrumentation itself works. DEV build."""
    session.run("python", "-m", "pytest", "tests/obs/", *session.posargs)


@nox.session(venv_backend="none")
def bench(session: nox.Session) -> None:
    """Aggregate: run all release benchmarks."""
    session.notify("bench_py")
    session.notify("bench_rust")
    session.notify("bench_cross")


@nox.session(venv_backend="none")
def check_regression(session: nox.Session) -> None:
    """Check criterion regression (relative drift; >10% mean fails)."""
    session.run("python", "-m", "bench.checks.check_criterion_regression")


@nox.session(venv_backend="none")
def check_anchors(session: nox.Session) -> None:
    """Assert absolute anchors: criterion Rust microbenchmarks + py suite."""
    session.run("python", "-m", "bench.checks.check_criterion_anchors")
    session.run("python", "-m", "bench.checks.check_py_anchors")
