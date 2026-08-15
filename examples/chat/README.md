# channels-shm chat demo

A minimal Django + Channels chat that shows off the one thing
`channels-shm` is for: **multi-process ASGI workers exchanging messages over
`/dev/shm` — no Redis, no database, no broker at all** (`DATABASES = {}` in
the settings).

It doubles as the pre-release acceptance app: `uv sync` here builds the
library from the working tree through maturin — a real wheel install, the
same path a release takes.

## Run it

Requires: Linux, a Rust toolchain (the path-source build compiles the native
module), uv.

```bash
cd examples/chat
uv sync                      # builds channels-shm from ../../ via maturin
uv run python manage.py run_workers --workers 3
```

Then open <http://127.0.0.1:8000/> in one tab and
<http://127.0.0.1:8001/> in another. Each chat line is tagged with the PID
of the worker that handled it — send from both tabs and watch messages hop
between processes through the shared-memory layer.

Single worker (plain dev server): `uv run python manage.py runserver`.

## Headless acceptance (release checklist)

The same cross-process fan-out, no browser needed — the command used to
accept a build before tagging a release:

```bash
uv run python manage.py demo_broadcast --workers 4 --messages 200
# → "broadcast acceptance PASSED" (non-zero exit on any shortfall)
```

## Accepting a published release candidate

To test what was actually published to TestPyPI rather than the working
tree, point the dependency at the rc in `pyproject.toml`:

```toml
[tool.uv.sources]
channels-shm = [
    { index = "testpypi", marker = "sys_platform == 'linux'" },
]
```

```toml
[[tool.uv.index]]
name = "testpypi"
url = "https://test.pypi.org/simple"
explicit = true
```

then `uv lock && uv sync` (fall back to the path source afterwards).

## Troubleshooting

- `uv sync` compiles Rust — ensure `cargo --version` works first.
- `/dev/shm` too small in a container? Lower `shm_size` in
  `chat/settings.py` (16 MiB is plenty for the demo).
