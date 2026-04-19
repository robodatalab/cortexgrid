# Jobs Control Plane — K8s Spec

How the jobs-control-plane workload runs inside Kubernetes. Applied by the Argo Application at [../../argo-deployments/jobs-control-plane/](../../argo-deployments/jobs-control-plane/).

## Deployment shape

- **`kind: Deployment`** — not a `Job` (no finite work), not a `StatefulSet` (stateless, no persistent identity), not a `DaemonSet` (not per-node). A Deployment keeps N pods alive and restarts them when they die.
- **`replicas: 1`** — single poller; horizontal scaling would cause duplicate scheduling.
- **`restartPolicy: Always`** — implicit default on Deployments. If the container exits for any reason, k8s restarts it.
- **`livenessProbe`** — file-based heartbeat (matches the current docker-compose check). If the app stops touching `/tmp/cp_heartbeat` for 30s, k8s kills the container; the Deployment replaces it.
- **`imagePullPolicy: Always`** — on every new pod, k8s re-pulls the image (so `:latest` actually gets the latest build).
- **`strategy: RollingUpdate`** (default) — when the Deployment spec changes (new image), k8s starts the new pod before killing the old one, giving a graceful handoff.
- **Env vars** — MLflow URL, Ray URL, poll interval, AWS creds (via `envFrom` secret).
- **`imagePullSecrets: ghcr-pull`** — the pull secret reflected into the namespace.

## Files

- **deployment.yaml** — the `Deployment` described above.
- **secrets.yaml** — `ExternalSecret` that materializes the `aws-creds` Secret in this namespace by pulling `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` from AWS Secrets Manager at `robolab/infra/*`. The Deployment consumes these via `envFrom`.
