# CortexGrid UI — backend (FastAPI server + cortexgrid library)

FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --group ui

COPY cortexgrid/ ./cortexgrid/
COPY cortexgrid_ui/backend/ ./cortexgrid_ui/backend/

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --group ui

CMD ["/app/.venv/bin/uvicorn", "cortexgrid_ui.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

# trigger: cortexgrid-rename-2026-09-14
