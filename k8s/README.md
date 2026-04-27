# k8s

GitOps manifests watched by Argo CD. See [argo-deployments/](argo-deployments/) for the actual deployment specs. [argocd.yaml](argocd.yaml) is the one-shot bootstrap applied at seed time and is not watched by Argo.

## Topology

The platform is a hybrid: a single k3s **head** plus zero or more **GPU workers**. The head can run on AWS EC2 or on a local box; workers can be the DGX, a ThinkStation, or any GPU node reachable over Tailscale. The same manifests apply in both cases - placement is decided by node labels, not by environment.

| Role | Label | What lands there | Typical box |
|------|-------|------------------|-------------|
| `head` | `role=head` | k3s control plane, Argo CD, mlflow, cortexflow-ui-backend, jobs-control-plane, prometheus stack, ray-head | AWS EC2 (`head-aws-apply` + `head-setup`) **or** on-prem ThinkStation/DGX (`head-setup` only) |
| `worker` | `role=worker` | ray-worker DaemonSet (1 Pod per node, requests 1 GPU) | DGX, ThinkStation, GPU EC2 - anything joined via `worker-setup` |

Cluster topology — which IP is head vs worker, where the head's HDD is mounted — lives in [infra-config.yaml](../infra-config.yaml) at the repo root. That file is written by the seed scripts and read by every infra script. Roles are applied as node labels (`role=head`, `role=worker`) at seed time; manifests reference those labels and stay agnostic of specific IPs.

### Profiles

`K3sServer` ([k8s/seed/operators/k3s_server.py](seed/operators/k3s_server.py)) auto-detects the profile at seed time from `/sys/class/dmi/id/sys_vendor`:

- **`aws`** — vendor reports `Amazon EC2`. Argo's `onprem/**` exclude stays in place; in-cluster MinIO and Postgres are not deployed. mlflow uses RDS + S3, written to AWS Secrets Manager by `terraform/platform/{rds,s3}`.
- **`onprem`** — anything else. K3sServer drops the `onprem/**` exclude, so [argo-deployments/onprem/](argo-deployments/onprem/) (Postgres, MinIO, the `s3-endpoint-override` Secret) syncs. `PlatformConfig` ([k8s/seed/operators/platform_config.py](seed/operators/platform_config.py)) writes MinIO admin creds + in-cluster Postgres URI to AWS Secrets Manager so workload manifests stay profile-agnostic.

