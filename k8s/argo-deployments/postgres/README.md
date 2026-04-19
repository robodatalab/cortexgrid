# PostgreSQL — Argo Deployment

## Problem

Several platform services need a relational database for metadata — MLflow stores runs, experiments, params, and metrics here; future control-plane services will do the same. Without a shared in-cluster Postgres, every service that needs SQL would have to manage its own instance, bloating resource use and operational surface.

## Components

**stack.yaml** — Argo Application pointing at raw manifests at [`k8s/workloads/postgres/`](../../workloads/postgres/). Deployed into the `postgres` namespace. See [workloads/postgres/README.md](../../workloads/postgres/README.md) for the k8s spec.

## Why raw manifests (not a Helm chart)

Same reasoning as other deployments. Also: we tried Bitnami's `postgresql` chart first but its default image (`docker.io/bitnami/postgresql:*`) was pulled from Docker Hub's free tier in Bitnami's August 2024 licensing change. The official `postgres:15-alpine` image is multi-arch (ARM64 on DGX), actively maintained, and doesn't move around.

## Dependencies

- **MLflow** ([../mlflow/](../mlflow/)) — metadata backend. Connection string: `postgresql://admin:admin@postgres.postgres.svc.cluster.local:5432/mlflow`.
- **Future**: any service needing Postgres can either create a new database in this instance (safest) or run its own.
