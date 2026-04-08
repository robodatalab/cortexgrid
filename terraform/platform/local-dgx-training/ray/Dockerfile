# Ray Head/Worker Image — Python 3.11 + ML Libraries
# GPU access is handled by NVIDIA Container Toolkit (--gpus all in compose).
# PyTorch CUDA wheels bundle the runtime libraries they need.

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "ray[default,train,tune,data]==2.9.3" \
    torch==2.2.0 \
    torchvision==0.17.0 \
    torchaudio==2.2.0 \
    transformers==4.37.2 \
    datasets==2.16.1 \
    accelerate==0.26.1 \
    peft==0.8.2 \
    mlflow==2.10.2 \
    boto3==1.34.29 \
    python-dotenv==1.0.1 \
    psutil==5.9.8

EXPOSE 8265 6379 8080

CMD ["ray", "start", "--head", "--block"]
