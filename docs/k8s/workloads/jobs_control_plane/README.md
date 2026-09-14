# Jobs Control Plane — K8s Spec

How the jobs-control-plane workload runs inside Kubernetes. Applied by the Argo Application at [../../argo_deployments/jobs_control_plane/](../../../../k8s/argo_deployments/aws/jobs_control_plane/).

## Deployment shape

- **`kind: Deployment`** — not a `Job` (no finite work), not a `StatefulSet` (stateless, no persistent identity), not a `DaemonSet` (not per-node). A Deployment keeps N pods alive and restarts them when they die.
- **`replicas: 1`** — single poller; horizontal scaling would cause duplicate scheduling.
- **`restartPolicy: Always`** — implicit default on Deployments. If the container exits for any reason, k8s restarts it.
- **`livenessProbe`** — file-based heartbeat (matches the current docker-compose check). If the app stops touching `/tmp/cp_heartbeat` for 30s, k8s kills the container; the Deployment replaces it.
- **`imagePullPolicy: Always`** — on every new pod, k8s re-pulls the image (so `:latest` actually gets the latest build).
- **`strategy: Recreate`** — enforces the singleton. The old pod terminates before the new one starts, so there's never more than one poller. Brief downtime during rollouts is acceptable; duplicate scheduling is not.
- **`revisionHistoryLimit: 1`** — CI bumps the image tag on every build; without this the cluster accumulates dozens of stale ReplicaSets.
- **`nodeSelector: role: head`** — pins to the head node. See [../../README.md](../../README.md) for the cluster-wide placement policy.
- **Env vars** — MLflow URL, Ray URL, poll interval, AWS creds (via `envFrom` secret).
- **`imagePullSecrets: ghcr-pull`** — the pull secret reflected into the namespace.

## Files

- **deployment.yaml** — the `Deployment` described above. The `s3-creds` Secret is materialized by External Secrets and mirrored into this namespace by [reflector](../../../../k8s/argo_deployments/base/secrets/reflector/); every other secret comes from the head secrets server at `CORTEXGRID_HEAD_URL`.
