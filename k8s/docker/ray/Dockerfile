# Ray Head/Worker Image — Infrastructure + PyTorch base
# GPU access is handled by NVIDIA Container Toolkit (--gpus all in compose).
#
# ML libraries (transformers, peft, accelerate, etc.) are NOT installed here.
# Each Ray job's pip runtime_env installs them from the submitting project's
# pyproject.toml, so different repos can use different versions.

FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "ray[default,train,tune,data]==2.9.3" \
    mlflow==3.11.1 \
    python-dotenv==1.0.1 \
    psutil==5.9.8

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY terraform/platform/local-dgx-training/ray/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8265 6379 8080

ENTRYPOINT ["/entrypoint.sh"]
