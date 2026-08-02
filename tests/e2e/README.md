# e2e — Django/channels cross-worker tests

These tests run inside Docker against a real Django + channels + channels-shm
stack with multiple ASGI workers, proving messages cross worker boundaries.

## Run locally (requires Docker)

    cd tests/e2e
    docker compose build
    docker compose up -d worker1 worker2 worker3   # start 3 ASGI workers
    docker compose run --rm runner pytest tests/e2e/ -v   # rebuilds runner image if missing

Tear down:

    docker compose down -v

## CI

The e2e job in `.github/workflows/ci.yml` runs the same commands. It is opt-in
(triggers only when python/rust/ci paths change, not on docs-only changes).
