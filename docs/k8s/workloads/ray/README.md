# Ray — K8s Spec

How the Ray head runs inside Kubernetes. Applied by the Argo Application at [../../argo_deployments/ray/](../../../../k8s/argo_deployments/aws/ray/). See that folder's README for the higher-level "why raw manifests and not KubeRay" rationale.

## Deployment shape

- **`kind: Deployment`** — single pod running Ray's head process. Not a `StatefulSet` (stateless — GCS is in-memory for now, workers connect fresh), not a `DaemonSet` (one head, not per-node).
- **`replicas: 1`** — only one head. Horizontal scaling of the head isn't meaningful in Ray; workers would be separate Deployments with different `command` args if we ever add them here.
- **`command: ray start --head --dashboard-host=0.0.0.0 --metrics-export-port=8080 --num-gpus=1 --block`** — invoked directly. `--block` keeps the process in the foreground so k8s sees the container running. `--num-gpus=1` declares the head as having 1 GPU, matching the pod's resource request.
- **`runtimeClassName: nvidia`** — runs the container via `nvidia-container-runtime` so the NVIDIA driver libraries + `nvidia-smi` are mounted in. Requires the [NVIDIA device plugin](../../../../k8s/argo_deployments/aws/nvidia/).
- **`resources.limits.nvidia.com/gpu: "1"`** — requests one GPU. The kubelet only schedules this pod onto a node that has a free GPU advertised by the device plugin.
- **`envFrom: ray-env`** — injects `RAY_GRAFANA_IFRAME_HOST`, `RAY_GRAFANA_HOST`, `RAY_PROMETHEUS_HOST` from the Secret materialized by [secrets.yaml](../../../../k8s/workloads/ray/secrets.yaml). The dashboard uses these to embed Grafana panels and query Prometheus.
- **`imagePullSecrets: ghcr-pull`** — reflected into the namespace by reflector, allows pulling the private image.
- **`imagePullPolicy: Always`** — every new pod re-pulls `:latest` so CI builds take effect.
- **`livenessProbe: ray status`** — if `ray status` fails for 30s, k8s kills the pod; the Deployment replaces it.
- **Workers via DaemonSets** — one pod per `worker=true` node: `ray-worker` (1 GPU) on `gpu=true` nodes, `ray-worker-cpu` (`--num-gpus=0`) elsewhere. All pods register against `ray-head.cortexgrid.svc.cluster.local:6379` and share one resource pool.

## Files

- **deployment.yaml** — the `Deployment` described above.
- **service.yaml** — `NodePort` Service exposing dashboard (`:30265`), Ray client (`:30001`), GCS (internal only), metrics (internal only, scraped via PodMonitor).
- **secrets.yaml** — `ExternalSecret` that templates a Secret containing the three `RAY_*` env vars. Pulls `CONTROL_PLANE_TAILSCALE_IP` from the head secrets store and composes URLs from it.
- **metrics.yaml** — `PodMonitor` telling Prometheus to scrape pods with label `app: ray-head` on port `metrics` (8080) every 15s.

## Port map

| Port | Purpose | Exposure |
|------|---------|----------|
| 8265 | Dashboard | NodePort 30265 (Tailscale-reachable) |
| 10001 | Ray client | NodePort 30001 (Tailscale-reachable) |
| 6379 | Ray GCS | in-cluster only |
| 8080 | Prometheus metrics | in-cluster only (PodMonitor scrape) |
