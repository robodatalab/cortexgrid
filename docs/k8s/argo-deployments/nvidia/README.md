# NVIDIA Device Plugin

## Problem

Ray (and future GPU workloads) need to request GPUs from Kubernetes just like they request CPU or memory. But k8s doesn't know about GPUs natively — it has no concept of "one NVIDIA H100" as a schedulable resource. Without this plugin, `resources.limits.nvidia.com/gpu: 1` in a pod spec is meaningless and the pod schedules as CPU-only, even on a node with working GPUs.

## Components

**stack.yaml** — Argo Application installing NVIDIA's official `nvidia-device-plugin` Helm chart as a DaemonSet into the `nvidia-device-plugin` namespace. The DaemonSet runs on every node, queries local GPUs via the NVIDIA driver, and advertises them to the kubelet as `nvidia.com/gpu` resources. Uses the `nvidia` runtime class so containers that request GPUs are launched via `nvidia-container-runtime` (which auto-mounts driver libraries + `nvidia-smi` into the container).

The chart's default `affinity` requires NFD (Node Feature Discovery) labels like `feature.node.kubernetes.io/pci-10de.present` to find GPU nodes. k3s doesn't ship NFD, so every node looks GPU-less and the DaemonSet schedules zero pods. We override `affinity` with a permissive `kubernetes.io/os: linux` match so the plugin runs on every Linux node — fine on a single-node DGX; revisit if we ever mix GPU and non-GPU nodes. (Passing `affinity: {}` doesn't work — Helm deep-merges the empty map into the default rather than replacing it.)

## Dependencies

- **k3s configured with NVIDIA container runtime** — k3s auto-detects `nvidia-container-runtime` at install time if the host has `nvidia-container-toolkit`. No extra config needed on DGX (it was there for docker-compose). Verify with `kubectl get runtimeclass nvidia`.
- **Ray** ([../ray/](../../../../k8s/argo-deployments/aws/ray/)) — requests `nvidia.com/gpu` in its Deployment, which wouldn't schedule without this plugin.
- **Future GPU workloads** — any pod doing CUDA work adds `resources.limits.nvidia.com/gpu: N` to get scheduled onto GPU resources.
