# Integration tests

End-to-end tests that run against the deployed cluster after a workload changes on `main`. Tests live in `tests/integration/<target>/` (unittest).

## Trigger flow

```
PR merges to main with a workload manifest change
  -> push event matches workflow `paths:` filter
  -> a GitHub-hosted runner joins the tailnet and executes the workflow
     1. wait until Argo has deployed $GITHUB_SHA (or a descendant) and is Synced+Healthy
     2. kubectl apply -f <test job manifest>
     3. kubectl wait --for=condition=complete job/<job>     (block on tests)
```

## Cluster access

Each job that talks to the cluster runs [.github/actions/cluster_access](../../.github/actions/cluster_access/action.yml):

1. `tailscale/github-action` joins the tailnet as `tag:ci` using workload identity federation (GitHub OIDC token, no long-lived secret).
2. `tailscale configure kubeconfig tailscale-operator` points `kubectl` at the Tailscale operator's API server proxy.
3. The proxy impersonates the caller into the `cortexgrid-ci` group granted by the tailnet ACL; [k8s/workloads/cortexgrid/ci_rbac.yaml](../../k8s/workloads/cortexgrid/ci_rbac.yaml) limits that group to refreshing/reading the `cortexgrid` and `cortexgrid-ui` Argo apps and running the test Jobs in the `cortexgrid` namespace.

Setup outside the repo:

- Tailscale: HTTPS certificates enabled; `tag:ci` in `tagOwners`; a federated identity trusting GitHub OIDC for subject `repo:robodatalab@283442170/cortexgrid@1193122421:ref:refs/heads/main` (the repo uses GitHub's immutable subject claims, which embed the owner and repo IDs) with the `auth_keys` scope and `tag:ci`; a grant from `tag:ci` to `tag:k8s` on `tcp:443` with `tailscale.com/cap/kubernetes` impersonating group `cortexgrid-ci`.
- GitHub repository secrets: `TS_OAUTH_CLIENT_ID` and `TS_AUDIENCE` from that federated identity.

## Wait-for-deployment mechanism

Git is the source of truth; Argo only applies it. The wait job must verify that the cluster runs code at-or-past `$GITHUB_SHA` on `main` before tests run.

The job:

1. Checks out `main` with full history so both `$GITHUB_SHA` and Argo's current `.status.sync.revision` are present locally.
2. Annotates the Application with `argocd.argoproj.io/refresh=normal` to kick Argo.
3. Polls Argo's `.status.sync.revision` and loops until `git merge-base --is-ancestor $GITHUB_SHA <revision>` succeeds — i.e., the deployed commit is `$GITHUB_SHA` itself, or a descendant of it on `main`.
4. Waits for `.status.sync.status=Synced` and `.status.health.status=Healthy` on that revision.

### Why ancestry, not equality

A naive `revision == $GITHUB_SHA` check deadlocks when another commit lands on `main` between our push and Argo's sync. Argo collapses pending changes and may skip our SHA, deploying a later one directly. That later commit still contains our change, so the deploy is correct, but the equality wait would never observe our SHA.

Example: PR with SHA `a` merges; the badge bot pushes SHA `b` 15s later; Argo deploys `b`. The ancestry check accepts `b` because `a` is reachable from `b` on `main`.

## Workflows

| Workflow | Watched `paths:` | Tests |
|---|---|---|
| [.github/workflows/on_post_merge_cortexgrid.yaml](../../.github/workflows/on_post_merge_cortexgrid.yaml) | `mlflow`, `ray`, `jobs_control_plane`, `cortexgrid_integration_tests` job | `tests/integration/cortexgrid` |
| [.github/workflows/on_post_merge_cortexgrid_ui.yaml](../../.github/workflows/on_post_merge_cortexgrid_ui.yaml) | same as above plus `cortexgrid_ui/deployment_*`, `cortexgrid_ui_integration_tests` job | `tests/integration/cortexgrid_ui` |

A `concurrency` block per workflow with `cancel-in-progress: false` queues runs in order rather than collapsing them.

## How image tags reach `main`

[.github/workflows/on_pull_request.yaml](../../.github/workflows/on_pull_request.yaml) builds each changed image, tags it `<branch-slug>-<sha>`, and rewrites the image field in the corresponding workload manifest (e.g. `k8s/workloads/ray/deployment.yaml`) with `yq`. When the PR merges, those manifest edits land on `main`, and the post-merge `paths:` filter fires the integration workflow.

## Manual trigger

There is no `workflow_dispatch`. To rerun a workflow without a new merge, use the Actions UI's "Re-run jobs" button on a past run.

## Adding a new test target

1. Add tests under `tests/integration/<target>/`.
2. Add a Job manifest at `k8s/workloads/<target>_integration_tests/job.yaml`.
3. Copy an existing `on_post_merge_*` workflow. Update: `paths:`, the `argocd app sync/wait` target(s), the `kubectl apply` path, and the `kubectl wait job/<name>` name.
