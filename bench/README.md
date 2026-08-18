# Benchmarks

Performance suites for channels-shm. The product story being measured: a
single-machine multi-process channel layer that replaces the in-memory or
Redis layers for Django Channels, with latency in the tens of microseconds.

## Layout

| Directory     | What it measures                                        | When to run          |
|---------------|---------------------------------------------------------|----------------------|
| `py/`         | single-process layer ops (send/receive/roundtrip/group/serializer) via pytest-benchmark | every release, on change |
| `xproc/`      | cross-process send/recv and group fanout (multiprocessing) | release acceptance  |
| `docker/`     | reproducible 3-way comparison (shm vs redis vs InMemory) pinned to 2 CPU / 2 GB | published numbers   |
| `checks/`     | gate scripts: criterion + pytest-benchmark anchors and regression | CI / release gates   |
| `baselines/`  | committed absolute anchors (`anchors.json`) and named snapshots | —                   |
| `results/`    | gitignored run artifacts (xproc snapshots, `--benchmark-json`) | —                   |

## Run

All benches must run in release mode (`python -O`): the observability block
inside the layer is compiled out only then, so dev-mode numbers are not
representative.

    nox -s bench_py      # single-process pytest-benchmark suite (fast)
    nox -s bench_cross   # cross-process send/recv + group fanout (slow)
    nox -s bench         # py + rust(crate) + cross
    nox -s check_anchors     # absolute anchors (criterion + py)
    nox -s check_regression  # criterion relative regression

The Rust microbenchmarks live in the crate (`cargo bench`, criterion) — see
`crates/_channels_shm_native/benches/`.

## How to read the results

- `bench_py` prints a pytest-benchmark table. Read the **median/IQR**, not the
  mean: these are async operations with real I/O (eventfd wakes, loop steps),
  so the mean is inflated by scheduler noise. The InMemory rows are the
  reference the shm layer competes against — compare rows within the same
  group, not absolute numbers.
- Every run is auto-saved to `.benchmarks/` (with commit id and machine info).
  Compare runs: `pytest-benchmark compare`, or fail on drift with
  `--benchmark-compare-fail=median:5%` against a saved baseline.
- `bench_cross` writes snapshots to `bench/results/` (gitignored) with the
  machine/build context embedded; they are for release acceptance, not CI.
- `bench/docker` is the only source of published README numbers: it pins the
  hardware so cross-layer and cross-run comparisons are valid.

## Adding a benchmark

- Message tiers and the layer config are the single source in `bench/common.py`;
  `bench/py/test_tiers.py` asserts tier names stay truthful about the
  inline/overflow boundary as the serializer changes.
- Pure-producer benches (send, group_send) must use fixed pedantic rounds and
  assert the enqueue budget stays below ring capacity — calibration-mode
  iteration counts grow with machine speed and would push the ring into the
  emergency-drain path (see `bench/py/test_send.py`).
- Consumer benches (receive) use `benchmark.pedantic(setup=...)`: the setup
  enqueues outside the timer; the timed call runs the loop so the pump drain
  lands inside the measurement.
