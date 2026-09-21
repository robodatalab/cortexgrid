# Jobs Control Plane — keeps cortexgrid's records in Postgres, schedules jobs on Ray

FROM python:3.11.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --group control-plane

# pyproject.toml's readme and license-files; `uv sync` builds the project and needs both.
COPY docs/cortexgrid/README.md ./docs/cortexgrid/README.md
COPY LICENSE ./
COPY cortexgrid/ ./cortexgrid/
COPY jobs_control_plane/ ./jobs_control_plane/

RUN --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --group control-plane

CMD ["/app/.venv/bin/python", "-m", "jobs_control_plane.server"]

# trigger: cortexgrid-rename-2026-09-14
