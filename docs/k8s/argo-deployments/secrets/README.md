# Secrets

ArgoCD needs credentials at runtime to provision K8s Pods — AWS keys, GitHub token, registry auth. The need arises from pod orchestration (cloning private repos, pulling private images, calling AWS APIs), not from application code inside the pods. App code reaches its own secrets through `cortexflow.secrets`, independently.

## Cortexflow infrastructure environment

Cortexflow assumes that:

1. AWS Secrets Manager is the only source of truth and stores and manages all of the configuration of the research platform and the applications and experiments running on it.
2. The entire infrastructure can only be seeded once - when it's deployed for the first time. [setup-node.py](../../../../k8s/seed/setup-node.py) is responsible for that
3. Credentials rotate and cannot be cached for any longer that 12 hrs.

All secrets — ArgoCD bootstrap (GHCR pull, repo clone, AWS access) and application-level (consumed by `cortexflow.secrets` at runtime) — live under a single namespace `robolab/infra/*`. Both consumers reach them through `cortexflow.secrets` (ExternalSecrets operator reads via AWS SDK; application code via `cortexflow.secrets.get_secret()`).


## Functionality of deployments from k8s/argo-deployments/secrets

external-secrets/ connect to AWS Secrets Manager and will act as a provider of keys used to provision new K8s Pods. These secrets, specifically, will be:
- GitHub access tokens to grant access to organization's private repositories
- GitHub Container Registry access tokens to grant access to organization's private Docker Images
- AWS deployment service account credentials that authorize ArgoCD to deploy platform and applications in  AWS

reflector/ replicates a single Secret into every K8s namespace, present and future. K8s Secrets are namespace-scoped, so without it we would have to enumerate target namespaces up front and create one ExternalSecret per namespace. Used for secrets that every pod may need regardless of namespace (e.g. the GHCR pull secret).

## Adding a new secret

The user will be responsible for adding those required secrets:

1. Store value in AWS Secrets Manager at `robolab/infra/<NAME>` (via [cortexflow/secrets.py](../../../../cortexflow/secrets.py) or AWS console).
2. Add an `ExternalSecret` YAML in [external-secrets/](../../../../k8s/argo-deployments/aws/secrets/external-secrets/) referencing `key: robolab/infra/<NAME>`.
3. Commit + push. ESO creates the k8s Secret; rotation auto-propagates.

There is no static list — it grows with the platform. A new secret is needed whenever a deployment fails with an auth error (ArgoCD shows it, `kubectl describe` names the missing credential) or when adding a deployment that talks to a new external system.

