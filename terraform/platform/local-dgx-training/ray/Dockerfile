# =============================================================================
# Ray Head/Worker Image — CUDA 12.2 + Python 3.11 + ML Libraries
# =============================================================================
# Multi-stage build: install Python deps first, then copy into CUDA runtime.

# -- Stage 1: Python dependencies --
FROM python:3.11-slim AS deps

RUN pip install --no-cache-dir \
    "ray[default,train,tune,data]==2.9.3" \
    torch==2.2.0 \
    torchvision==0.17.0 \
    torchaudio==2.2.0 \
    --index-url https://download.pytorch.org/whl/cu121 \
    && pip install --no-cache-dir \
    transformers==4.37.2 \
    datasets==2.16.1 \
    accelerate==0.26.1 \
    peft==0.8.2 \
    mlflow==2.10.2 \
    boto3==1.34.29 \
    python-dotenv==1.0.1 \
    psutil==5.9.8

# -- Stage 2: CUDA runtime image --
FROM nvcr.io/nvidia/cuda:12.2.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    curl \
    wget \
    git \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from deps stage
COPY --from=deps /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/dist-packages
COPY --from=deps /usr/local/bin /usr/local/bin

ENV PATH="/usr/local/bin:${PATH}"
ENV PYTHONPATH="/usr/local/lib/python3.11/dist-packages:${PYTHONPATH}"

EXPOSE 8265 6379 8080

CMD ["ray", "start", "--head", "--block"]
