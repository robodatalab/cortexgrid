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

# build-essential is in the final image, not just the builder: `torch.compile`
# ships no kernels. Inductor generates C++ and Triton source at request time and
# shells out to a compiler to build it, so a worker without one raises
# "Failed to find C compiler" and every model silently runs eager.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget git build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Torch's CUDA 13 dependency chain (torch + cuda-toolkit + cudnn + triton)
# totals several GB. Installed as separate RUN layers so each image blob stays
# under ~1 GB and fits within GHCR's upload window. CUDA 13 replaces the
# per-library nvidia-*-cu12 wheels with one cuda-toolkit meta-package and its
# extras, leaving only the four that ship separately. Versions mirror torch
# 2.13.0+cu130's METADATA; bump together when upgrading torch.
#
# cu130 rather than cu128 because the cluster has sm_121 cards (GB10): CUDA 12.8
# tops out at sm_120, so a cu128 torch warns that the device is newer than it
# supports and falls back off its optimised paths.
RUN pip install --no-cache-dir \
    "cuda-toolkit[cublas,cudart,cufft,cufile,cupti,curand,cusolver,cusparse,nvjitlink,nvrtc,nvtx]==13.0.3"
RUN pip install --no-cache-dir nvidia-cudnn-cu13==9.20.0.48
RUN pip install --no-cache-dir \
    "cuda-bindings>=13.0.3,<14" \
    nvidia-cusparselt-cu13==0.8.1 \
    nvidia-nccl-cu13==2.29.7 \
    nvidia-nvshmem-cu13==3.4.5
RUN pip install --no-cache-dir triton==3.7.1
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cu130 \
    --no-deps \
    torch==2.13.0

# torch is installed --no-deps above (to control the multi-GB CUDA chain layer
# by layer), which also drops its small pure-Python deps. Add them back with
# torch 2.13.0's own version bounds -- without them torch._dynamo fails to import
# (e.g. `import sympy` in torch.fx.experimental.symbolic_shapes). ~40 MB total,
# dominated by sympy + networkx; pip leaves already-present deps (jinja2,
# typing-extensions via ray/mlflow) untouched. Mirror torch's METADATA on bump.
RUN pip install --no-cache-dir \
    filelock \
    "typing-extensions>=4.10.0" \
    "setuptools>=77.0.3" \
    "sympy>=1.13.3" \
    "networkx>=2.5.1" \
    jinja2 \
    "fsspec>=0.8.5"
