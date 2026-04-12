# Jobs Control Plane — polls MLflow for submitted jobs, schedules them on Ray

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY cortexflow/ ./cortexflow/
COPY jobs_control_plane/ ./jobs_control_plane/

RUN pip install --no-cache-dir .

CMD ["python", "-m", "jobs_control_plane"]
