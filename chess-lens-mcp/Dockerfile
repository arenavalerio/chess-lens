# syntax=docker/dockerfile:1

FROM python:3.11-slim-bookworm AS base

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY install_stockfish.py /tmp/install_stockfish.py
RUN python /tmp/install_stockfish.py && \
    ln -s /usr/games/stockfish /usr/bin/stockfish && \
    rm /tmp/install_stockfish.py

# Use the uv project workflow (pyproject.toml + uv.lock) instead of pip.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install third-party dependencies first so this layer caches across source edits.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# Install the package itself; uv puts the `chess-lens-mcp` console script on PATH.
COPY src ./src
RUN uv sync --locked --no-dev

FROM base AS test

# Test/dev extras plus the test sources, then run the full quality gate.
COPY tests ./tests
RUN uv sync --locked --extra test --extra dev && \
    uv run ruff check . && \
    uv run ruff format --check . && \
    uv run mypy src tests && \
    uv run pytest tests/test_mcp_server.py

FROM base AS runtime

# Drop the build-time toolchain; the runtime only needs Python and Stockfish.
RUN rm -rf /root/.cache/uv /bin/uv /bin/uvx

# The package is installed into /app/.venv, so no PYTHONPATH or source path is needed.
ENTRYPOINT ["chess-lens-mcp"]
