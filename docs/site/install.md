# Installation

## Requirements

- **Linux** (x86-64; AArch64 best-effort) — the layer builds on `MAP_SHARED` and `AF_UNIX`
- **Python ≥ 3.11** (tested 3.11–3.13)
- **channels ≥ 4.0** — installed automatically as a dependency
- **Rust ≥ 1.86** — only needed to *build* the native extension

Django itself is not a dependency: the layer runs from any ASGI stack that channels supports.

## Install from GitHub

The package is not published to PyPI yet, so install from GitHub. The maturin backend builds the `abi3` wheel during install, which needs a Rust toolchain:

```bash
pip install git+https://github.com/jukanntenn/django-channels-shm.git
```

## Install from source (development)

```bash
uv sync
uvx maturin develop --skip-install   # builds _native.abi3.so into src/
```

The native module must be built before any test, type-check, or local docs build can run.

## Try the demo app

[`examples/chat`](https://github.com/jukanntenn/django-channels-shm/tree/main/examples/chat) is a multi-process Django + Channels chat with zero infrastructure — no Redis, no database:

```bash
cd examples/chat
uv sync                                   # builds django-channels-shm from ../.. via maturin
uv run uvicorn chat.asgi:application --workers 3 --port 8000
```

Open <http://127.0.0.1:8000/>, pick nicknames in several tabs, and chat across worker processes over `/dev/shm`.

Next: [Quickstart](quickstart.md).
