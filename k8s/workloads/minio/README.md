# MinIO — K8s Spec

How the MinIO server runs inside Kubernetes. Applied by the Argo Application at [../../argo-deployments/minio/](../../argo-deployments/minio/).

## Deployment shape

- **`kind: Deployment` (replicas: 1)** — single MinIO pod backed by a PersistentVolumeClaim.
- **`strategy: Recreate`** — only one pod can mount a RWO volume at a time, so we tear the old one down before starting the new one.
- **`args: server /data --console-address :9001`** — identical to the old docker-compose command.
- **Env** — `MINIO_ROOT_USER=admin` / `MINIO_ROOT_PASSWORD=adminadmin` (hardcoded; tailnet-only security model).
- **Ports** — 9000 (S3 API), 9001 (web console). Both exposed via NodePort (30900 / 30901) so they're reachable from the Mac over Tailscale.
- **Probes** — MinIO's `/minio/health/ready` + `/minio/health/live` HTTP endpoints.

## Files

- **deployment.yaml** — the Deployment described above.
- **service.yaml** — NodePort Service for S3 API (9000/30900) and console (9001/30901).
- **pvc.yaml** — 50Gi `PersistentVolumeClaim` (uses k3s's `local-path` default storage class).
- **bucket-init.yaml** — a `Job` running `minio/mc` once after sync to create `mlflow-artifacts`. Annotated as an Argo PostSync hook so it runs after the Deployment is up; `ttlSecondsAfterFinished: 300` cleans it up after success.

## AWS migration plan

On AWS, drop MinIO entirely and swap consumers to real S3. Nothing changes in the consumers' code — just the endpoint and credentials.
