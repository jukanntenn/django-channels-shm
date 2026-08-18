# tests/ — organization guide

The test tree uses two top-level groupings with a clear rule for each.

## 1. Mirror of `src` (unit / integration)

Test files mirror the package layout one-to-one, so a source module's tests
are trivial to find:

| source | tests |
|---|---|
| `src/channels_shm/layer.py` | `tests/layer/` (split by concern) |
| `src/channels_shm/pump.py` | `tests/test_pump.py` |
| `src/channels_shm/serializer.py` | `tests/test_serializer.py` |
| `src/channels_shm/inspect.py` | `tests/test_inspect.py` |
| `src/channels_shm/channel/…` | `tests/channel/` |
| `src/channels_shm/group/…` | `tests/group/` |
| `src/channels_shm/shm/…` | `tests/shm/` |
| `src/channels_shm/_obs/…` | `tests/obs/` (leading underscore dropped: it marks "private" in `src`, meaningless in tests) |
| `src/channels_shm/_native` (Rust, via pyo3) | `tests/native/` |

Large modules get a directory split by concern (e.g. `tests/layer/`); the
files are still named `test_<concern>.py`.

## 2. Behavior / scenario layers

Cross-cutting scenarios that don't map to one module:

- `stateful/` — model-based differential tests (IUT vs `InMemoryChannelLayer`)
- `cross_process/` — normal multi-process interop (`@slow`, Linux-only)
- `recovery/` — fault injection (fork/SIGKILL, corrupted states) + observability (`@slow`, Linux-only)
- `e2e/` — Django/channels stack via docker compose (`@e2e`)

## Naming rules

- File names never repeat the directory name: `tests/cross_process/test_group.py`,
  not `test_cross_process_group.py`.
- Shared helpers live in underscored modules (`_helpers.py`, `_workers.py`,
  `_types.py`) — they are not test modules, so they must not match
  `test_*.py` or pytest would collect them.
- Hypothesis strategies live in `tests/strategies.py` (shared, single source).

## Marker policy

- Fast (default): everything except `slow` and `e2e`.
- `slow`: multiprocessing / fork-based tests (Linux only).
- `e2e`: docker-based end-to-end (opt-in; requires the compose stack).
- Benchmarks are NOT collected by the default run (`testpaths = ["tests"]`);
  they run only via `nox -s bench_py` in the release build.

# Running the tests

## Prerequisites

    uv sync                       # install test deps (pytest, hypothesis, pytest-asyncio, …)
    uvx maturin develop --skip-install   # build _native.abi3.so into src/ (required)
    uv tool install prek && prek install # commit/push/commit-msg hooks (optional locally)

The native module MUST be built before any test or type-check passes.

## Quick reference

| What | Command | Notes |
|---|---|---|
| Fast suite (default) | `uv run pytest -m "not slow and not e2e"` | ~3 s; the pre-push gate |
| Cross-process + recovery | `uv run pytest -m slow` | fork/SIGKILL; Linux only; ~3 s |
| Everything | `uv run pytest` | fast + slow; e2e auto-skips w/o websockets |
| Release (`-O`) smoke | `uv run python -O -m pytest tests/layer/ tests/test_serializer.py tests/native/ -W "ignore::pytest.PytestConfigWarning"` | CI-only gate; core paths w/o observability |
| Recovery only | `uv run pytest tests/recovery/` | fault-injection + metrics assertions |
| Observability only | `uv run pytest tests/obs/` | config / logging_setup / metrics |
| Native bindings | `uv run pytest tests/native/` | pyo3 FFI surface |
| e2e (Docker) | see below | needs `docker compose` + `websockets` |

Run a single file / nodeid:

    uv run pytest tests/layer/test_send_receive.py -k expiry
    uv run pytest "tests/stateful/test_channel_layer_machine.py::TestChannelLayerMachine::runTest"

## What each area covers

- `tests/layer/` — layer public API: config (`test_config`), lifecycle/close/
  flush/compact + loop binding (`test_lifecycle`), ASGI send/receive + expiry +
  size limits (`test_send_receive`), groups (`test_groups`), wakeup routing +
  orphan cleanup (`test_wakeup`), `new_channel` (`test_new_channel`).
