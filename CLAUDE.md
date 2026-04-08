# RoboLab Infrastructure

Cloud infrastructure and deployment orchestration for RoboLab.

## Structure

- `k8s/` — Kubernetes manifests for simulation workloads and platform services
- `terraform/` — Cloud infrastructure provisioning
  - `website/` — S3 + CloudFront + Route53 for the marketing website (deployed from `robolabwebsite` repo via GitHub Actions)
- `docker-base/` — Base Docker images for simulation runtimes

