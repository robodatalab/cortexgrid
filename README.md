# RoboLab Infrastructure

[![cortexflow tests](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-tests.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-tests.yml)
[![cortexflow integration tests](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-integration-tests.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-integration-tests.yml)
[![cortexflow-ui tests](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-ui-tests.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-ui-tests.yml)
[![cortexflow-ui integration tests](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-ui-integration-tests.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-ui-integration-tests.yml)
[![cortexflow-ui-backend](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-ui-backend.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-ui-backend.yml)
[![cortexflow-ui-frontend](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-ui-frontend.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/cortexflow-ui-frontend.yml)
[![jobs-control-plane](https://github.com/paksas/robolab-infra/actions/workflows/jobs-control-plane.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/jobs-control-plane.yml)
[![mlflow](https://github.com/paksas/robolab-infra/actions/workflows/mlflow.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/mlflow.yml)
[![ray-head](https://github.com/paksas/robolab-infra/actions/workflows/ray-head.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/ray-head.yml)
[![version-bump](https://github.com/paksas/robolab-infra/actions/workflows/version-bump.yml/badge.svg)](https://github.com/paksas/robolab-infra/actions/workflows/version-bump.yml)

Cloud infrastructure, ML compute, and deployment orchestration for RoboLab. Cloud resources run on AWS (`eu-west-2`, except the marketing site which stays in `us-east-1` because CloudFront requires `us-east-1` ACM) and are provisioned with Terraform. ML compute runs on a DGX Spark worker that joins the cluster over Tailscale.

## Features

- **Async job submission** — `cortexflow.remote(fn, *args, num_gpus=, num_cpus=, retry=)` ships a Python callable to the cluster and returns a job ID for polling.
- **Experiment tracking** — `Experiment.init(name)` creates an MLflow experiment+run; `log_metric`, `log_params`, `log_artifact` log against it.
- **Checkpoint / resume** — `cortexflow.checkpoint() / resume()` persists training state to MLflow artifacts so retries pick up where the previous attempt left off.
- **Retries** — `retry=True` on `remote()` re-submits failed jobs with the same checkpoint context.
- **Storage** — `upload`, `download`, `upload_dir` against S3 (AWS) or MinIO (on-prem).
- **Secrets** — `get_secret`, `set_secret` against AWS Secrets Manager (AWS) or K8s secrets (on-prem).
- **Two deployment profiles** — same manifests target either AWS (managed Postgres + S3) or on-prem (in-cluster Postgres + MinIO + DGX worker).
- **GitOps cluster bootstrap** — `make head-setup` / `make worker-setup` install k3s and seed Argo CD; the rest syncs from `k8s/`.
- **Experiment UI** — `cortexflow-ui` browses experiments, runs, and job logs (in development).

## Cortexflow vs. Modal

Modal replaces the compute-submission half of cortexflow cleanly. It does not replace experiment tracking, the experiment UI, or the on-prem deployment target.

| Capability | Cortexflow | Modal |
|---|---|---|
| Async job submission | `cortexflow.remote(fn, ...)` → job ID | `fn.spawn()` → `FunctionCall` |
| Wait for result | `_ray_run` polls Ray | `FunctionCall.from_id(id).get(timeout=)` |
| Code shipping | tarball → S3 per submit | `modal.Image` built once, cached |
| Retries | `retry=True` flag | `retries=modal.Retries(...)` decorator |
| Secrets | AWS Secrets Manager / K8s | `modal.Secret` |
| Autoscaling | Ray + `jobs-control-plane` | built-in autoscaler |
| Experiment tracking | MLflow integration | none |
| Training-state checkpoint | `cortexflow.checkpoint() / resume()` | none — `modal.Volume` is manual |
| Experiment UI | `cortexflow-ui` | dashboard for jobs only |
| On-prem deployment | DGX Spark + ThinkStation + MinIO | cloud-only |
| GPU hardware | whatever you own | H100 / A100 / L40S / L4 / A10G / T4 |
| Long jobs | unlimited (Ray) | 24h function timeout |

Switching to Modal would replace `cortexflow.remote` + `jobs-control-plane` + Ray + Argo + k3s. It would not replace MLflow, `cortexflow-ui`, or the on-prem story.

Full documentation: [docs/README.md](docs/README.md).
