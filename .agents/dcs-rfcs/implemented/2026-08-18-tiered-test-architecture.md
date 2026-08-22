# DCS-RFC: A tiered test architecture — fast default, real environments behind markers

Status: implemented

English | [中文](2026-08-18-tiered-test-architecture.zh.md)

## Problem

The suite had grown into one mixed mass: an 812-line `tests/test_layer.py` carrying unit, property, and integration cases side by side, fault-injection scenarios (a worker SIGKILLed mid-enqueue, PID reuse, a stuck watchdog pump) without a home, and Docker-stack e2e sharing a directory with sub-second unit tests. One flat suite forces a false choice: either every edit pays the slowest test's latency, or the real-environment tests get skipped and rot. Mock-heavy fast suites dodge the latency but stop testing the thing that ships — the shared-memory region's cross-process semantics.

## Decision

Tests split into tiers by environment fidelity, gated by pytest markers, with `tests/` mirroring `src/channels_shm/`: the default run (`uv run pytest -m "not slow and not e2e"`) covers unit, Hypothesis property, and stateful machine tests against the real region in `/dev/shm`; `@slow` `tests/cross_process/` runs real `multiprocessing` interop; `@slow` `tests/recovery/` is the fault-injection tier — fork/SIGKILL harnesses (`_workers.py`, `conftest.py`) driving owner-crash, slot-reclaim, PID-reuse, seqlock-staleness, and watchdog scenarios; `@e2e` `tests/e2e/` exercises the real Django/channels stack under docker compose with three ASGI workers. The fast tier stays the pre-push and default-CI gate (mirroring CI's test job); slow and e2e stay CI-only by design. Local and CI run the same suite as fully as feasible — no in-memory stand-in for the thing under test, mocks only at the request boundary.

## Alternatives considered

**One flat suite, everything on every run.** It lost: the recovery and e2e tiers cost seconds-to-minutes per case; making them the price of every unit-test loop guarantees they get skipped locally, which is the rot this restructure exists to prevent.

**Mock the region for speed.** It lost: the layer's contract is cross-process shared-memory semantics — ownership, reclamation, wakeup — and a mock replaces exactly the subject under test; the fast tier is already fast because the real region is a memory mapping, not because anything is faked.

**Keep growing `test_layer.py`.** It lost: an 812-line module with no internal boundaries hides which behavior is covered by which tier and makes the property/stateful/concurrency split unreviewable; the restructure gave each kind its own file and each tier its own directory.

## Consequences

Feedback latency matches fidelity: sub-second default runs, opt-in slow/e2e tiers that CI always exercises. The marker split is contract — `pyproject.toml` registers `slow`/`e2e`, pre-push runs the fast subset, and the AGENTS command table carries the three invocations. The restructure itself (116 files, +8424/−6029) landed with the suite green, and the recovery tier now owns the crash-safety claims the [ring + slab decision](./2026-08-02-lock-free-ring-slab-over-broker.md) makes.
