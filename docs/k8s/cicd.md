# CI/CD requirements

What the pipeline must guarantee. Implementation lives in [.github/workflows/](../../.github/workflows/).

1. **Tests run on every PR.** Every push to a PR branch re-runs the relevant test suites.

2. **Images are built post-merge, with retry on transient failure.** Image builds happen only after the PR is merged to `main`. Failures caused by anything other than a code error (network timeouts, registry outages, runner flakes) are retried automatically.

3. **Integration tests run only after the full PR is deployed.** They fire only when every image rebuilt for that PR's merge has landed in the Argo-managed environment — never partway through a rollout.

4. **Exactly one integration-test run per PR.**

5. **Exactly one image build per service per PR.**

6. **Only changed services rebuild.** A PR that doesn't touch a service's inputs must not trigger that service's image build.

## Target pipeline

Single principle: every bit of work that determines what main looks like happens **on the PR branch**, so the merge commit is the only new revision on main per PR.

One diagram per case. Every node is tagged with the phase it runs in:

- **[PR push]** — fires every time someone pushes to the PR branch
- **[Merge]** — fires when the PR is squash-merged into `main`
- **[Argo]** — Argo CD reconciling `main` after the merge commit lands

All image builds tag `ghcr.io/<owner>/<service>:<head-sha>` and skip if that tag is already in GHCR; transient failures (network / registry / runner flake) are retried; terminal failures (Dockerfile error) fail the PR.

Mixtures (e.g. `cortexgrid/` + `cortexgrid_ui/frontend/`) are the union of matching lanes. Every case ends at the same convergence diagram at the bottom.

### Case 1 — `cortexgrid/**` (the library)

```mermaid
flowchart TB
    src["cortexgrid/** changed"]

    subgraph S1["Stage 1: PR push (on every push to PR branch)"]
        direction TB
        T1["cortexgrid-tests.yml"]
        T2["cortexgrid-ui-tests.yml :: backend"]
        T3["jobs-control-plane-tests.yml"]
        B1["build cortexgrid-ui-backend image"]
        B2["build jobs-control-plane image"]
        E1["set new cortexgrid-ui-backend image in its deployment"]
        E2["set new jobs-control-plane image in its deployment"]
        E3["bump cortexgrid library patch version, uv lock"]
        C["commit changes to PR branch"]
        T1 --> B1
        T2 --> B1
        T1 --> B2
        T3 --> B2
        B1 --> E1
        B2 --> E2
        B1 --> E3
        B2 --> E3
        E1 --> C
        E2 --> C
        E3 --> C
    end

    src --> T1
    src --> T2
    src --> T3

    subgraph S2["Stage 2: Squash & Merge to main"]
        M["one new commit on main"]
    end
    C --> M

    subgraph S3["Stage 3: Argo CD reconcile on main"]
        direction TB
        Sync["Argo re-resolves all Apps to new main HEAD"]
        Roll_uib["cortexgrid-ui App rolls a new backend pod"]
        Roll_jcp["jobs-control-plane App rolls a new pod"]
        Agg_cf["cortexgrid aggregator: Synced+Healthy"]
        Agg_ui["ui aggregator: Synced+Healthy"]
        N_cf["fires cortexgrid-stack-deployed (once per revision)"]
        N_ui["fires cortexgrid-ui-stack-deployed (once per revision)"]
        IT_cf["cortexgrid-integration-tests.yml runs"]
        IT_uic["cortexgrid-ui-integration-tests.yml runs"]

        Sync --> Roll_uib
        Sync --> Roll_jcp
        Roll_jcp --> Agg_cf
        Roll_uib --> Agg_ui
        Agg_cf --> Agg_ui
        Agg_cf --> N_cf
        Agg_ui --> N_ui
        N_cf --> IT_cf
        N_ui --> IT_uic
    end
    M --> Sync
```

