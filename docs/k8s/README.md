# k8s

GitOps manifests watched by Argo CD. See [argo_deployments/](../../k8s/argo_deployments/) for the actual deployment specs. [argocd.yaml](../../k8s/argocd.yaml) is the one-shot bootstrap applied at seed time and is not watched by Argo.

## Topology

The platform is a hybrid: a single k3s **head** plus zero or more **workers**. The head can run on AWS EC2 or on a local box; workers can be the DGX, a ThinkStation, or any node (GPU or CPU-only) reachable over Tailscale. The same manifests apply in both cases - placement is decided by node labels, not by environment.

| Role | Label | What lands there | Typical box |
|------|-------|------------------|-------------|
| `head` | `role=head` | k3s control plane, Argo CD, mlflow, cortexgrid-ui-backend, jobs-control-plane, prometheus stack, ray-head | AWS EC2 (`head-aws-apply` + `head-setup`) **or** on-prem ThinkStation/DGX (`head-setup` only) |
| `worker` | `role=worker`, `worker=true`, `gpu=true` (GPU hosts only) | `ray-worker` (GPU, requests 1 GPU) or `ray-worker-cpu` DaemonSet, 1 Pod per node | DGX, ThinkStation, EC2 - anything joined via `worker-setup` |

Cluster topology — which IP is head vs worker, where the head's HDD is mounted — lives in [infra-config.yaml](../../infra-config.yaml) at the repo root. That file is written by the seed scripts and read by every infra script. Roles are applied as node labels (`role=head`, `role=worker`) at seed time; manifests reference those labels and stay agnostic of specific IPs. Ray worker placement uses separate compute labels: `worker=true`, plus `gpu=true` when `nvidia-smi -L` on the host lists a GPU (`ComputeLabels`).

### Profiles

`K3sServer` ([k8s/seed/operators/k3s_server.py](../../k8s/seed/operators/k3s_server.py)) auto-detects the profile at seed time from `/sys/class/dmi/id/sys_vendor`:

Both profiles render via kustomize from a shared [argo_deployments/base/](../../k8s/argo_deployments/base/) plus a per-profile overlay that supplies a profile-local `loki.yaml`, the profile's `secrets/` ExternalSecret folder, and a patch on the secrets Application's `path:`.

- **`aws`** — vendor reports `Amazon EC2`. K3sServer keeps `argo-bootstrap-aws`, which syncs [argo_deployments/aws/](../../k8s/argo_deployments/aws/). In-cluster MinIO and Postgres are not deployed; mlflow uses RDS + S3, whose coordinates the [`TerraformOutputs`](../../k8s/seed/operators/terraform_outputs.py) seed operator publishes to the head secrets store from `terraform/platform` outputs.
- **`onprem`** — anything else. K3sServer keeps `argo-bootstrap-onprem`, which syncs [argo_deployments/onprem/](../../k8s/argo_deployments/onprem/) (`base/` plus the on-prem `loki.yaml` and `secrets/` overlay; on-prem-only MinIO + Postgres Apps would also live here when added). The [`MinioCredentials`](../../k8s/seed/operators/minio_credentials.py) and [`PostgresCredentials`](../../k8s/seed/operators/postgres_credentials.py) seed operators write profile-specific service-discovery into the head secrets store — endpoints (head tailscale IP + NodePort), bucket name, and credentials — so workload manifests stay profile-agnostic.

