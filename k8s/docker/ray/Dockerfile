# Ray head image — pip-installs Ray + our extras on Python 3.11.13.
# Multi-stage: builder compiles any native deps, final image only keeps the
# installed packages. No custom entrypoint — the pod's command runs
# `ray start --head` directly.
# rebuild-force: 19-02-2026 16:18

FROM python:3.11.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "ray[default,serve]==2.58.0" \
    "smart_open[s3]==7.0.4" \
    mlflow==3.11.1 \
    python-dotenv==1.0.1 \
    psutil==5.9.8

FROM python:3.11.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Torch's CUDA 12.8 dependency chain (torch + nvidia-cu12-* + triton) totals
# several GB. Installed as separate RUN layers so each image blob stays under
# ~1 GB and fits within GHCR's upload window. Versions mirror torch 2.10.0+cu128's
# METADATA; bump together when upgrading torch.
RUN pip install --no-cache-dir nvidia-cublas-cu12==12.8.4.1
RUN pip install --no-cache-dir nvidia-cudnn-cu12==9.10.2.21
RUN pip install --no-cache-dir \
    nvidia-cuda-nvrtc-cu12==12.8.93 \
    nvidia-cuda-runtime-cu12==12.8.90 \
    nvidia-cuda-cupti-cu12==12.8.90 \
    nvidia-cufft-cu12==11.3.3.83 \
    nvidia-curand-cu12==10.3.9.90 \
    nvidia-cusolver-cu12==11.7.3.90 \
    nvidia-cusparse-cu12==12.5.8.93 \
    nvidia-cusparselt-cu12==0.7.1 \
    nvidia-nccl-cu12==2.27.5 \
    nvidia-nvshmem-cu12==3.4.5 \
    nvidia-nvtx-cu12==12.8.90 \
    nvidia-nvjitlink-cu12==12.8.93 \
    nvidia-cufile-cu12==1.13.1.3 \
    cuda-bindings==12.9.4
RUN pip install --no-cache-dir triton==3.6.0
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cu128 \
    --no-deps \
    torch==2.10.0

# torch is installed --no-deps above (to control the multi-GB CUDA chain layer
# by layer), which also drops its small pure-Python deps. Add them back with
# torch 2.10.0's own version bounds -- without them torch._dynamo fails to import
# (e.g. `import sympy` in torch.fx.experimental.symbolic_shapes). ~40 MB total,
# dominated by sympy + networkx; pip leaves already-present deps (jinja2,
# typing-extensions via ray/mlflow) untouched. Mirror torch's METADATA on bump.
RUN pip install --no-cache-dir \
    filelock \
    "typing-extensions>=4.10.0" \
    "sympy>=1.13.3" \
    "networkx>=2.5.1" \
    jinja2 \
    "fsspec>=0.8.5"