### Test it
- input: any `.py` under `cortexgrid/`
- on PR: `test-cortexgrid`, `test-cortexgrid-ui-backend`, `test-jobs-control-plane`, `build-cortexgrid-ui-backend`, `build-jobs-control-plane`, `update_image_tags` (version bump, `uv.lock` and image tags in one bot commit)
- after squash-merge in Argo: `cortexgrid-ui` and `jobs-control-plane` synced at the merge SHA (`kubectl -n argocd get app cortexgrid-ui jobs-control-plane -o custom-columns='NAME:.metadata.name,SYNC:.status.sync.status,HEALTH:.status.health.status,REV:.status.sync.revision,SYNCED_AT:.status.operationState.finishedAt'`)
- after ~10 min in GitHub Actions: one `cortexgrid integration tests` run and one `cortexgrid-ui integration tests` run (`gh run list --limit 5`)

### Case 2 — `cortexgrid_ui` (code, Dockerfile, or manifest)

```mermaid
flowchart TB
    b["cortexgrid_ui/backend/** code changed"]
    f["cortexgrid_ui/frontend/** code changed"]
    db["k8s/docker/cortexgrid_ui/backend/** Dockerfile changed"]
    df["k8s/docker/cortexgrid_ui/frontend/** Dockerfile changed"]
    wl["k8s/workloads/cortexgrid_ui/** manifest direct-edit"]

    subgraph S1["Stage 1: PR push (only when a code or Dockerfile change needs rebuilding)"]
        direction TB
        T1["cortexgrid-ui-tests.yml :: backend"]
        T2["cortexgrid-ui-tests.yml :: frontend"]
        B1["build cortexgrid-ui-backend image"]
        B2["build cortexgrid-ui-frontend image"]
        E1["set new cortexgrid-ui-backend image in its deployment"]
        E2["set new cortexgrid-ui-frontend image in its deployment"]
        C["bot commit changes to PR branch"]
        T1 --> B1
        T2 --> B2
        B1 --> E1
        B2 --> E2
        E1 --> C
        E2 --> C
    end

    b --> T1
    f --> T2
    db --> B1
    df --> B2

    subgraph S2["Stage 2: Squash & Merge to main"]
        M["one new commit on main"]
    end
    C --> M
    wl -. manifest already final, no rebuild .-> M

    subgraph S3["Stage 3: Argo CD reconcile on main"]
        direction TB
        Sync["Argo re-resolves all Apps to new main HEAD"]
        Roll_ui["cortexgrid-ui App rolls new backend and/or frontend pod"]
        Agg_ui["ui aggregator: Synced+Healthy"]
        N_ui["fires cortexgrid-ui-stack-deployed"]
        IT_uic["cortexgrid-ui-integration-tests.yml runs"]

        Sync --> Roll_ui
        Roll_ui --> Agg_ui
        Agg_ui --> N_ui
        N_ui --> IT_uic
    end
    M --> Sync
```

### Test it
- input: any `.py` or `.ts` under `cortexgrid_ui/`
- on PR: `test-cortexgrid-ui-backend` and/or `test-cortexgrid-ui-frontend`, `build-cortexgrid-ui-backend` and/or `build-cortexgrid-ui-frontend`
- after squash-merge in Argo: `cortexgrid-ui` synced (`kubectl -n argocd get app cortexgrid-ui`)
- after ~10 min in GitHub Actions: one `cortexgrid-ui integration tests` run; `cortexgrid integration tests` does NOT run (`gh run list --limit 5`)

### Case 3 — `jobs_control_plane` (code, Dockerfile, or manifest)

