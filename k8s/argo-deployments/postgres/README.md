# PostgreSQL

## Problem

Several platform services need a relational database for metadata — MLflow stores runs, experiments, params, and metrics here; future control-plane services will do the same. Without a shared in-cluster Postgres, every service that needs SQL would have to manage its own instance, bloating resource use and operational surface.

## Components

**stack.yaml** — Argo Application installing Bitnami's `postgresql` Helm chart into the `postgres` namespace. Single primary, 10Gi persistent volume, ClusterIP-only (no NodePort — internal use only). Hardcoded creds (`admin` / `admin`) match the old docker-compose setup; tighten when we move to AWS and swap for RDS.

Exposed at `postgres.postgres.svc.cluster.local:5432`. Default database `mlflow` is created at install.

## Dependencies

- **MLflow** ([../mlflow/](../mlflow/)) — metadata backend. Connection string in mlflow's deployment env: `postgresql://admin:admin@postgres.postgres.svc.cluster.local:5432/mlflow`.
- **Future**: any service needing Postgres should either create a new database in this instance (safest) or add a new database in the Helm values.
