# SHM Chat — channels-shm demo

A WeChat-style web chat on top of the one thing `channels-shm` is for:
**multi-process ASGI workers exchanging messages over `/dev/shm` — no Redis,
no database, no broker at all** (`DATABASES = {}` in the settings).

- **登录**: no accounts — pick a unique nickname and you are in.
- **私聊**: start a chat by nickname (pick from the online list, or type any
  nickname directly).
- **群聊**: join by group name; the first member creates the group. Group
  size is capped at **500** by the channel layer itself
  (`max_members_per_group`).
- **多进程**: one port, N uvicorn workers; connections land wherever the
  kernel sends them and every message crosses processes through the
  shared-memory layer.

The app also doubles as the pre-release acceptance project: `uv sync` here
builds the library from the working tree through maturin — a real wheel
install, the same path a release takes.

## Run it

Requires: Linux, a Rust toolchain (the path-source build compiles the native
module), uv.

```bash
cd examples/chat
uv sync                                                   # builds channels-shm from ../../ via maturin
uv run uvicorn chat.asgi:application --workers 3 --port 8000
```

Open <http://127.0.0.1:8000/> in several tabs and chat. The connection bar in
the sidebar shows the worker PID serving each tab; hover any message to see
the PID of the worker that relayed it — open a private chat between two tabs
and watch messages hop processes with no Redis in sight.

Single worker for development (auto-reload):

```bash
uv run uvicorn chat.asgi:application --reload --port 8000
```

## How it works without any storage

Everything lives in channel-layer groups, so it works across worker
processes with the standard Channels API:

- **Nickname uniqueness** — each user joins the group `u_<sha256(nick)>`. A
  newcomer broadcasts a claim into that group; a current holder answers
  directly to the claimant's channel, which rejects the new login. (Two
  perfectly simultaneous claims can both win — fine for a demo.)
- **Presence** — one shared group; a fresh client broadcasts a census query
  and every member answers directly to it. Clients re-run the census
  periodically, so members that vanish without a farewell drop out on their
  own.
- **Private messages** — a group of exactly one member (the nickname's
  group). Recipients ack directly to the sender's channel; the client marks
  un-acked messages with WeChat's red `!` after a timeout.
- **Group size 500** — enforced by the layer: a full `group_add` raises and
  surfaces as a `group_full` error to the client.

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
- `/dev/shm` too small in a container? Lower `shm_size` in `chat/settings.py`
  (the demo config asks for 64 MiB).
