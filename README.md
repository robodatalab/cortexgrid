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

## Comparison to other platforms

Where cortexflow's surface overlaps with hosted/open-source alternatives:

| Platform | Compute | Retries | Checkpoint | Experiment tracking | Exp. UI | On-prem | OSS |
|---|---|---|---|---|---|---|---|
| **Cortexflow** | Ray + jobs-control-plane | `retry=` flag | `checkpoint() / resume()` | MLflow | `cortexflow-ui` | yes | yes |
| **ClearML** | agents + queues | yes | yes (artifacts) | yes | yes | yes (self-host) | yes |
| **Determined AI** | yes | auto | yes (built-in, first-class) | yes | yes | yes (Helm) | yes |
| **Metaflow / Outerbounds** | yes | yes | yes (built-in) | yes | yes | yes | yes (Metaflow) |
| **Anyscale** | managed Ray | Ray-native | via Ray Train | integrates MLflow / W&B | lineage UI | no (cloud) | no |
| **dstack** | yes | yes | manual | no | partial | yes | yes |
| **Modal** | yes | yes | manual (`modal.Volume`) | no | jobs only | no | no |

**Largest overlap: ClearML** — its agent-and-queue architecture maps almost 1:1 onto `cortexflow.remote` + `jobs-control-plane`, plus it bundles experiment tracking, web UI, and a self-hosted server. Determined is a close second but its `Trial` API constrains job shape to training loops. Anyscale would feel native (already on Ray) but kills on-prem and isn't OSS. Modal has the slickest DX but covers only the compute half.

Full documentation: [docs/README.md](docs/README.md).
