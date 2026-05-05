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

Full documentation: [docs/README.md](docs/README.md).
