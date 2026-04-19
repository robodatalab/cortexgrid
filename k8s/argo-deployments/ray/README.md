# Ray — Argo Deployment

## Problem

Training code needs a distributed compute runtime that can place tasks across CPU and GPU resources, expose a dashboard for debugging, publish metrics, and accept jobs from the MLflow-driven control plane. Without a long-running Ray head, every training run would have to boot its own cluster, and there would be no shared GCS for logs or actors.

## Components

**stack.yaml** — Argo Application pointing at [k8s/workloads/ray/](../../workloads/ray/) which contains raw k8s manifests. Deployed into the `ray` namespace. Sync-wave `1`. See [workloads/ray/README.md](../../workloads/ray/README.md) for the k8s spec itself.

## Why raw manifests and not KubeRay (for now)

The recommended production way to run Ray on k8s is the [KubeRay operator](https://github.com/ray-project/kuberay): a `RayCluster` CRD that spawns head and worker pods, autoscales workers, and handles lifecycle. We deliberately chose NOT to use it **yet** because:

- On the DGX today we run a **single-node** Ray — head only, zero workers. All of KubeRay's payoff (worker scaling, multi-node orchestration, autoscaling, failover) is unused.
- KubeRay adds two Helm charts (operator + ray-cluster), a CRD, and the "chart values can't add arbitrary resources" problem — which forced a second Argo Application just for our ExternalSecret + PodMonitor.
- A plain `Deployment` + `Service` does the exact same job on the DGX with much less machinery.

## The AWS migration plan

When we add AWS and want real workers / GPU scaling:

1. Install the KubeRay operator as a sibling Argo Application.
2. Replace [../../workloads/ray/deployment.yaml](../../workloads/ray/deployment.yaml) + [service.yaml](../../workloads/ray/service.yaml) with a `RayCluster` CR — one head group, one or more worker groups with GPU resource requests.
3. The existing [secrets.yaml](../../workloads/ray/secrets.yaml) (env Secret) and [metrics.yaml](../../workloads/ray/metrics.yaml) (PodMonitor) stay put; they reference the cluster by pod label, which we update to match KubeRay's scheme.
4. The image [ghcr.io/paksas/ray-head](../../docker/ray/) stays the same — KubeRay invokes `ray start` itself, which our image supports (ray installed via pip on top of `python:3.11-slim`, no custom entrypoint).

The DGX deployment can either stay on raw manifests forever (head-only doesn't need KubeRay) or migrate to KubeRay if we ever want the DGX to run workers.

## Dependencies

- **Custom image** ([../../docker/ray/](../../docker/ray/)) — `ghcr.io/paksas/ray-head`.
- **Monitoring** ([../monitoring/](../monitoring/)) — the PodMonitor in `workloads/ray/` tells Prometheus to scrape port 8080.
- **External Secrets Operator** ([../secrets/](../secrets/)) — materializes `ray-env` (dashboard's Grafana URL, Prometheus URL) from AWS Secrets Manager.
- **Reflector** ([../secrets/](../secrets/)) — provides the `ghcr-pull` Secret in this namespace so kubelet can pull the private image.
- **Jobs Control Plane** ([../jobs-control-plane/](../jobs-control-plane/)) — submits runs to this Ray head via `RAY_ADDRESS`.
