# RoboLab Infrastructure

Cloud infrastructure and deployment orchestration for RoboLab.

## Structure

- `terraform/` — Cloud infrastructure provisioning
  - `website/` — S3 + CloudFront + Route53 for the marketing website (deployed from `robolabwebsite` repo via GitHub Actions)
  - `platform/secrets/` — Centralized secrets management (AWS Secrets Manager + IAM)
  - `platform/local-dgx-training/` — ML compute stack (Docker Compose on DGX Spark) + `cortexflow` Python library
- `lambda/auth/` — Auth Lambda source code

## Related repos

- `robolab-platform` — Platform frontend and backend
- `robolab-sims-unity` — Unity simulation projects
- `robolab-sims-unreal` — Unreal Engine simulation projects
- `model-gateway` — LLM provider abstraction layer
- `model-training` — Training facilities (SFT, LoRA), depends on `cortexflow`
