# Ray head image — pip-installs Ray + our extras on Python 3.11.
# Multi-stage: builder compiles any native deps, final image only keeps the
# installed packages. No custom entrypoint — the pod's command runs
# `ray start --head` directly.

FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "ray[default]==2.9.3" \
    mlflow==3.11.1 \
    python-dotenv==1.0.1 \
    psutil==5.9.8

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
