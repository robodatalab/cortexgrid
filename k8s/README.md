# k8s

GitOps manifests watched by Argo CD. See [argo-deployments/](argo-deployments/) for the actual deployment specs. [argocd.yaml](argocd.yaml) is the one-shot bootstrap applied at seed time and is not watched by Argo.

## Workload placement

**Current policy:** every first-party workload pins to the head node (`nodeSelector: role: head`). The only exception is `ray-worker`, which runs as a DaemonSet on every node so Ray can dispatch GPU jobs anywhere. The head hosts the k3s control plane, storage, and all CPU workloads; workers are there for extra GPU capacity only.

Cluster topology — which IP is head vs worker, where the head's HDD is mounted — lives in [infra-config.yaml](../infra-config.yaml) at the repo root. That file is written by `setup-node` and read by every infra script. Roles are applied as node labels (`role=head`, `role=worker`) at seed time; manifests reference those labels and stay agnostic of specific IPs.

Nodes are seeded and torn down via the repo-root Makefile:

```bash
make setup-head   IP=<head-ip>   STORAGE_PATH=<hdd-mount>   [SSH_USER=<user>]
make setup-worker IP=<worker-ip>                            [SSH_USER=<user>]
make teardown-node IP=<any-ip>                              [SSH_USER=<user>]
```

Worker-before-head is supported: if the head hasn't been seeded yet, `make setup-worker` installs node prerequisites and drops a systemd timer on the worker that polls AWS Secrets Manager for the head's credentials and joins automatically once the head appears. Command returns immediately.

### Storage routing (head HDD)

The head node has an HDD mounted at whatever path you pass as `STORAGE_PATH` to `make setup-head`. All `PersistentVolumeClaim`s (MinIO, Postgres, etc.) land there instead of the default `/var/lib/rancher/k3s/storage`, keeping PV data off the root NVMe. The routing is a node-specific entry in the k3s-bundled `local-path-config` ConfigMap, patched idempotently by `setup-node.py` using the value from `infra-config.yaml`. The stamped path is declarative, not quota-enforced — minio's `1Ti` PVC can still physically fill the whole disk.

## Features

### Multi-node Ray

`ray-head` is pinned to `role=head` via `nodeSelector`. A `ray-worker` DaemonSet runs on every node (head + workers), registering to the head via the in-cluster Service at `ray-head.ray.svc.cluster.local:6379`. Each `ray-worker` pod requests 1 GPU; Ray pools them all into one scheduler — a job asking for 1 GPU can land on any node, a job asking for more parallelises across nodes. No code change at the cortexflow submission site; Ray handles GPU assignment transparently. See [workloads/ray/](workloads/ray/).

## Future extensions

### Branch dev-environments

**Problem.** Changes to `robolab-infra` shouldn't block downstream repositories consuming its `main` branch. Users of the `cortexflow` library in separate experiment repos need a stable MLflow, Ray, and S3 always reachable. We want to iterate on an infra branch end-to-end without pushing to `main` first.

**Shape.** One `ApplicationSet` per component we want replicated per PR, using Argo's `pullRequest` generator to emit one Application per open PR, sourced from that branch, into a namespace like `dev-pr-<N>`. CI already tags images `<branch-slug>-<sha>`; per-Application Image Updater regexes match only the right branch's tags. Not every component would get a per-branch copy — stateful/GPU-bound ones (MLflow, Ray) stay on `main` and are consumed cross-namespace; only actively-iterated workloads (cortexflow-ui, jobs-control-plane) get per-branch copies.

### AWS migration

When `main` migrates to AWS, on-prem stays as the dev cluster, production on AWS. ApplicationSet templates parameterise `destination.server` so PR Applications land on the dev cluster while main Applications land on AWS — same manifest shape, routed by destination.

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

### Ray worker DaemonSet hardcodes GPU count to 1

**Symptom.** [workloads/ray/deployment.yaml](workloads/ray/deployment.yaml) requests `nvidia.com/gpu: 1` and passes `--num-gpus=1` on every node. One GPU per node is the common denominator that fits everywhere (head shares its GPUs with `ray-head`). If a future node has more GPUs, this DaemonSet only uses one of them.

**Why we don't do anything preemptive.** Without KubeRay there's no clean k8s-native way to say "one worker per GPU on each node with variable count." Two realistic fixes when it bites: (a) swap the DaemonSet for a per-node Deployment with explicit replicas/GPU requests, or (b) multiple DaemonSets filtered by an extra label like `gpu-count=N`. Neither's worth doing until we actually add a node with a different GPU count that we want to exploit fully.
