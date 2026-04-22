# k8s

GitOps manifests watched by Argo CD. See [argo-deployments/](argo-deployments/) for the actual deployment specs. [argocd.yaml](argocd.yaml) is the one-shot bootstrap applied at seed time and is not watched by Argo.

## Workload placement

**Current policy:** every first-party workload runs on P5 (`nodeSelector: kubernetes.io/arch: amd64`). The only exception is `ray-worker`, which runs as a DaemonSet on both nodes so Ray can dispatch GPU jobs to either the DGX or the P5. DGX is effectively the control-plane + GPU-execution node; P5 is the storage + CPU-workload node.

Node tier labels (`tier=main` / `tier=dev`) still exist and are consumed by `ray-worker` and by legacy affinity rules, but the branch-per-node model is receding — new workloads use `kubernetes.io/arch` instead. See [workloads/](workloads/) for the specific manifests.

Nodes are seeded with [seed/setup-node.sh](seed/setup-node.sh) and torn down with [seed/teardown-node.sh](seed/teardown-node.sh). The first node seeded is also the k3s control plane and hosts the Argo CD bootstrap.

### Storage routing (P5 HDD)

P5 has a 2TB HDD mounted at `/home/ptrochim/GitHub`. All `PersistentVolumeClaim`s on P5 (MinIO, Postgres, etc.) are provisioned into `/home/ptrochim/GitHub/k3s-storage/` rather than the default `/var/lib/rancher/k3s/storage`, to keep PV data off the root NVMe. The routing is a node-specific entry in the k3s-bundled `local-path-config` ConfigMap, applied idempotently by `setup-node.sh` on every control-plane seed. The stamped path is declarative, not quota-enforced — minio's `1Ti` PVC can still physically fill the whole disk.

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

**Symptom.** Argo sync fails with `Deployment.apps ... is invalid: spec.strategy.rollingUpdate: Forbidden: may not be specified when strategy type is 'Recreate'`. The live Deployment carries a stale `rollingUpdate: {...}` subfield that was default-filled by the API server when it was originally created with `type: RollingUpdate`. No field manager owns it, so ServerSideApply can't delete it — even an explicit `rollingUpdate: null` in the manifest is a no-op, because SSA only removes fields it owns.

**Fix (the simple one we use).** Delete the live Deployment and let Argo recreate it fresh:
```bash
kubectl -n <namespace> delete deployment <name>
```
Argo's next sync creates a new object from scratch with only the fields in our manifest — no stale subfields survive. Brief downtime (seconds), no ongoing cost. This is what we did for `jobs-control-plane`, `cortexflow-ui-*`, `minio`, and `postgres` when adding `strategy: Recreate`.

**Alternative (annotation-based).** `argocd.argoproj.io/sync-options: Replace=true` on the Deployment swaps SSA for `kubectl replace` on every sync — wipes stale fields automatically. Cost: every manifest edit (image tag, env, probe) triggers a full object replace and a pod recreate. Worth it only when the delete-and-recreate dance is too disruptive. See [workloads/ray/deployment.yaml](workloads/ray/deployment.yaml) for an example.

**Related trap: stuck Argo sync op.** After the failing sync exceeds its retry limit, Argo keeps replaying the *same* bad payload on refresh — it doesn't re-plan against current git/cluster state until the operation is terminated. Clear it:
```bash
kubectl -n argocd patch app <name> --type merge -p '{"operation":null}'
kubectl -n argocd annotate app <name> argocd.argoproj.io/refresh=hard --overwrite
```

### Ray worker DaemonSet hardcodes GPU count to 2

**Symptom.** [workloads/ray/worker-daemonset.yaml](workloads/ray/worker-daemonset.yaml) requests `nvidia.com/gpu: 2` and passes `--num-gpus=2` on every dev-tier node. This matches P5 exactly. If a future `tier=dev` node has 1 GPU the pod won't schedule; if it has 4 GPUs the pod will only use 2.

**Why we don't do anything preemptive.** Without KubeRay there's no clean k8s-native way to say "one worker per GPU on each node with variable count." Two realistic fixes when it bites: (a) swap the DaemonSet for a per-node Deployment with explicit replicas/GPU requests, or (b) multiple DaemonSets filtered by an extra label like `gpu-count=2`. Both are fine; neither's worth doing until we actually add a dev node with a different GPU count.
