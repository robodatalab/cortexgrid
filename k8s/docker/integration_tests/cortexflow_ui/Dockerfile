FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY cortexflow/ ./cortexflow/
COPY tests/integration/cortexflow_ui/ ./tests/

RUN uv sync

CMD ["/app/.venv/bin/python", "-m", "unittests", "discover", "-s", "./tests", "-p", "test_*.py", "-v"]
