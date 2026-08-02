# syntax=docker/dockerfile:1.7
# stage1: build the native wheel with maturin (abi3 → Python-version independent)
FROM rust:1.85-bookworm AS builder
WORKDIR /build
# Install maturin via pip (rust image needs python3-pip first)
RUN apt-get update -qq && apt-get install -y -qq python3-pip && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir --break-system-packages "maturin>=1.0,<2.0"
# Copy the whole repo so maturin can resolve Cargo workspace + Cargo.lock
COPY . /build
RUN maturin build --release --interpreter python3 \
    && mkdir -p /wheels && cp /build/crates/_channels_shm_native/target/wheels/*manylinux*.whl /wheels/

# stage2: runtime
FROM python:3.13-slim-bookworm
WORKDIR /app
# Install the built wheel + Django/channels stack + daphne (ASGI server) + test client libs
COPY --from=builder /wheels/ /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    "channels==4.3.2" "Django>=5.2,<6.1" "daphne>=4.0" "websockets>=12.0" "pytest>=8.0" "pytest-asyncio>=0.23"
# Copy the test app + tests
COPY tests/e2e/ /app/tests/e2e/
# /dev/shm is used by the layer; ensure it is writable (default tmpfs is fine)
ENV PYTHONPATH=/app
ENV DJANGO_SETTINGS_MODULE=tests.e2e.testapp.settings
EXPOSE 8000
# Default command overridden by docker-compose per service
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "tests.e2e.testapp.asgi:application"]
