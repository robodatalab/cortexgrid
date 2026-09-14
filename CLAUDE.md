# RoboLab Infrastructure

Cloud infrastructure and deployment orchestration for RoboLab.

## Structure

- `terraform/` — Cloud infrastructure provisioning
  - `platform/secrets/` — `robolab-dgx` IAM user (Route53 + S3) and the GitHub Actions OIDC provider
- `k8s/` — Argo CD GitOps platform. `argocd.yaml` declares both `argo-bootstrap-aws` and `argo-bootstrap-onprem`; K3sServer keeps the one matching the detected profile at install time. `argo_deployments/base/` holds the shared Application YAMLs; `argo_deployments/{aws,onprem}/` are kustomize overlays that pull in `base/` plus profile-local resources (`loki.yaml`, the `secrets/` ExternalSecret folder) and patches (the secrets Application's `path:`). `workloads/` holds the manifests Apps point at. `seed/` holds the one-shot setup scripts.
- `cortexgrid/` — Python library for connecting ML code to the RoboLab compute cluster (Ray, MLflow, S3)
- `lambda/auth/` — Auth Lambda source code

## Related repos

- `model-gateway` — LLM provider abstraction layer
- `model-training` — Training facilities (SFT, LoRA), depends on `cortexgrid`

## Important conversation rules

WORK in small increments, always consulting everything with the user.

Respond succintly, and always to the specifically asked question. Do not add unnecessary details unless asked.

Never use local/inline imports (imports inside functions, methods, or conditional blocks). All imports must be unconditional and at the top of the file. If this creates a circular dependency, restructure the code (e.g. move a function to a different module) rather than working around it with a lazy import.