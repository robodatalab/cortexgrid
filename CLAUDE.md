# RoboLab Infrastructure

Cloud infrastructure and deployment orchestration for RoboLab.

## Structure

- `terraform/` — Cloud infrastructure provisioning
  - `website/` — S3 + CloudFront + Route53 for the marketing website (deployed from `robolabwebsite` repo via GitHub Actions)
  - `platform/secrets/` — Centralized secrets management (AWS Secrets Manager + IAM)
- `k8s/` — Argo CD GitOps platform: `argocd.yaml` bootstrap, `argo-deployments/` (Argo Application CRs), `workloads/` (k8s manifests), `seed/` (one-shot DGX seed scripts)
- `cortexflow/` — Python library for connecting ML code to the RoboLab compute cluster (Ray, MLflow, S3)
- `lambda/auth/` — Auth Lambda source code

## Related repos

- `robolab-platform` — Platform frontend and backend
- `robolab-sims-unity` — Unity simulation projects
- `robolab-sims-unreal` — Unreal Engine simulation projects
- `model-gateway` — LLM provider abstraction layer
- `model-training` — Training facilities (SFT, LoRA), depends on `cortexflow`


WORK in small increments, always consulting everything with the user.
You are not allowed to touch more than one function at a time, maximum 20 lines of code.

## Imports

Never use local/inline imports (imports inside functions, methods, or conditional blocks). All imports must be unconditional and at the top of the file. If this creates a circular dependency, restructure the code (e.g. move a function to a different module) rather than working around it with a lazy import.