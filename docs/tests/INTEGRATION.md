# Integration test platform

End-to-end tests that run against the deployed cluster after every successful Argo CD sync. Tests live in this repo's `tests/integration/` (and, in time, in consumer repos).

## Trigger flow

```
Argo CD sync success on a subscribed Application
  -> Argo Notifications controller fires webhook
  -> POST https://api.github.com/repos/paksas/robolab-infra/dispatches
       (event_type: argocd-synced, client_payload: {app, aws_role_arn})
  -> GitHub Actions workflows triggered by repository_dispatch type "argocd-synced"
  -> Workflow assumes robolab-github-actions role (ARN from payload), joins tailnet, runs tests
```

## What's wired

- **Notifications controller** is enabled in the argocd Helm chart via [k8s/argocd.yaml](../../k8s/argocd.yaml). The webhook notifier, template, and `on-sync-succeeded` trigger are all in the `valuesContent` block.
- **Subscribed Applications** carry the annotation `notifications.argoproj.io/subscribe.on-sync-succeeded.github: ""`:
  - cortexflow-ui, mlflow, ray, jobs-control-plane (cortexflow's cluster-side stack)
  - external-secrets, reflector (secrets plumbing)
- **Notifications secret** ([k8s/argo-deployments/aws/secrets/external-secrets/argocd-notifications.yaml](../../k8s/argo-deployments/aws/secrets/external-secrets/argocd-notifications.yaml)) is materialized by ESO from SM. Two keys:
  - `github-token` <- `robolab/infra/GH_TOKEN` (used in the webhook Authorization header)
  - `aws-role-arn` <- `robolab/infra/AWS_ROLE_ARN` (substituted into the dispatch payload)

## Workflows

Each workflow listens for `repository_dispatch: argocd-synced` and runs `unittest discover` against a target directory.

- [.github/workflows/cortexflow-integration-tests.yml](../../.github/workflows/cortexflow-integration-tests.yml) -> `tests/integration/cortexflow`
- [.github/workflows/cortexflow-ui-integration-tests.yml](../../.github/workflows/cortexflow-ui-integration-tests.yml) -> `tests/integration/cortexflow_ui`

Each workflow:

1. Assumes `robolab-github-actions` via OIDC using `client_payload.aws_role_arn`.
2. Fetches `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` from SM.
3. Joins the tailnet via `tailscale/github-action@v2` with `tag:ci`.
4. Runs `uv run python -m unittest discover -s <target>`.

A `concurrency` block with `cancel-in-progress: true` collapses fan-out: if 6 Applications sync at once, only the last dispatch's run survives.

## Single sources of truth

| Value | Lives in | Flows to |
|---|---|---|
| GitHub PAT (`GH_TOKEN`) | `.env` | SM (via EnvSecrets) -> argocd-notifications-secret (ESO) |
| Tailscale OAuth (`TS_OAUTH_CLIENT_ID`/`SECRET`) | `.env` | SM (via EnvSecrets) -> workflow env (via OIDC + `aws secretsmanager get-secret-value`) |
| GitHub Actions role ARN (`AWS_ROLE_ARN`) | terraform ([terraform/platform/secrets/iam.tf](../../terraform/platform/secrets/iam.tf)) | SM -> argocd-notifications-secret -> webhook payload -> workflow |

The role ARN is the one bootstrap value the workflow can't fetch from SM (since SM access requires assuming the role). It travels in the dispatch payload so it's still sourced from terraform, not duplicated in a GH variable.

## Apply order

Different changes propagate through different paths:

| Change | How to apply |
|---|---|
| Terraform secrets module (e.g. new SM entry) | `cd terraform/platform/secrets && terraform apply` |
| `.env` (e.g. new key) | `make head-setup IP=<head-ip> STORAGE_PATH=/storage` (re-runs EnvSecrets) |
| `k8s/argocd.yaml` (notifications template, Helm values) | `kubectl apply -f k8s/argocd.yaml` (or re-run `make head-setup`) |
| `k8s/argo-deployments/**` (ExternalSecrets, Application annotations) | push to `main`; Argo CD syncs |
| `.github/workflows/**` | push to any branch (workflows are read from default branch on dispatch) |

## Adding a new test target

1. Add tests under `tests/integration/<target>/` using `unittest`.
2. Copy one of the existing workflow files in `.github/workflows/` and change `name`, `concurrency.group`, and the `discover -s` path.
3. If the tests should run only when specific Applications sync, add a top-level `if:` on the `test` job filtering on `github.event.client_payload.app`.

## Adding a new subscribed Application

Add the annotation to the Application CR:

```yaml
metadata:
  annotations:
    notifications.argoproj.io/subscribe.on-sync-succeeded.github: ""
```

Push to main; Argo picks it up on next sync.
