FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY pyproject.toml ./
COPY cortexgrid/ ./cortexgrid/
COPY tests/__init__.py ./tests/
COPY tests/integration/__init__.py ./tests/integration/
COPY tests/integration/stubs/ ./tests/integration/stubs/
COPY tests/integration/cortexgrid/ ./tests/integration/cortexgrid/

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked

CMD ["/app/.venv/bin/python", "-m", "unittest", "discover", "-t", "/app", "-s", "tests", "-p", "test_*.py", "-v"]

# trigger: cortexgrid-rename-2026-09-14
