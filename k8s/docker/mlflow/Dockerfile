# MLflow Tracking Server — multi-stage to drop build toolchain

FROM python:3.11.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    mlflow==3.11.1 \
    psycopg2-binary==2.9.9 \
    boto3==1.34.29

FROM python:3.11.13-slim

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

EXPOSE 5000

CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000"]

# trigger: 315-trigger-tests-2026-05-16
