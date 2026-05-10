# RoboLab Infrastructure

Cloud infrastructure and deployment orchestration for RoboLab.

## Structure

- `terraform/` — Cloud infrastructure provisioning
  - `platform/secrets/` — Centralized secrets management (AWS Secrets Manager + IAM)
- `k8s/` — Argo CD GitOps platform. `argocd.yaml` declares both `argo-bootstrap-aws` and `argo-bootstrap-onprem`; K3sServer keeps the one matching the detected profile at install time. `argo-deployments/{aws,onprem}/` are each self-contained App lists for that profile (shared Apps duplicated across both folders, on-prem-only Apps live only under `onprem/`). `workloads/` holds the manifests Apps point at. `seed/` holds the one-shot setup scripts.
- `cortexflow/` — Python library for connecting ML code to the RoboLab compute cluster (Ray, MLflow, S3)
- `lambda/auth/` — Auth Lambda source code

## Related repos

- `model-gateway` — LLM provider abstraction layer
- `model-training` — Training facilities (SFT, LoRA), depends on `cortexflow`

## Important conversation rules

WORK in small increments, always consulting everything with the user.

Respond succintly, and always to the specifically asked question. Do not add unnecessary details unless asked.

Never use local/inline imports (imports inside functions, methods, or conditional blocks). All imports must be unconditional and at the top of the file. If this creates a circular dependency, restructure the code (e.g. move a function to a different module) rather than working around it with a lazy import.