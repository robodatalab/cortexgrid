# MLflow — K8s Spec

How the MLflow tracking server runs inside Kubernetes. Applied by the Argo Application at [../../argo_deployments/mlflow/](../../../../k8s/argo_deployments/aws/mlflow/). See that folder's README for the higher-level rationale.

## Deployment shape

- **`kind: Deployment`** — stateless HTTP server; all state lives in Postgres and MinIO. Not a `StatefulSet` (no per-pod identity needed).
- **`replicas: 1`** — single instance. Horizontal scaling is possible (MLflow server is stateless) but unnecessary at current load.
- **`nodeSelector: role: head`** — pins to the head node to co-locate with its Postgres + MinIO backends. See [../../README.md](../../README.md).
- **`command: mlflow server …`** — full invocation equivalent to the old docker-compose command. Key flags: `--serve-artifacts` enables artifact proxying so clients don't talk to MinIO directly; `--artifacts-destination s3://mlflow-artifacts/` points at the shared MinIO bucket via the AWS SDK; `--backend-store-uri` points at the shared Postgres.
- **Env vars** — mlflow uses unmodified boto3 and requires the standard `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` env names; the pod spec maps each from the `s3-creds` Secret's `S3_*` keys (the object-storage identity in our naming) via explicit `env:` blocks. `MLFLOW_S3_ENDPOINT_URL` is mapped the same way from `S3_ENDPOINT_URL` (MinIO Service on on-prem, regional `s3.amazonaws.com` on AWS). `MLFLOW_SERVER_ALLOWED_HOSTS` / `CORS` open the server to any client.
- **`livenessProbe`** — HTTP GET `/health` on port 5000. If it fails for 15s × 3, k8s restarts the pod.
- **`imagePullPolicy: Always`** — every new pod re-pulls `:latest`.
- **`imagePullSecrets: ghcr-pull`** — reflector-mirrored into this namespace.

## Files

- **deployment.yaml** — the `Deployment` described above.
- **service.yaml** — `NodePort` Service exposing HTTP at `:30500` (Tailscale-reachable) and internal port 5000.

## AWS migration plan

On AWS, swap:
- Postgres connection string → RDS endpoint.
- MinIO env vars → real AWS IAM credentials (materialized by ExternalSecret from Secrets Manager), `MLFLOW_S3_ENDPOINT_URL` unset so the SDK talks to real S3.
- `--artifacts-destination` → a real S3 bucket.

The image stays the same.
