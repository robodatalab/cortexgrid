# k8s

GitOps manifests watched by Argo CD. See [argo-deployments/](argo-deployments/) for the actual deployment specs. [argocd.yaml](argocd.yaml) is the one-shot bootstrap applied at seed time and is not watched by Argo.

## Node tiers

Every node is labelled `tier=main` or `tier=dev`. The label is the one signal the scheduler uses to decide *which branch's workloads go where*.

| Tier | Purpose | Current node |
|------|---------|--------------|
| `main` | Runs workloads deployed from the `main` branch (the stable deployment) | DGX Spark |
| `dev` | Runs dev-branch or dev-only workloads | ThinkStation P5 |

Nodes are seeded with [seed/setup-node.sh](seed/setup-node.sh) and torn down with [seed/teardown-node.sh](seed/teardown-node.sh). The first node seeded is also the k3s control plane and hosts the Argo CD bootstrap; that's an orthogonal concern from the tier — today it happens to be the `main`-tier node but doesn't have to be.

### Why tier is independent of k3s control-plane

When we later migrate main-branch workloads to AWS, the `main` tier will live on an AWS node while DGX becomes a `dev`-tier worker. At that point the AWS node becomes the k3s control plane too (k3s server migration is a separate operation). The tier label keeps the scheduling semantics the same across the move — dev workloads keep a `tier=dev` nodeSelector, main-branch workloads keep `tier=main`.

### Nuance: which machine hosts which branch, and what those branches represent

Today the split is 1-to-1: one branch → one machine. `main` → DGX, `dev` → P5.

As we add nodes and migrate to AWS, we'll likely split *infra* from *jobs* across tiers rather than by branch alone. For example, MLflow + Grafana + Prometheus (shared state, long-lived, cheap CPU) may live on AWS under `tier=main`, while Ray (GPU-bound, bursty) keeps running on DGX regardless of which branch submitted the job. When that happens, individual workload manifests will declare their own nodeSelector / affinity, and `tier` becomes one of several scheduling inputs rather than the only one.

## Features

### Multi-node Ray

`ray-head` is pinned to `tier=main` (DGX) via `nodeSelector`. A `ray-worker` DaemonSet runs on every `tier=dev` node, registering to the head via the in-cluster Service at `ray-head.ray.svc.cluster.local:6379`. All GPUs (1 on DGX + 2 on P5 today) join one Ray pool — a submitted Ray job asking for 1 GPU can land on any of them; a job asking for 2 or 3 GPUs parallelises across nodes. No code change at the cortexflow submission site; Ray handles GPU assignment transparently. See [workloads/ray/](workloads/ray/).

## Future extensions

### Branch dev-environments

**Problem.** Changes to `robolab-infra` shouldn't block downstream repositories consuming its `main` branch. Users of the `cortexflow` library in separate experiment repos need a stable MLflow, Ray, and S3 always reachable. We want to iterate on an infra branch end-to-end without pushing to `main` first.

**Shape.** One `ApplicationSet` per component we want replicated per PR, using Argo's `pullRequest` generator to emit one Application per open PR, sourced from that branch, into a namespace like `dev-pr-<N>`. CI already tags images `<branch-slug>-<sha>`; per-Application Image Updater regexes match only the right branch's tags. Not every component would get a per-branch copy — stateful/GPU-bound ones (MLflow, Ray) stay on `main` and are consumed cross-namespace; only actively-iterated workloads (cortexflow-ui, jobs-control-plane) get per-branch copies.

### Opportunistic dev-node utilisation

When no dev-branch work is active, `tier=dev` nodes sit idle. Strict `nodeSelector: tier=dev` on dev workloads + unconstrained main workloads lets main spread onto dev nodes opportunistically, retreating the moment a dev workload claims a GPU.

### AWS migration

When `main` migrates to AWS, dev-envs stay on DGX/P5 (`tier=dev`), production on AWS (`tier=main`). ApplicationSet templates parameterise `destination.server` so PR Applications land on the dev cluster while main Applications land on AWS — same manifest shape, routed by tier.

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
