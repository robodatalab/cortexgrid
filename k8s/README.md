# k8s

GitOps manifests watched by Argo CD. See [argo-deployments/](argo-deployments/) for the actual deployment specs. [argocd.yaml](argocd.yaml) is the one-shot bootstrap applied at seed time and is not watched by Argo.

## Node tiers

Every node is labelled `tier=main` or `tier=dev`. The label is the one signal the scheduler uses to decide *which branch's workloads go where*.

| Tier | Purpose | Current node |
|------|---------|--------------|
| `main` | Runs workloads deployed from the `main` branch (the stable deployment) | DGX Spark |
| `dev` | Runs workloads from per-PR dev environments (branches in flight) | ThinkStation P5 |

Nodes are seeded with [seed/setup-node.sh](seed/setup-node.sh) and torn down with [seed/teardown-node.sh](seed/teardown-node.sh). The first node seeded is also the k3s control plane and hosts the Argo CD bootstrap; that's an orthogonal concern from the tier — today it happens to be the `main`-tier node but doesn't have to be.

### Why tier is independent of k3s control-plane

When we later migrate main-branch workloads to AWS, the `main` tier will live on an AWS node while DGX becomes a `dev`-tier worker. At that point the AWS node becomes the k3s control plane too (k3s server migration is a separate operation). The tier label keeps the scheduling semantics the same across the move — PR-branch workloads keep a `tier=dev` nodeSelector, main-branch workloads keep `tier=main`.

### Nuance: which machine hosts which branch, and what those branches represent

Today the split is 1-to-1: one branch → one machine. `main` → DGX, `dev` → P5.

As we add nodes and migrate to AWS, we'll likely split *infra* from *jobs* across tiers rather than by branch alone. For example, MLflow + Grafana + Prometheus (shared state, long-lived, cheap CPU) may live on AWS under `tier=main`, while Ray (GPU-bound, bursty) keeps running on DGX regardless of which branch submitted the job. When that happens, individual workload manifests will declare their own nodeSelector / affinity, and `tier` becomes one of several scheduling inputs rather than the only one.

## Branch dev-environments

### What this is for

Changes to `robolab-infra` (this repo) shouldn't block downstream repositories that depend on its `main` branch. The `cortexflow` library users — experiment code in other repos — need a stable MLflow, Ray, and S3 reachable on `main` at all times. We, as infra developers, want to iterate on an infra branch and test the result end-to-end without pushing to `main` first. Branch dev-environments give every open `robolab-infra` PR its own isolated deployment of whichever components are under test, leaving `main` untouched.

### How it's implemented

One `ApplicationSet` per component we want to replicate per PR. Each uses Argo's `pullRequest` generator to poll GitHub, emitting one Argo Application per open PR sourced from that branch, into a namespace like `dev-pr-<N>`. CI tags images as `<branch-slug>-<sha>`; per-Application Image Updater regexes match only the right branch's tags.

Not every component gets a per-branch copy. Shared state (MLflow + its Postgres, MinIO buckets) and GPU-heavy workloads (Ray) stay on `main` and are consumed by the PR namespace via cross-namespace service DNS. Only workloads actively under test — typically `cortexflow-ui` and `jobs-control-plane` — get per-branch copies. The concrete list lives as files in [argo-deployments/](argo-deployments/) — add or remove an `ApplicationSet` to change it.

### Future extension — AWS

When `main` migrates to AWS, dev-envs stay on DGX/P5 (`tier=dev`), production on AWS (`tier=main`). The ApplicationSet's template can parameterise `destination.server` so PR Applications land on the dev cluster while main-sourced Applications land on AWS — same manifest shape, routed by tier.

### Future extension — opportunistic dev-node utilisation

When no infra branches are open, P5 (tier=dev) sits idle. We'd pin PR workloads with a hard `nodeSelector: tier=dev` but leave main workloads *unconstrained* so k8s schedules them wherever capacity is free. Main therefore spreads onto P5 whenever dev is otherwise quiet, and retreats to DGX the moment a PR env claims P5's GPU.

### Future extension — Ray jobs spanning DGX + P5

Today `ray-head` is a single-node Deployment with one GPU. To let a single Ray job use DGX + P5's GPUs as one pool, add a `ray-worker` Deployment/DaemonSet that joins the head via a headless Service, one worker per node, each requesting `nvidia.com/gpu: 1` with topology-spread or pod anti-affinity. Ray's scheduler then treats all GPUs as a single pool — submitted jobs parallelise across nodes with no change at the submission site.

## FAQ

### Argo sync is stuck / app stays OutOfSync after a Git push

The operation hit its retry limit, or Argo cached a stale state. Clear it:

```bash
kubectl -n argocd patch app <app-name> --type merge -p '{"operation": null}'
kubectl -n argocd annotate app <app-name> argocd.argoproj.io/refresh=hard --overwrite
```

### How do I verify Argo is actually looking at my latest commit?

```bash
kubectl -n argocd get app <app-name> -o jsonpath='{.status.sync.revision}'
git show <that-sha>:<path-to-file>
```

If the SHA is stale, force a hard refresh as above.

### A sync fails because a CRD isn't installed yet

Argo validates all resources upfront; a resource whose CRD comes from a sibling Application will fail validation before sync-waves take effect. Annotate the CRD-dependent resource:

```yaml
metadata:
  annotations:
    argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true
```

## Known issues we accept

### SSA field-ownership breakage when changing `Deployment.strategy.type`

**Symptom.** Argo sync fails with `Deployment.apps ... is invalid: spec.strategy.rollingUpdate: Forbidden: may not be specified when strategy type is 'Recreate'`. Live Deployment holds stale `rollingUpdate` subfields owned by `kube-controller-manager` (default-filled when `type: RollingUpdate`). ServerSideApply can't remove them — it only touches fields it owns.

**Why we don't do anything preemptive.** The fix is `argocd.argoproj.io/sync-options: Replace=true` on the affected Deployment, which swaps SSA for `kubectl replace` and wipes stale fields. Applying that blanket to every Deployment costs pod churn on every manifest edit (image tag, env, probe — all trigger a full recreate). The trigger — changing `strategy.type` — is rare, so we eat the one-time pain of adding the annotation when it bites. See [workloads/ray/deployment.yaml](workloads/ray/deployment.yaml) for the live example.

### Ray worker DaemonSet hardcodes GPU count to 2

**Symptom.** [workloads/ray/worker-daemonset.yaml](workloads/ray/worker-daemonset.yaml) requests `nvidia.com/gpu: 2` and passes `--num-gpus=2` on every dev-tier node. This matches P5 exactly. If a future `tier=dev` node has 1 GPU the pod won't schedule; if it has 4 GPUs the pod will only use 2.

**Why we don't do anything preemptive.** Without KubeRay there's no clean k8s-native way to say "one worker per GPU on each node with variable count." Two realistic fixes when it bites: (a) swap the DaemonSet for a per-node Deployment with explicit replicas/GPU requests, or (b) multiple DaemonSets filtered by an extra label like `gpu-count=2`. Both are fine; neither's worth doing until we actually add a dev node with a different GPU count.
