# Troubleshooting

## Health Check

Always start here:
```bash
make health
```

This checks connectivity to all services and reports which are unreachable.

## Common Issues

### Cannot connect to Ray Dashboard

**Symptoms:** `make health` shows Ray Dashboard as unreachable. `cortexflow.init()` fails with connection error.

**Causes & fixes:**
1. **Stack not running:** `docker compose ps` on the DGX. If services are down, run `make up`.
2. **Tailscale not connected:** Run `tailscale status` on both Mac and DGX. Ensure both are on the same network.
3. **Stale `DGX_TAILSCALE_IP` in CMS:** Verify the value in AWS Secrets Manager under `robolab/infra/DGX_TAILSCALE_IP` matches `tailscale ip -4` on the DGX. Update it in the UI if it changed.
4. **Ray head crashed:** `docker compose logs ray-head` — look for OOM or GPU errors. Restart with `docker compose restart ray-head`.

### GPU not detected by Ray

**Symptoms:** Ray Dashboard shows 0 GPUs. Jobs requesting GPUs stay pending.

**Fixes:**
1. Verify GPU visibility: `docker compose exec ray-head nvidia-smi`
2. Check NVIDIA Container Toolkit: `docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi`
3. Ensure `deploy.resources.reservations.devices` is set in `docker-compose.yml` (it is by default).

### MLflow not logging metrics

**Symptoms:** Jobs complete but no runs appear in MLflow UI.

**Fixes:**
1. Check MLflow is running: `docker compose logs mlflow`
2. Verify the job has `MLFLOW_TRACKING_URI` set: `cortexflow.remote` injects it automatically.
3. Check PostgreSQL: `docker compose logs postgres`
4. Test manually: `curl http://<DGX_IP>:5000/api/2.0/mlflow/experiments/list`

### MinIO bucket errors

**Symptoms:** MLflow artifact uploads fail with "bucket does not exist".

**Fixes:**
1. Check `minio-init` ran successfully: `docker compose logs minio-init`
2. Manually create buckets:
   ```bash
   docker compose run --rm minio-init
   ```
3. Check MinIO is healthy: `curl http://<DGX_IP>:9000/minio/health/live`

### Job stuck in PENDING

**Symptoms:** Job shows as PENDING in Ray Dashboard but never starts.

**Causes:**
1. **Insufficient resources:** Job requests more GPUs than available. Check the Ray Dashboard.
2. **All workers busy:** Wait for running jobs to complete or add more nodes.
3. **Runtime env install:** Ray is installing pip packages. Check job logs in the Dashboard.

### Checkpoint resume not working

**Symptoms:** Resumed job starts from epoch 0 instead of last checkpoint.

**Fixes:**
1. Verify checkpoints exist in MLflow: open the run in MLflow UI, check Artifacts tab.
2. Ensure the correct `--run-id` is passed.
3. Check MinIO connectivity from the Ray worker: the worker needs access to `MLFLOW_S3_ENDPOINT_URL`.

### Out of memory (OOM)

**Symptoms:** Job killed with OOM error.

**Fixes:**
1. Reduce batch size in your training config.
2. Enable gradient checkpointing in your model.
3. Use `ray.train.torch.prepare_model()` which handles memory optimization.
4. Monitor GPU memory in Grafana: `http://<DGX_IP>:3000`.

## Logs

View logs for specific services:
```bash
docker compose logs ray-head      # Ray head node
docker compose logs mlflow         # MLflow server
docker compose logs postgres       # PostgreSQL
docker compose logs minio          # MinIO
docker compose logs redis          # Redis
docker compose logs prometheus     # Prometheus
docker compose logs grafana        # Grafana
```

Add `-f` to follow, `--tail=100` to limit lines:
```bash
docker compose logs -f --tail=50 ray-head
```

## Reset Everything

To completely reset the stack (destroys all data):
```bash
docker compose down -v    # removes containers AND volumes
make up                   # fresh start
```
