# Secrets

ArgoCD needs credentials at runtime to provision K8s Pods — AWS keys, GitHub token, registry auth. The need arises from pod orchestration (cloning private repos, pulling private images, calling AWS APIs), not from application code inside the pods. App code reaches its own secrets through `cortexgrid.secrets`, independently.

## Cortexgrid infrastructure environment

Cortexgrid assumes that:

1. The head secrets server is the only source of truth for the configuration of the research platform and the applications and experiments running on it. It runs on the head host ([cortexgrid_head.py](../../../../k8s/seed/scripts/cortexgrid_head.py), port 7700) and stores every value in `/etc/cortexgrid/.env`.
2. Everything runs on the tailnet, so the server has no authentication.
3. Head setup seeds it: `.env.head` values, terraform outputs (AWS) and generated credentials (on-prem).

All secrets — ArgoCD bootstrap (GHCR pull, repo clone) and application-level (consumed by `cortexgrid.secrets` at runtime) — live in that one store, keyed by plain names such as `GH_TOKEN`. The External Secrets Operator reads them through its webhook provider; application code through `cortexgrid.secrets.get_secret()`.


## Functionality of deployments from k8s/argo_deployments/secrets

external-secrets/ defines the `cortexgrid-head` ClusterSecretStore, which calls the head secrets server through the `cortexgrid-head` Service in the `default` namespace (created by the seed's BootstrapSecrets), and the ExternalSecrets that turn stored values into K8s Secrets used to provision Pods:
- GitHub access tokens to grant access to organization's private repositories
- GitHub Container Registry access tokens to grant access to organization's private Docker Images
- Route53 credentials for cert-manager and object-storage credentials for the workloads

reflector/ replicates a single Secret into every K8s namespace, present and future. K8s Secrets are namespace-scoped, so without it we would have to enumerate target namespaces up front and create one ExternalSecret per namespace. Used for secrets that every pod may need regardless of namespace (e.g. the GHCR pull secret).

## Adding a new secret

The user will be responsible for adding those required secrets:

1. Store the value in the head secrets store as `<NAME>`: add it to `.env.head` before head setup, or call `cortexgrid.set_secret("<NAME>", value)` (or the UI's Secrets page) on a running cluster.
2. Add an `ExternalSecret` YAML in [external-secrets/](../../../../k8s/argo_deployments/base/secrets/external-secrets/) referencing `key: <NAME>` in the `cortexgrid-head` ClusterSecretStore.
3. Commit + push. ESO creates the k8s Secret; changes propagate on its refresh interval.

There is no static list — it grows with the platform. A new secret is needed whenever a deployment fails with an auth error (ArgoCD shows it, `kubectl describe` names the missing credential) or when adding a deployment that talks to a new external system.