```mermaid
flowchart TB
    src["jobs_control_plane/** code changed"]
    dsrc["k8s/docker/jobs_control_plane/** Dockerfile changed"]
    wl["k8s/workloads/jobs_control_plane/** manifest direct-edit"]

    subgraph S1["Stage 1: PR push (only when a code or Dockerfile change needs rebuilding)"]
        direction TB
        T1["jobs-control-plane-tests.yml"]
        B1["build jobs-control-plane image"]
        E1["set new jobs-control-plane image in its deployment"]
        C["bot commit changes to PR branch"]
        T1 --> B1
        B1 --> E1
        E1 --> C
    end

    src --> T1
    dsrc --> B1

    subgraph S2["Stage 2: Squash & Merge to main"]
        M["one new commit on main"]
    end
    C --> M
    wl -. manifest already final, no rebuild .-> M

    subgraph S3["Stage 3: Argo CD reconcile on main"]
        direction TB
        Sync["Argo re-resolves all Apps to new main HEAD"]
        Roll_jcp["jobs-control-plane App rolls a new pod"]
        Agg_cf["cortexgrid aggregator: Synced+Healthy"]
        Agg_ui["ui aggregator: Synced+Healthy (cortexgrid child rolled)"]
        N_cf["fires cortexgrid-stack-deployed"]
        N_ui["fires cortexgrid-ui-stack-deployed"]
        IT_cf["cortexgrid-integration-tests.yml runs"]
        IT_uic["cortexgrid-ui-integration-tests.yml runs
        (UI consumes from cortexgrid stack via cortexgrid.jobs; jcp behavior change affects UI)"]

        Sync --> Roll_jcp
        Roll_jcp --> Agg_cf
        Agg_cf --> Agg_ui
        Agg_cf --> N_cf
        Agg_ui --> N_ui
        N_cf --> IT_cf
        N_ui --> IT_uic
    end
    M --> Sync
```

### Test it
- input: any `.py` under `jobs_control_plane/`
- on PR: `test-jobs-control-plane`, `build-jobs-control-plane`
- after squash-merge in Argo: `jobs-control-plane` synced (`kubectl -n argocd get app jobs-control-plane`)
- after ~10 min in GitHub Actions: one `cortexgrid integration tests` run AND one `cortexgrid-ui integration tests` run (`gh run list --limit 5`)

### Case 4 — `mlflow` (Dockerfile or manifest)

```mermaid
flowchart TB
    dsrc["k8s/docker/mlflow/** Dockerfile changed"]
    wl["k8s/workloads/mlflow/** manifest direct-edit"]

    subgraph S1["Stage 1: PR push (only when the Dockerfile change needs rebuilding)"]
        direction TB
        B1["build mlflow image"]
        E1["set new mlflow image in its deployment"]
        C["bot commit changes to PR branch"]
        B1 --> E1
        E1 --> C
    end

    dsrc --> B1

    subgraph S2["Stage 2: Squash & Merge to main"]
        M["one new commit on main"]
    end
    C --> M
    wl -. manifest already final, no rebuild .-> M

    subgraph S3["Stage 3: Argo CD reconcile on main"]
        direction TB
        Sync["Argo re-resolves all Apps to new main HEAD"]
        Roll_ml["mlflow App rolls a new pod"]
        Agg_cf["cortexgrid aggregator: Synced+Healthy"]
        Agg_ui["ui aggregator: Synced+Healthy"]
        N_cf["fires cortexgrid-stack-deployed"]
        N_ui["fires cortexgrid-ui-stack-deployed"]
        IT_cf["cortexgrid-integration-tests.yml runs"]
        IT_uic["cortexgrid-ui-integration-tests.yml runs"]

        Sync --> Roll_ml
        Roll_ml --> Agg_cf
        Agg_cf --> Agg_ui
        Agg_cf --> N_cf
        Agg_ui --> N_ui
        N_cf --> IT_cf
        N_ui --> IT_uic
    end
    M --> Sync
```

### Test it
- input: edit `k8s/docker/mlflow/Dockerfile`
- on PR: `build-mlflow`
- after squash-merge in Argo: `mlflow` synced (`kubectl -n argocd get app mlflow`)
- after ~10 min in GitHub Actions: one `cortexgrid integration tests` run AND one `cortexgrid-ui integration tests` run (`gh run list --limit 5`)

