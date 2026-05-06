# Postgres — K8s Spec

How the on-prem Postgres runs inside Kubernetes. Applied by the Argo Application at [../../argo-deployments/onprem/postgres/](../../../../k8s/argo-deployments/onprem/postgres/). Replaces RDS on the AWS profile.

## Deployment shape

- **`kind: Deployment` (replicas: 1)** — single postgres pod backed by a PersistentVolumeClaim. Same shape as MinIO.
- **`strategy: Recreate`** — RWO volume, only one pod can mount it at a time.
- **`nodeSelector: role: head`** — pins to the head node, same HDD as MinIO.
- **`image: postgres:16.13`** — matches the AWS RDS `engine_version`.
- **`PGDATA: /var/lib/postgresql/data/pgdata`** — subdirectory of the mount, so initdb does not abort on a non-empty volume root (`lost+found`).
- **Probes** — `pg_isready -U robolab -d mlflow`.
- **Service** — `NodePort 30432` so the DB is reachable from in-cluster pods (via flannel's `tailscale0`-bound network) AND from any laptop on the tailnet — single endpoint, single composed URI in SM.

## Files

- **deployment.yaml** — the Deployment described above.
- **service.yaml** — `NodePort` exposing postgres at `:30432`.
- **pvc.yaml** — 50Gi `PersistentVolumeClaim` via local-path (same provisioner as MinIO).
- **notes-init.yaml** — PostSync `Job`: `pg_isready` poll, then `CREATE DATABASE notes` (if missing), then `psql -f /schema/notes-schema.sql`. Mirrors the AWS terraform `null_resource.notes_database` + `null_resource.notes_schema` provisioners.
- **notes-schema.sql** — single source of truth for the notes schema. Also consumed by [terraform/platform/rds/notes.tf](../../../../terraform/platform/rds/notes.tf) on the AWS profile via a relative path.
- **kustomization.yaml** — synthesizes `notes-schema` ConfigMap from `notes-schema.sql` via `configMapGenerator` with `disableNameSuffixHash: true`. Argo auto-detects kustomize when this file is present.

## Credentials and URI publishing

The cluster-side `postgres-credentials` Secret is **not** in this directory — it is applied by the seed pipeline operator [PostgresCredentials](../../../../k8s/seed/operators/postgres_credentials.py) before the Argo App syncs. The operator:

1. Generates a random master password on first run, persists it in the cluster Secret. On rerun reads the existing Secret rather than regenerating, so data survives reseeds.
2. Composes `postgresql://robolab:<password>@<head-tailscale-ip>:30432/mlflow` and `.../notes`, publishes as `MLFLOW_BACKEND_STORE_URI` and `NOTES_DB_URI` in AWS Secrets Manager.

Mirrors the AWS posture: terraform's `random_password.master` persists in tfstate; the password lives only inside the RDS instance and inside the two composed URIs. No standalone password key in SM.

## Logging in from a laptop

```sh
psql "$(uv run python -c 'from cortexflow.secrets import get_secret; print(get_secret("MLFLOW_BACKEND_STORE_URI"))')"
```

Substitute `NOTES_DB_URI` for the notes database. Identical command works against AWS RDS — only the host portion of the URI differs, and it always resolves via the tailnet (subnet router on AWS, NodePort on the head's tailscale IP on-prem).

## AWS migration plan

On the AWS profile this whole directory is excluded from the Argo bootstrap (`exclude: 'onprem/**'` — see [k8s/argocd.yaml](../../../../k8s/argocd.yaml)). RDS provisioned by [terraform/platform/rds](../../../../terraform/platform/rds/) takes over; the workload manifests that consume `MLFLOW_BACKEND_STORE_URI` / `NOTES_DB_URI` are profile-agnostic.
