# django-channels-shm

English | [中文](README.zh.md)

[![CI](https://github.com/jukanntenn/django-channels-shm/actions/workflows/ci.yml/badge.svg)](https://github.com/jukanntenn/django-channels-shm/actions/workflows/ci.yml)
[![Documentation Status](https://readthedocs.org/projects/django-channels-shm/badge/?version=latest)](https://django-channels-shm.readthedocs.io/en/latest/?badge=latest)
[![License: BSD-3-Clause](https://img.shields.io/badge/License-BSD_3--Clause-blue.svg)](LICENSE)

A high-performance **shared-memory channel layer for Django Channels**, designed for single-machine multi-process deployments. Messages travel between ASGI workers through an `mmap(MAP_SHARED)` region in `/dev/shm` — no Redis, no TCP, no broker — while the hot path runs in a **Rust native extension (PyO3)**.

```
ASGI worker A ──send──► ┌───────────────────────────────┐ ──receive──► ASGI worker B
                        │  /dev/shm (MAP_SHARED)        │
                        │  lock-free MPMC rings + slab  │
                        │  channel/group indexes        │
                        │  eventfd / AF_UNIX wakeup     │
                        └───────────────────────────────┘
```

## Documentation

User documentation — installation, quickstart, concepts, configuration, and the auto-generated API reference — is published at [django-channels-shm.readthedocs.io](https://django-channels-shm.readthedocs.io/en/latest/); the sources live in `docs/site/`.

## Features

- **Zero-copy shared memory**: channels and groups live in one shared region; messages under `inline_size` are written directly into ring slots (no allocation, no serialization hop).
- **Lock-free hot path**: Vyukov bounded MPMC ring buffer implemented in Rust for `send`/`receive`, with per-slot sequence numbers.
- **Crash recovery**: every slot tracks its owner (`pid` + process start time). Dead owners are detected and their rings/slots are safely reclaimed — a worker that dies mid-message never blocks the others.
- **Event-driven wakeup**: `eventfd` for intra-process and `AF_UNIX` datagram sockets for cross-process notification. No polling, no busy-wait.
- **Complete channels API**: `send` / `receive` / `new_channel` / `group_add` / `group_discard` / `group_send` / `flush`, process-specific channels (`!`-suffix), per-channel capacity overrides and message expiry.
- **Observable in dev, fast in prod**: watchdog, structured logs and metrics in debug builds; `python -O` strips them completely.
- **Tested hard**: unit, Hypothesis property, stateful machine, concurrency, cross-process, and Docker e2e suites (see [Testing](#testing)).

## Requirements

- **Linux** (x86-64; AArch64 best-effort) — `MAP_SHARED` + `AF_UNIX`
- **Python ≥ 3.11**
- **Rust ≥ 1.86** (only needed to build the native extension)
- **channels ≥ 4.0** (the only runtime dependency — Django itself is not a dependency of the layer; run it from any Django/ASGI stack that channels supports)

Tested on Python 3.11–3.13 × channels ≥ 4.0 on Linux x86-64 (CI matrix).

## Installation

Not published to PyPI yet; install from GitHub (a Rust toolchain is required — maturin builds the `abi3` wheel during install):

```bash
pip install git+https://github.com/jukanntenn/django-channels-shm.git
```

### Development setup

```bash
uv sync
uvx maturin develop --skip-install   # builds _native.abi3.so into src/
```

The native module must be built before any test or type-check can run.

## Quickstart

```python
# settings.py
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_shm.SharedMemoryChannelLayer",
        "CONFIG": {
            "capacity": 100,
            "shm_size": 256 * 1024 * 1024,
        },
    },
}
```

All ASGI workers on the same machine share one region: instantiate the layer with the same `prefix` (default `"channels_shm"`). No server to start — the region and wakeup sockets are created lazily in `/dev/shm`.

### Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `prefix` | `"channels_shm"` | Namespace for the shm region + wakeup sockets. Max 53 chars (the wakeup socket path `/dev/shm/{prefix}_wakeup/{client}.sock` must fit the 108-byte AF_UNIX limit). |
| `capacity` | `100` | Default per-channel ring capacity (messages). |
| `channel_capacity` | `None` | `{regex: capacity}` overrides, e.g. `{"^video\.": 1000}`. |
| `expiry` | `60` | Message expiry in seconds. |
| `group_expiry` | `86400` | Group membership expiry in seconds. |
| `shm_size` | `256 MiB` | Max size of the shared region. |
| `inline_size` | `512` | Messages ≤ this size are stored inline in the ring slot. |
| `max_channels` | `10000` | Max channel index entries. |
| `max_groups` | `1000` | Max group index entries. |
| `max_processes` | `4096` | Max distinct processes tracked in the registry. |
| `max_members_per_group` | `1024` | Max members per group. |
| `watchdog_interval` | `30` | Watchdog sweep interval in seconds (`None` disables). |
| `obs_dir` | `None` | Observability output dir (metrics/logs; debug builds only). |

## Benchmarks

Published numbers are generated inside a Docker container pinned to **2 CPUs / 2 GB RAM** (`bench/docker/docker-compose.yml`) — the same container runs all three channel layers, with a local `redis-server` for the `channels_redis` baseline. Release mode (`python -O`).

| Scenario (2 CPUs / 2 GB, 50 B message) | InMemory | django-channels-shm | channels_redis |
|----------------------------------------|---------:|-------------:|---------------:|
| Single-process send+receive roundtrip  | 111k ops/s | 60k ops/s | — |
| Cross-process send, S2 (2 processes)   | — | 119k msg/s | 1.9k msg/s |
| Group fan-out, S4 (4 receivers)        | — | 10.5k msg/s | 672 msg/s |

- **~61×** higher cross-process send throughput than `channels_redis`
- **~16×** higher group fan-out throughput than `channels_redis`
- Single-process roundtrip is within ~1.8× of the pure in-memory layer — the cost of being able to share messages between *processes*.

Latency detail (median of 7 runs):

| Scenario | Layer | send p50 / p99 | recv p50 |
|----------|-------|---------------:|---------:|
| S2 cross-process | django-channels-shm | 7.4 µs / 35 µs | 2.0 ms |
| S2 cross-process | channels_redis | 490 µs / 862 µs | 19.2 ms |
| Roundtrip (single process) | InMemory | 8.3 µs / 27 µs | — |
| Roundtrip (single process) | django-channels-shm | 15.0 µs / 53 µs | — |

> `recv` latency under this harness includes queueing delay: the sender blasts `count` messages without backpressure, so the receiver drains a backlog. Send-side numbers are the clean comparison; full per-run JSON is committed in `bench/docker/results/` and the methodology lives in [docs/benchmarking.md](docs/benchmarking.md).

### Reproduce

```bash
cd bench/docker
docker compose build
docker compose run --rm bench        # prints the full JSON summary
```

## Example app

[`examples/chat`](examples/chat/README.md) is a WeChat-style multi-process Django + Channels chat with **zero infrastructure** — no Redis, no database. It doubles as the pre-release acceptance project: `uv sync` there builds django-channels-shm from the working tree through maturin, and `manage.py demo_broadcast` asserts cross-process fan-out headlessly.

```bash
cd examples/chat
uv sync                                   # builds django-channels-shm from ../.. via maturin
uv run uvicorn chat.asgi:application --workers 3 --port 8000
uv run python manage.py demo_broadcast    # headless acceptance: must print PASSED
```

Open <http://127.0.0.1:8000/> in several tabs, pick nicknames, and chat — private chats by nickname, group chats by group name (max 500 members). All tabs hit the same port; the kernel spreads connections over the worker processes, and every message crosses them via `/dev/shm` (hover a message to see which worker PID delivered it). Screenshots and internals: [`examples/chat/README.md`](examples/chat/README.md).

## Testing

```bash
# fast unit / property / concurrency suite (no docker)
uv run pytest -m "not slow and not e2e"

# cross-process integration (Linux, multiprocessing)
uv run pytest -m slow

# Django/channels stack e2e — 3 ASGI workers via docker compose
cd tests/e2e
docker compose build
docker compose up -d worker1 worker2 worker3
docker compose run --rm runner pytest tests/e2e/ -v
```

## Development

| Action | Command |
|--------|---------|
| Format | `uv run ruff format .` |
| Lint | `uv run ruff check .` |
| Type check | `uv run basedpyright` (progressive baseline, CI fails only on new errors) |
| Pre-commit | `prek run --all-files` |
| Rust format / lint / test | `cargo fmt --check && cargo clippy --all-targets --all-features -- -D warnings && cargo test` |

The full daily workflow — gate groups, the type-check baseline, formatting — lives in [docs/development.md](docs/development.md).

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs `prek run --all-files`, basedpyright (Python 3.12–3.13), the fast test suite on Python 3.11–3.13, a `python -O` release-mode smoke, the slow cross-process suite, the Docker e2e stack, `cargo test`, and a maturin wheel build.

## Community & support

- **Questions about usage, configuration, or tuning** → [GitHub Discussions](https://github.com/jukanntenn/django-channels-shm/discussions) (Q&A category; search first). Valuable discussions get distilled into a DCS-RFC.
- **Reproducible bugs and feature requests** → [GitHub issues](https://github.com/jukanntenn/django-channels-shm/issues/new/choose) — the templates guide you; see [SUPPORT](.github/SUPPORT.md) for what makes a report actionable.
- **Security vulnerabilities** → private reporting as described in the [security policy](.github/SECURITY.md). Never a public issue.

Support is community best-effort; there is no commercial support or SLA.

## Contributing

Every change starts with an issue — even typo fixes. The [contributing guide](CONTRIBUTING.md) covers the setup, the ground rules, and what we accept; issues labeled [`good first issue`](https://github.com/jukanntenn/django-channels-shm/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) are the intended entry point for newcomers.

The project is maintained by jukanntenn and contributors on a best-effort basis. Architecture and behavior changes go through the DCS-RFC process — proposals and decision records in [`.agents/dcs-rfcs/`](.agents/dcs-rfcs/README.md) — and AI-assisted contributions are welcome (see `AGENTS.md`).

## License

BSD-3-Clause. See [LICENSE](LICENSE).
