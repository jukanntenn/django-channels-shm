# syntax=docker/dockerfile:1.7
# Reproducible 3-way benchmark image: channels-shm vs channels_redis vs
# InMemoryChannelLayer, constrained to 2 CPUs / 2 GB RAM via docker-compose.
# stage1: build the native wheel with maturin (abi3 -> Python-version independent)
FROM rust:1.85-bookworm AS builder
WORKDIR /build
# Install maturin via pip (rust image needs python3-pip first)
RUN apt-get update -qq && apt-get install -y -qq python3-pip && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir --break-system-packages "maturin>=1.0,<2.0"
# Copy the whole repo so maturin can resolve Cargo workspace + Cargo.lock
COPY . /build
RUN maturin build --release --interpreter python3 \
    && mkdir -p /wheels && cp /build/crates/_channels_shm_native/target/wheels/*manylinux*.whl /wheels/

# stage2: runtime (bench harness + redis for the channels_redis baseline)
FROM python:3.13-slim-bookworm
WORKDIR /bench
# redis-server runs inside this container (localhost:6379) so the whole
# comparison lives under the same 2c/2G resource budget.
RUN apt-get update -qq && apt-get install -y -qq redis-server && rm -rf /var/lib/apt/lists/*
COPY --from=builder /wheels/ /tmp/wheels/
RUN pip install --no-cache-dir /tmp/wheels/*.whl \
    "channels>=4.0" "asgiref>=3.9" "msgpack>=1.0" "structlog>=25.0" \
    "channels-redis>=4.3.0" "pytest>=8.0" "pytest-benchmark>=4.0"
# The bench harness imports the repo's bench package (run_cross_process etc.)
COPY bench/ /bench/bench/
COPY pyproject.toml /bench/pyproject.toml
ENV PYTHONPATH=/bench
CMD ["python", "-O", "bench/docker/run_benchmarks.py"]