Workload manifests (mlflow, ray, cortexgrid-ui-backend, jobs-control-plane) reference the same Secret names in both profiles; only the Secret *contents* differ. See the root [README.md](../README.md#service-discovery) for the full key matrix.

### Argo Application hierarchy

Within each profile the manifests are a three-tier App-of-Apps that mirrors the dependency direction (bootstrap → cortexgrid → ui):

```
argo-bootstrap-<profile>           bootstrap (defined in argocd.yaml; no upstream deps)
├── secrets/                       ClusterSecretStore + ExternalSecrets
├── cert-manager                   leaf
├── tailscale-operator             leaf
├── nvidia                         leaf
├── monitoring                     leaf (kube-prometheus-stack)
└── ui                             top aggregator (fires cortexgrid-ui integration tests)
    ├── cortexgrid-ui              leaf
    └── cortexgrid                 mid aggregator (fires cortexgrid library integration tests)
        ├── jobs-control-plane     leaf
        ├── mlflow                 leaf
        ├── mlflow-monitoring      leaf (Grafana dashboards for mlflow)
        ├── ray                    leaf
        ├── minio                  leaf (onprem only)
        └── postgres               leaf (onprem only)
```

The bootstrap App's `directory.exclude: 'ui/**'` keeps it from claiming anything below `ui/`, so the two aggregators own their subtrees exclusively. Bootstrap directly owns everything else: `secrets/`, the cluster-infra leaves (cert-manager, tailscale-operator, nvidia, monitoring), and `ui.yaml` itself. App-specific observability (e.g. `mlflow-monitoring`'s Grafana dashboards) lives next to the App it observes, inside the relevant aggregator — bootstrap owns only the *platform* (Prometheus, Grafana, etc.), not per-app dashboards.

**Why secrets/ must stay bootstrap-owned.** ArgoCD reads its source repo using the `argo-github-repo` Secret, which is produced by an `ExternalSecret` backed by the `ClusterSecretStore` from `secrets/external-secrets/`. If those resources live downstream of an aggregator, ArgoCD enters a chicken-and-egg state on any sync that prunes them — the aggregator can't reload its source until git auth is restored, and git auth comes from what the aggregator was supposed to manage. Bootstrap-owning `secrets/` keeps the trust chain rooted at a layer that doesn't depend on git working. Recovery from accidentally nesting it requires re-running `make head-setup` so [BootstrapSecrets](../../k8s/seed/operators/bootstrap_secrets.py) can re-seed `argo-github-repo` directly via `kubectl apply`.

**Test triggers.** Each aggregator subscribes to a distinct ArgoCD notification trigger (`on-cortexgrid-stack-deployed`, `on-cortexgrid-ui-stack-deployed`) declared in [argocd.yaml](../../k8s/argocd.yaml) and uses `oncePer: app.status.sync.revision` so it fires exactly once per main commit, only after every child is Synced + Healthy. The corresponding GitHub workflows under [.github/workflows/](../../.github/workflows/) listen for the matching `repository_dispatch` event types — no polling, no per-commit dedup logic on the GH side.

### Seeding

Nodes are seeded and torn down via the repo-root Makefile:

```bash
# AWS head (provision EC2 + VPC + S3 + RDS, then bootstrap k3s on it)
make head-aws-apply
make head-setup IP=<robolab-head-tailscale-ip> STORAGE_PATH=/storage

# On-prem head (skip terraform; just bootstrap k3s on an existing box)
make head-setup IP=<head-ip> STORAGE_PATH=<hdd-mount> [SSH_USER=<user>]

# Worker (any box, GPU or CPU-only, AWS or on-prem)
make worker-setup IP=<worker-ip> [SSH_USER=<user>]

# Head as a worker too (runs Ray workers next to the control plane)
make worker-setup IP=<head-ip> [SSH_USER=<user>]

# Teardown
make node-teardown IP=<any-ip> [SSH_USER=<user>]
make worker-teardown IP=<head-ip> [SSH_USER=<user>]   # removes only the worker role; on a plain worker, same as node-teardown
make head-aws-destroy   # AWS only - destroys EC2 + VPC + S3 + RDS
```

`worker-setup` on the head's IP does not join or relabel it: it only runs `ComputeLabels` (`worker=true`, plus `gpu=true` on a GPU host) and records `worker: true` on the head's `infra-config.yaml` entry, which `head-setup` re-runs, `node-teardown` and `restart` honour.

Worker-before-head is supported: if the head hasn't been seeded yet, `make worker-setup` installs node prerequisites and drops a systemd timer on the worker that polls the head secrets server (`http://robolab-head:7700`) for the head's credentials and joins automatically once the head appears. The command returns immediately.

### Storage routing (head HDD)

`STORAGE_PATH` passed to `head-setup` becomes the local-path-provisioner directory on the head node. Any in-cluster PVC (Postgres, MinIO, Prometheus, Grafana, Alertmanager) lands there instead of the default `/var/lib/rancher/k3s/storage`, keeping PV data off the root volume. The routing is a node-specific entry in the k3s-bundled `local-path-config` ConfigMap, patched idempotently by [k8s/seed/operators/local_path.py](../../k8s/seed/operators/local_path.py) using the value from `infra-config.yaml`. The stamped path is declarative, not quota-enforced.

In the AWS profile, mlflow's backend store and artifact store are RDS + S3 - no PVC. Only the prometheus stack uses PVCs there. In the on-prem profile, MinIO and Postgres also live on this volume.

## Features

### Ray topology

Ray is split into a CPU-only control plane on the head and worker DaemonSets on every `worker=true` node: the GPU flavour on `gpu=true` nodes, the CPU-only flavour elsewhere. All shapes are in [charts/cortexgrid/templates/ray/](../../k8s/charts/cortexgrid/templates/ray/).

| | nodeSelector | GPU | Replicas |
|------|--------------|-----|----------|
| `ray-head` | `role=head` | none (no `nvidia.com/gpu` request, no `--num-gpus`, no `runtimeClassName`) | 1 (Deployment) |
| `ray-worker` | `worker=true`, `gpu=true` | 1 (`nvidia.com/gpu: 1`, `--num-gpus=1`, `runtimeClassName: nvidia`) | 1 per GPU worker node (DaemonSet) |
| `ray-worker-cpu` | `worker=true`, no `gpu` label | none (`--num-gpus=0`) | 1 per CPU-only worker node (DaemonSet) |

Each GPU worker also advertises its GPU memory as the custom Ray resource `vram_mib`, read from `nvidia-smi` at startup, so a model's `vram_gb` requirement can place it (see [model-serving](../cortexgrid/model-serving.md#how-ray-places-a-replica)). A GPU with unified memory (the DGX Spark's GB10) reports no size of its own, so that worker advertises the host's RAM instead. The same number is also set as a node **label** of the same name: the resource says how much of the card is left, the label says how big it is, and the label is what lets a model be placed on the [smallest GPU that fits](../cortexgrid/model-serving.md#which-gpu-it-picks-when-several-fit) rather than any GPU that fits.

Workers register with the head's GCS via the in-cluster Service at `ray-head.ray.svc.cluster.local:6379`. Ray pools every worker's GPU into a single scheduler - a job asking for 1 GPU lands on any worker, a job asking for more parallelises across them. No code change at the cortexgrid submission site.

**To run ray on AWS:** join GPU EC2 instances via `worker-setup`. ray-head stays on the AWS EC2 head; ray-worker DaemonSet lights up one Pod per GPU EC2.

**To run ray on-prem:** join the DGX (or any GPU box on Tailscale) via `worker-setup`. The head can be either AWS EC2 or another on-prem box - ray-head only needs CPU and reaches workers through the cluster's Tailscale-routed overlay network.

**To run ray fully on-prem:** seed the head on an on-prem box (`head-setup` only, no `head-aws-apply`), then join GPU workers. The auto-detected `onprem` profile brings in MinIO + Postgres so mlflow has somewhere to store metadata and artifacts.

**Adding/removing capacity at runtime:** `worker-setup IP=<new-box>` or `node-teardown IP=<old-box>` - the DaemonSets self-adjust; ray-head's GCS picks up the new worker (or notices the missing one) on the next heartbeat.

## Future extensions

### Branch dev-environments

**Problem.** Changes to `cortexgrid` shouldn't block downstream repositories consuming its `main` branch. Users of the `cortexgrid` library in separate experiment repos need a stable MLflow, Ray, and S3 always reachable. We want to iterate on an infra branch end-to-end without pushing to `main` first.

**Shape.** One `ApplicationSet` per component we want replicated per PR, using Argo's `pullRequest` generator to emit one Application per open PR, sourced from that branch, into a namespace like `dev-pr-<N>`. CI already tags images `<branch-slug>-<sha>`; per-Application Image Updater regexes match only the right branch's tags. Not every component would get a per-branch copy — stateful/GPU-bound ones (MLflow, Ray) stay on `main` and are consumed cross-namespace; only actively-iterated workloads (cortexgrid-ui, jobs-control-plane) get per-branch copies.

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
Argo's next sync creates a new object from scratch with only the fields in our manifest — no stale subfields survive. Brief downtime (seconds), no ongoing cost. This is what we did for `jobs-control-plane`, `cortexgrid-ui-*`, `minio`, and `postgres` when adding `strategy: Recreate`.

**Alternative (annotation-based).** `argocd.argoproj.io/sync-options: Replace=true` on the Deployment swaps SSA for `kubectl replace` on every sync — wipes stale fields automatically. Cost: every manifest edit (image tag, env, probe) triggers a full object replace and a pod recreate. Worth it only when the delete-and-recreate dance is too disruptive. See [workloads/ray/deployment.yaml](../../k8s/workloads/ray/deployment.yaml) for an example.

**Related trap: stuck Argo sync op.** After the failing sync exceeds its retry limit, Argo keeps replaying the *same* bad payload on refresh — it doesn't re-plan against current git/cluster state until the operation is terminated. Clear it:
```bash
kubectl -n argocd patch app <name> --type merge -p '{"operation":null}'
kubectl -n argocd annotate app <name> argocd.argoproj.io/refresh=hard --overwrite
```

### Ray worker DaemonSet hardcodes GPU count to 1

**Symptom.** [workloads/ray/deployment.yaml](../../k8s/workloads/ray/deployment.yaml) requests `nvidia.com/gpu: 1` and passes `--num-gpus=1` on every worker. One GPU per node is the common denominator that fits the DGX Spark and most single-GPU boxes. If a future node has more GPUs, this DaemonSet only uses one of them.

**Why we don't do anything preemptive.** Without KubeRay there's no clean k8s-native way to say "one worker per GPU on each node with variable count." Two realistic fixes when it bites: (a) swap the DaemonSet for a per-node Deployment with explicit replicas/GPU requests, or (b) multiple DaemonSets filtered by an extra label like `gpu-count=N`. Neither's worth doing until we actually add a node with a different GPU count that we want to exploit fully.
