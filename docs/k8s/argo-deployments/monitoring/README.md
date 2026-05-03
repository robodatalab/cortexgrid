# Monitoring

## Problem

Every workload in the cluster — Ray, MLflow (future), k8s itself, node OS — emits metrics that need to be scraped, stored, queried, and visualized. Without a central stack, operators have no view of GPU utilization, task throughput, node health, or failures, and Ray's built-in dashboards stop working (Ray's UI embeds Grafana panels that rely on an external Prometheus + Grafana).

## Components

**stack.yaml** — Argo Application installing the `kube-prometheus-stack` Helm chart. Bundles Prometheus, Grafana, node-exporter, kube-state-metrics, alertmanager and the Prometheus Operator in one release. Installed into `monitoring` namespace. Exposed via NodePort: Grafana at `:30300`, Prometheus at `:30090`.

Prometheus is configured with `*SelectorNilUsesHelmValues: false` so it discovers `ServiceMonitor` / `PodMonitor` resources cluster-wide, not only ones labeled for this release. Workloads elsewhere (Ray, MLflow) drop their own monitors and Prometheus picks them up automatically.

All chart components (Prometheus, Alertmanager, Grafana, Operator, kube-state-metrics, node-exporter) are pinned to the head node via `nodeSelector: role: head` in the values override. Workers don't run `node-exporter` — we accept the loss of per-worker host metrics to avoid the restart cycles we used to see on flaky nodes.

## Dependencies

- **Ray** ([../ray/](../../../../k8s/argo-deployments/ray/)) — Ray head pod exposes Prometheus metrics on port 8080. [../ray/metrics.yaml](../../../../k8s/argo-deployments/ray/metrics.yaml) declares the PodMonitor that causes Prometheus to scrape it.
- **CRDs** — `ServiceMonitor` / `PodMonitor` / `PrometheusRule` come from this chart. Any sibling deployment using them must annotate its resources with `argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true` to survive the initial install ordering.
- **Grafana UI reachable from Mac** — head node's Tailscale IP + NodePort `:30300`. Default login: admin/admin, anonymous viewer enabled.
