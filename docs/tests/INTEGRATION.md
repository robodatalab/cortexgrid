# Integration tests

End-to-end tests that run against the deployed cluster after a workload changes on `main`. Tests live in `tests/integration/<target>/` (unittest).

## Trigger flow

```
PR merges to main with a workload manifest change
  -> push event matches workflow `paths:` filter
  -> arc-runners executes the workflow in-cluster
     1. argocd app sync <app> && argocd app wait --health --sync   (block on rollout)
     2. kubectl apply -f <test job manifest>
     3. kubectl wait --for=condition=complete job/<job>            (block on tests)
```

The runner is an `arc-runners` pod inside the cluster, so `kubectl` and `argocd` CLIs are already authenticated.

## Workflows

| Workflow | Watched `paths:` | Tests |
|---|---|---|
| [.github/workflows/on_post_merge_cortexflow.yaml](../../.github/workflows/on_post_merge_cortexflow.yaml) | `mlflow`, `ray`, `jobs_control_plane`, `cortexflow_integration_tests` job | `tests/integration/cortexflow` |
| [.github/workflows/on_post_merge_cortexflow_ui.yaml](../../.github/workflows/on_post_merge_cortexflow_ui.yaml) | same as above plus `cortexflow_ui/deployment_*`, `cortexflow_ui_integration_tests` job | `tests/integration/cortexflow_ui` |

A `concurrency` block per workflow with `cancel-in-progress: false` queues runs in order rather than collapsing them.

## How image tags reach `main`

[.github/workflows/on_pull_request.yaml](../../.github/workflows/on_pull_request.yaml) builds each changed image, tags it `<branch-slug>-<sha>`, and rewrites the image field in the corresponding workload manifest (e.g. `k8s/workloads/ray/deployment.yaml`) with `yq`. When the PR merges, those manifest edits land on `main`, and the post-merge `paths:` filter fires the integration workflow.

## Manual trigger

There is no `workflow_dispatch`. To rerun a workflow without a new merge, use the Actions UI's "Re-run jobs" button on a past run.

## Adding a new test target

1. Add tests under `tests/integration/<target>/`.
2. Add a Job manifest at `k8s/workloads/<target>_integration_tests/job.yaml`.
3. Copy an existing `on_post_merge_*` workflow. Update: `paths:`, the `argocd app sync/wait` target(s), the `kubectl apply` path, and the `kubectl wait job/<name>` name.
