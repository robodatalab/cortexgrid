# Custom Images

Docker images we build and push to GHCR, consumed by deployments in [../argo-deployments/](../argo-deployments/). Built by [GitHub Actions workflows](../../.github/workflows/) on push; images land at `ghcr.io/paksas/<name>`.

## Why custom images exist

Upstream images (`rayproject/ray`, `mlflow-server`, etc.) are generic. Our workloads need specific version pins and extra Python packages co-located with the core binary so Ray workers can log to MLflow without installing deps at runtime, and so every pod boots with the same dependency set.

## Images

### ray-head — [ray/Dockerfile](ray/Dockerfile)

Base: `python:3.11-slim`. Pip-installs `ray[default,train,tune,data]==2.9.3`, `mlflow==3.11.1`, `python-dotenv==1.0.1`, `psutil==5.9.8` so training code running on Ray workers can log to MLflow without extra installs. Python 3.11 is required because mlflow 3.x needs Python ≥3.10; `rayproject/ray` at pinned ray versions only publishes Python 3.8 + amd64, neither of which fits DGX (arm64).

No custom entrypoint: the deployment (see [../argo-deployments/ray/](../argo-deployments/ray/)) invokes `ray start --head` itself via the pod's `command`.

Pushed by [ray-head.yml](../../.github/workflows/ray-head.yml).

### mlflow — [mlflow/Dockerfile](mlflow/Dockerfile)

Base: `python:3.11-slim`. Installs `mlflow==3.11.1`, `psycopg2-binary==2.9.9`, `boto3==1.34.29`. Runs `mlflow server` on port 5000. Used by the MLflow deployment ([../argo-deployments/mlflow/](../argo-deployments/mlflow/)).

Pushed by [mlflow.yml](../../.github/workflows/mlflow.yml).

### jobs-control-plane — [jobs-control-plane/Dockerfile](jobs-control-plane/Dockerfile)

Base: `python:3.11-slim` + `uv`. Builds the `jobs_control_plane` package from the repo (depends on `cortexflow`). Entrypoint runs the polling server that submits MLflow runs to Ray. Used by [../argo-deployments/jobs-control-plane/](../argo-deployments/jobs-control-plane/).

Pushed by [jobs-control-plane.yml](../../.github/workflows/jobs-control-plane.yml).
