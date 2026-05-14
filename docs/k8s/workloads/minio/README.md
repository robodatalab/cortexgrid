# MinIO — K8s Spec

How the MinIO server runs inside Kubernetes. Applied by the Argo Application at [../../argo_deployments/onprem/minio/](../../../../k8s/argo_deployments/onprem/minio/). On-prem only — the AWS profile excludes this workload (real S3 takes over).

## Deployment shape

- **`kind: Deployment` (replicas: 1)** — single MinIO pod backed by a PersistentVolumeClaim.
- **`strategy: Recreate`** — only one pod can mount a RWO volume at a time, so we tear the old one down before starting the new one.
- **`nodeSelector: role: head`** — pins to the head node where the HDD lives. See [../../README.md](../../README.md) for the cluster-wide storage setup.
- **`args: server /data --console-address :9001`** — identical to the old docker-compose command.
- **Ports** — 9000 (S3 API), 9001 (web console). Both exposed via NodePort (30900 / 30901) so they're reachable from anywhere on the tailnet.
- **Probes** — MinIO's `/minio/health/ready` + `/minio/health/live` HTTP endpoints.

## Credentials and SM publishing

The cluster-side `minio-credentials` Secret is **not** in this directory — it is applied by the seed pipeline operator [MinioCredentials](../../../../k8s/seed/operators/minio_credentials.py) before the Argo App syncs. The operator:

1. Generates a random MinIO admin password on first run, persists it in the Secret. On rerun reads the existing Secret rather than regenerating, so MinIO data tied to those creds is preserved.
2. Publishes to AWS Secrets Manager: `AWS_S3_ENDPOINT_URL` (=`http://<head-tailscale-ip>:30900`), `S3_BUCKET_NAME` (=`mlflow-artifacts`), `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`.

The endpoint URL uses the head's tailscale IP + the NodePort so the same value works for in-cluster pods (flannel binds to `tailscale0`) and for any tailnet member — laptops, CI runners, ray workers. This is the on-prem analog of `terraform/platform/s3` writing `https://s3.<region>.amazonaws.com` on the AWS profile: in both cases the *infrastructure layer* publishes service-discovery info into the shared SM namespace, and workloads only read.

## Files

- **deployment.yaml** — the Deployment described above. Reads `MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` from the seed-applied `minio-credentials` Secret via `envFrom`.
- **service.yaml** — NodePort Service for S3 API (9000/30900) and console (9001/30901).
- **pvc.yaml** — 1Ti `PersistentVolumeClaim` (uses k3s's `local-path` storage class, routed to the head HDD via the node-specific override in `local-path-config`). The `1Ti` is declarative only — local-path does not enforce quotas at the filesystem level.
- **bucket-init.yaml** — a `Job` running `minio/mc` once after sync to create `mlflow-artifacts`. Reads the same Secret via `valueFrom: secretKeyRef`. Annotated as an Argo PostSync hook so it runs after the Deployment is up; `ttlSecondsAfterFinished: 300` cleans it up after success.

## AWS migration plan

On the AWS profile this whole directory is excluded from the Argo bootstrap — `terraform/platform/s3` provisions a real S3 bucket and writes the same SM keys (`AWS_S3_ENDPOINT_URL` = regional public S3 URL, `S3_BUCKET_NAME` = the real bucket name); `PlatformConfig` mirrors the real-AWS keys to `S3_ACCESS_KEY_ID`/`SECRET`. The workload manifests that consume those SM keys are profile-agnostic — same Secret names, different contents per profile.
