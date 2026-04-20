# CortexFlow UI — backend (FastAPI server + cortexflow library)

FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY cortexflow/ ./cortexflow/
COPY cortexflow_ui/backend/ ./cortexflow_ui/backend/

RUN uv sync --group ui

CMD ["/app/.venv/bin/uvicorn", "cortexflow_ui.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
