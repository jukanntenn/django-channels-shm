# 3-way benchmark harness (channels-shm vs channels_redis vs InMemory)

Runs all three channel layers in one Docker container pinned to
**2 CPUs / 2 GB RAM** — the hardware constraint documented in
`README.md` / `README.zh-CN.md`.

## Run

    cd bench/docker
    docker compose build
    docker compose run --rm bench        # prints the full JSON summary

The container:

1. builds the native wheel (maturin, release),
2. starts a local `redis-server` (for the `channels_redis` baseline),
3. measures:
   - `InMemoryChannelLayer` single-process send+receive roundtrip
   - `channels_shm` single-process roundtrip
   - `channels_shm` cross-process S2 (send/recv) and S4 (group fan-out, 4 workers)
   - `channels_redis` S2 and S4 (same scenarios)

## Results

`results/` holds the published result snapshots (median of several runs).
`results/2026-08-02.json` was generated on 2026-08-02 and is the source of
the numbers in the READMEs.
