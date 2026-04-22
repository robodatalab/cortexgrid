# PostgreSQL — K8s Spec

How the Postgres server runs inside Kubernetes. Applied by the Argo Application at [../../argo-deployments/postgres/](../../argo-deployments/postgres/).

## Deployment shape

- **`kind: Deployment` (replicas: 1)** — single Postgres pod backed by a PersistentVolumeClaim. Could be a `StatefulSet`, but with one replica there's no benefit; we'd switch to a StatefulSet (or CloudNativePG) only when we need replication.
- **`strategy: Recreate`** — only one pod can mount a RWO volume at a time.
- **`nodeSelector: kubernetes.io/arch: amd64`** — pins to P5 where the 2TB HDD lives. See [../../README.md](../../README.md) for the cluster-wide storage setup.
- **Image** — official `postgres:15-alpine`, multi-arch.
- **Env** — `POSTGRES_USER=admin`, `POSTGRES_PASSWORD=admin`, `POSTGRES_DB=mlflow`. `PGDATA=/var/lib/postgresql/data/pgdata` so the data directory is a sub-path, avoiding conflicts with mount metadata on the PVC root.
- **Probes** — `pg_isready` exec on port 5432.
- **Ports** — 5432, exposed via `ClusterIP` Service only (internal use).

## Files

- **deployment.yaml** — the Deployment described above.
- **service.yaml** — ClusterIP Service at port 5432. DNS: `postgres.postgres.svc.cluster.local:5432`.
- **pvc.yaml** — 10Gi `PersistentVolumeClaim` (k3s `local-path` storage class, routed to the 2TB HDD on P5 via the node-specific override in `local-path-config`).

## AWS migration plan

Swap for RDS. Update mlflow's `--backend-store-uri` to the RDS endpoint. Everything else unchanged.