Workload manifests (mlflow, ray, cortexflow-ui-backend, jobs-control-plane) reference the same Secret names in both profiles; only the Secret *contents* differ. See the root [README.md](../README.md#service-discovery) for the full SM key matrix.

### Seeding

Nodes are seeded and torn down via the repo-root Makefile:

```bash
# AWS head (provision EC2 + VPC + S3 + RDS, then bootstrap k3s on it)
make head-aws-apply
make head-setup IP=<robolab-head-tailscale-ip> STORAGE_PATH=/storage

# On-prem head (skip terraform; just bootstrap k3s on an existing box)
make head-setup IP=<head-ip> STORAGE_PATH=<hdd-mount> [SSH_USER=<user>]

# Worker (any GPU box, AWS or on-prem)
make worker-setup IP=<worker-ip> [SSH_USER=<user>]

# Teardown
make node-teardown IP=<any-ip> [SSH_USER=<user>]
make head-aws-destroy   # AWS only - destroys EC2 + VPC + S3 + RDS
```

Worker-before-head is supported: if the head hasn't been seeded yet, `make worker-setup` installs node prerequisites and drops a systemd timer on the worker that polls AWS Secrets Manager for the head's credentials and joins automatically once the head appears. The command returns immediately.

### Storage routing (head HDD)

`STORAGE_PATH` passed to `head-setup` becomes the local-path-provisioner directory on the head node. Any in-cluster PVC (Postgres, MinIO, Prometheus, Grafana, Alertmanager) lands there instead of the default `/var/lib/rancher/k3s/storage`, keeping PV data off the root volume. The routing is a node-specific entry in the k3s-bundled `local-path-config` ConfigMap, patched idempotently by [k8s/seed/operators/local_path.py](seed/operators/local_path.py) using the value from `infra-config.yaml`. The stamped path is declarative, not quota-enforced.

In the AWS profile, mlflow's backend store and artifact store are RDS + S3 - no PVC. Only the prometheus stack uses PVCs there. In the on-prem profile, MinIO and Postgres also live on this volume.

## Features

### Ray topology

Ray is split into a CPU-only control plane on the head and a GPU-bearing worker DaemonSet on every joined GPU node. Both shapes are in [workloads/ray/deployment.yaml](workloads/ray/deployment.yaml).

| | nodeSelector | GPU | Replicas |
|------|--------------|-----|----------|
| `ray-head` | `role=head` | none (no `nvidia.com/gpu` request, no `--num-gpus`, no `runtimeClassName`) | 1 (Deployment) |
| `ray-worker` | `role=worker` | 1 (`nvidia.com/gpu: 1`, `runtimeClassName: nvidia`) | 1 per worker node (DaemonSet) |

Workers register with the head's GCS via the in-cluster Service at `ray-head.ray.svc.cluster.local:6379`. Ray pools every worker's GPU into a single scheduler - a job asking for 1 GPU lands on any worker, a job asking for more parallelises across them. No code change at the cortexflow submission site.

**To run ray on AWS:** join GPU EC2 instances via `worker-setup`. ray-head stays on the AWS EC2 head; ray-worker DaemonSet lights up one Pod per GPU EC2.

**To run ray on-prem:** join the DGX (or any GPU box on Tailscale) via `worker-setup`. The head can be either AWS EC2 or another on-prem box - ray-head only needs CPU and reaches workers through the cluster's Tailscale-routed overlay network.

**To run ray fully on-prem:** seed the head on an on-prem box (`head-setup` only, no `head-aws-apply`), then join GPU workers. The auto-detected `onprem` profile brings in MinIO + Postgres so mlflow has somewhere to store metadata and artifacts.

**Adding/removing GPU capacity at runtime:** `worker-setup IP=<new-gpu-box>` or `node-teardown IP=<old-gpu-box>` - DaemonSet self-adjusts; ray-head's GCS picks up the new worker (or notices the missing one) on the next heartbeat.

## Future extensions

### Branch dev-environments

**Problem.** Changes to `robolab-infra` shouldn't block downstream repositories consuming its `main` branch. Users of the `cortexflow` library in separate experiment repos need a stable MLflow, Ray, and S3 always reachable. We want to iterate on an infra branch end-to-end without pushing to `main` first.

**Shape.** One `ApplicationSet` per component we want replicated per PR, using Argo's `pullRequest` generator to emit one Application per open PR, sourced from that branch, into a namespace like `dev-pr-<N>`. CI already tags images `<branch-slug>-<sha>`; per-Application Image Updater regexes match only the right branch's tags. Not every component would get a per-branch copy — stateful/GPU-bound ones (MLflow, Ray) stay on `main` and are consumed cross-namespace; only actively-iterated workloads (cortexflow-ui, jobs-control-plane) get per-branch copies.

### Multi-cluster routing

Today the platform runs on a single cluster at a time (either AWS-headed or on-prem-headed). When per-PR dev clusters land alongside a stable main cluster, ApplicationSet templates can parameterise `destination.server` so PR Applications target the dev cluster while main Applications target the production cluster - same manifest shape, routed by destination. Same mechanism would let an AWS production cluster coexist with an on-prem development cluster.

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

**Symptom.** [workloads/ray/deployment.yaml](workloads/ray/deployment.yaml) requests `nvidia.com/gpu: 1` and passes `--num-gpus=1` on every worker. One GPU per node is the common denominator that fits the DGX Spark and most single-GPU boxes. If a future node has more GPUs, this DaemonSet only uses one of them.

**Why we don't do anything preemptive.** Without KubeRay there's no clean k8s-native way to say "one worker per GPU on each node with variable count." Two realistic fixes when it bites: (a) swap the DaemonSet for a per-node Deployment with explicit replicas/GPU requests, or (b) multiple DaemonSets filtered by an extra label like `gpu-count=N`. Neither's worth doing until we actually add a node with a different GPU count that we want to exploit fully.
