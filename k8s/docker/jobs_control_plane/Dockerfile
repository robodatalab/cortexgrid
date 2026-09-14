# Jobs Control Plane — polls MLflow for submitted jobs, schedules them on Ray

FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

COPY cortexgrid/ ./cortexgrid/
COPY jobs_control_plane/ ./jobs_control_plane/

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked

CMD ["/app/.venv/bin/python", "-m", "jobs_control_plane.server"]

# trigger: cortexgrid-rename-2026-09-14