### Case 5 — `ray` (Dockerfile or manifest)

```mermaid
flowchart TB
    dsrc["k8s/docker/ray/** Dockerfile changed"]
    wl["k8s/workloads/ray/** manifest direct-edit"]

    subgraph S1["Stage 1: PR push (only when the Dockerfile change needs rebuilding)"]
        direction TB
        B1["build ray-head image"]
        E1["set new ray-head image in its deployment"]
        C["bot commit changes to PR branch"]
        B1 --> E1
        E1 --> C
    end

    dsrc --> B1

    subgraph S2["Stage 2: Squash & Merge to main"]
        M["one new commit on main"]
    end
    C --> M
    wl -. manifest already final, no rebuild .-> M

    subgraph S3["Stage 3: Argo CD reconcile on main"]
        direction TB
        Sync["Argo re-resolves all Apps to new main HEAD"]
        Roll_ray["ray App rolls a new pod"]
        Agg_cf["cortexgrid aggregator: Synced+Healthy"]
        Agg_ui["ui aggregator: Synced+Healthy"]
        N_cf["fires cortexgrid-stack-deployed"]
        N_ui["fires cortexgrid-ui-stack-deployed"]
        IT_cf["cortexgrid-integration-tests.yml runs"]
        IT_uic["cortexgrid-ui-integration-tests.yml runs"]

        Sync --> Roll_ray
        Roll_ray --> Agg_cf
        Agg_cf --> Agg_ui
        Agg_cf --> N_cf
        Agg_ui --> N_ui
        N_cf --> IT_cf
        N_ui --> IT_uic
    end
    M --> Sync
```

### Test it
- input: edit `k8s/docker/ray/Dockerfile`
- on PR: `build-ray-head`
- after squash-merge in Argo: `ray` synced (`kubectl -n argocd get app ray`)
- after ~10 min in GitHub Actions: one `cortexgrid integration tests` run AND one `cortexgrid-ui integration tests` run (`gh run list --limit 5`)

### Case 6 — `arc-runner` (Dockerfile or kustomization)

```mermaid
flowchart TB
    dsrc["k8s/docker/arc-runner/** Dockerfile changed"]
    wl["k8s/argo_deployments/{onprem,aws}/arc-runner-set/** kustomization direct-edit"]

    subgraph S1["Stage 1: PR push (only when the Dockerfile change needs rebuilding)"]
        direction TB
        B1["build arc-runner image"]
        E1["set new arc-runner image in arc-runner-set kustomization"]
        C["bot commit changes to PR branch"]
        B1 --> E1
        E1 --> C
    end

    dsrc --> B1

    subgraph S2["Stage 2: Squash & Merge to main"]
        M["one new commit on main"]
    end
    C --> M
    wl -. kustomization already final, no rebuild .-> M

    subgraph S3["Stage 3: Argo CD reconcile on main"]
        direction TB
        Sync["Argo re-resolves all Apps to new main HEAD"]
        Roll_arc["arc-runner-set App rolls new runner pods
        (separate App, not under any aggregator with notifications; no integration tests fire)"]

        Sync --> Roll_arc
    end
    M --> Sync
```

### Test it
- input: edit `k8s/docker/arc-runner/Dockerfile`
- on PR: `build-arc-runner`
- after squash-merge in Argo: `arc-runner-set` synced (`kubectl -n argocd get app arc-runner-set`)
- after ~10 min in GitHub Actions: no integration test runs (`gh run list --limit 5`)

### Case 7 — `k8s/argo_deployments/**` (Argo Application definitions)

