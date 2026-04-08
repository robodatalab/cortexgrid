# =============================================================================
# MLflow Tracking Server
# =============================================================================
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "mlflow[extras]==2.10.2" \
    psycopg2-binary==2.9.9 \
    boto3==1.34.29

EXPOSE 5000

CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000"]
