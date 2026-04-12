# Jobs Control Plane — polls MLflow for submitted jobs, schedules them on Ray

FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY cortexflow/ ./cortexflow/
COPY jobs_control_plane/ ./jobs_control_plane/

RUN uv sync

CMD [".venv/bin/python", "-m", "jobs_control_plane.server"]