```mermaid
flowchart TB
    src["k8s/argo_deployments/** changed (Application specs, paths, sync policies)"]

    subgraph S1["Stage 1: PR push"]
        direction TB
        N1["no build, no tag bump; manifest is already final"]
    end

    src --> N1

    subgraph S2["Stage 2: Squash & Merge to main"]
        M["one new commit on main"]
    end
    N1 --> M

    subgraph S3["Stage 3: Argo CD reconcile on main"]
        direction TB
        Sync["Argo re-resolves App-of-Apps to new main HEAD"]
        Reload["parent App reconfigures children (Apps can be created, pruned, or have their source updated)"]
        Branch["any cascading App rolls fire notifications per the routing in Cases 4a-e
        (no rollouts -> no notifications)"]

        Sync --> Reload
        Reload --> Branch
    end
    M --> Sync
```

### Test it
- input: edit any Application spec under `k8s/argo_deployments/`
- on PR: no build, no bot commit
- after squash-merge in Argo: parent App reconfigures children (`kubectl -n argocd get app`)
- after ~10 min in GitHub Actions: integration tests fire only if a workload App actually rolled (`gh run list --limit 5`)

### Case 8 — `k8s/seed/**` or `terraform/**` (no Argo coupling)

```mermaid
flowchart TB
    seed["k8s/seed/** changed"]
    tf["terraform/** changed"]

    subgraph S1["Stage 1: PR push"]
        direction TB
        N1["no build, no tag bump"]
    end

    seed --> N1
    tf --> N1

    subgraph S2["Stage 2: Squash & Merge to main"]
        M["one new commit on main"]
    end
    N1 --> M

    subgraph S3["Stage 3: Argo CD reconcile on main"]
        direction TB
        Note["no Argo coupling: seed scripts run via make targets, terraform apply runs manually; nothing fires in CI"]
    end
    M --> Note
```

### Test it
- input: edit a file under `k8s/seed/` or `terraform/`
- on PR: no build, no bot commit
- after squash-merge in Argo: nothing (no Argo coupling)
- after ~10 min in GitHub Actions: no integration test runs (`gh run list --limit 5`)

Mapping back to requirements:

1. **Tests on every PR** — `pull_request` trigger on the three test workflows.
2. **Robust post-merge build with retry** — moved to PR phase; build step wraps `docker buildx` in a retry loop that distinguishes transient (timeout, 5xx, DNS) from terminal (Dockerfile error, non-zero from `RUN`) failures.
3. **Integration tests only after full PR deployed** — kustomization bumps are inside the merge commit, so Argo's Synced+Healthy on that revision means *all* of the PR's images are rolled out.
4. **Exactly one integration run per PR** — only one new revision per PR (the merge commit); `oncePer: sync.revision` fires once.
5. **Exactly one image build per service per PR** — `detect` skips a service whose `image:<head-sha>` already exists in GHCR, so PR iterations don't redundantly rebuild the same source.
6. **Only changed services rebuild** — `detect` filters per service.

Dependencies / unresolved:

- **Same-repo PR write-back from arc-runners.** `actions/checkout@v4` with `ref: github.head_ref` previously failed with `git fetch` exit 1 on these runners. Root cause not yet diagnosed (research agent recommended repro with `ACTIONS_STEP_DEBUG=true` + `git ls-remote`). PR-side bumps are blocked until this is fixed.
- **The bot commit's own run.** `update_image_tags` pushes its single bot commit only after every build has finished, and that commit becomes the PR head. GitHub does start an `on_pull_request` run for it, held for approval (`action_required`). Approved or not, that run builds nothing, so the pipeline terminates: `detect` marks every service unchanged when `github.actor` is `github-actions[bot]`, and its `all_checks_passed` repeats the verdict the pushing run posted on the commit. The pushing run's `all_checks_passed` posts that verdict as an `all_checks_passed` commit status, so the PR head shows the pipeline's result without the approval. Merge only once the head shows it: until then builds may still be running. `[skip ci]` in the bot commit is not an option: squash merges copy every commit message into the merge commit, which would skip the post-merge workflows on `main`.
