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
COPY cortexflow/ ./cortexflow/
COPY tests/__init__.py ./tests/
COPY tests/integration/__init__.py ./tests/integration/
COPY tests/integration/stubs/ ./tests/integration/stubs/
COPY tests/integration/cortexflow_ui/ ./tests/integration/cortexflow_ui/

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked

RUN /app/.venv/bin/playwright install --with-deps chromium

CMD ["/app/.venv/bin/python", "-m", "unittest", "discover", "-t", "/app", "-s", "tests", "-p", "test_*.py", "-v"]