- `tests/channel/`, `tests/group/`, `tests/shm/` — managers, validators, region/
  lock/wakeup primitives (mirror of `src`).
- `tests/obs/` — observability: config paths, per-pid JSONL logging, metrics.
- `tests/native/` — Rust extension via pyo3 (atomic/ring/slab/index/layout/
  registry/group_members/shm_init). Layout offsets come from `tests/layout_helpers.py`.
- `tests/stateful/` — Hypothesis `RuleBasedStateMachine` differential test
  (shm vs `InMemoryChannelLayer`). Uses a persistent loop thread.
- `tests/cross_process/` — normal multi-process send/receive + group fanout.
- `tests/recovery/` — dead-owner slot recovery, stale-odd seqlock repair,
  watchdog stall detection (+ metric assertion), PID-reuse, dead-slot reclaim,
  concurrent herd recovery. Worker functions live in `_workers.py` (spawn-safe).
- `tests/e2e/` — real Django + channels stack across workers (docker).

## Hypothesis

Property tests run under the default profile (100 examples). To tighten locally:

    uv run pytest tests/test_serializer.py -k int64 --hypothesis-seed 0   # deterministic

The stateful machine is bounded via `settings(stateful_step_count=20, max_examples=20)`
in `tests/stateful/test_channel_layer_machine.py`.

## e2e (Docker)

    cd tests/e2e
    docker compose build
    docker compose up -d worker1 worker2 worker3
    docker compose run --rm runner pytest tests/e2e/ -v
    docker compose down -v

Requires the `websockets` package in the test env (pytest `importorskip` skips
the module otherwise). The runner container sets `WORKER_HOSTS` for the
cross-worker tests.

## Nox sessions

    nox -s test_recovery     # fault-injection, DEV build (observability ON)
    nox -s test_observability
    nox -s bench_py          # release build (-O) pytest-benchmark -> bench/results/
    nox -s bench_rust        # criterion, saves baseline "main"
    nox -s bench_cross       # multiprocessing scenario benchmarks
    nox -s bench             # all three benches
    nox -s check_regression  # criterion drift >10% fails
    nox -s check_anchors     # absolute anchors (rust + py)

## Quality gates (prek — what CI runs)

    prek run --all-files                # the full CI gate
    prek run --group format --all-files # mutating fixers (ruff format, …)
    prek run --group lint --all-files   # read-only (ruff check --fix, typos, …)
    prek run --group check --all-files  # uv.lock / Cargo.lock / version freshness
    prek run --group format --group lint --files <path>   # per-file (as AI hooks do)

## Type check (committed progressive baseline)

    uv run basedpyright --baselinefile .basedpyright-baseline.json

Fails only on NEW errors vs the committed baseline. After intentional error
changes, regenerate it:

    uv run basedpyright --writebaseline --baselinefile .basedpyright-baseline.json

(The codebase absorbs known multiprocessing-stub `Unknown` errors this way —
matches the established cross_process convention.)

## Troubleshooting

- **e2e modules skipped**: `websockets` not installed — `uv sync --group dev` or
  install it; without the docker stack they skip anyway via host-reachability.
- **`ModuleNotFoundError: No module named 'tests'`** in a spawned child: the
  worker function must live in `tests/.../_workers.py` (which adds the repo
  root to `sys.path`). Don't define spawn targets inline in test modules.
- **Leftover `/dev/shm` files**: each test uses a unique prefix; the fixtures
  (`layer`, `layer_factory`, `recv_cleanup`, `xproc_cleanup`) remove
  `{prefix}`, `{prefix}_wakeup`, `{prefix}_obs`. If a run is killed mid-test,
  clean manually: `rm -rf /dev/shm/test_* /dev/shm/xproc_* /dev/shm/recv_*`.
- **Parallel runs (xdist)**: safe because prefixes are unique; recovery/
  cross_process use unique prefixes too (never a fixed name).
- **Watchdog test flakiness**: `tests/recovery/test_watchdog_pump_stuck.py`
  needs `_watchdog_armed = True` and a settle sleep after send before corrupting
  `last_drain_ts`; if you change it, keep that ordering.
