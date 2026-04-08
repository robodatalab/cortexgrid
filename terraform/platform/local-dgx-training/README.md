# RoboLab ML Training Infrastructure

GPU-accelerated ML training infrastructure running on a DGX Spark, orchestrated from a MacBook over Tailscale. Designed for seamless expansion to AWS EC2 nodes with zero code changes.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Tailscale Network                           │
│                                                                     │
│  ┌──────────────┐         ┌─────────────────────────────────────┐  │
│  │  MacBook Pro  │         │         DGX Spark (128GB VRAM)      │  │
│  │              │  HTTP   │                                     │  │
│  │  submit.py  ─┼────────▶│  ┌─────────┐    ┌───────────────┐  │  │
│  │  monitor.py  │         │  │ Ray Head │◀──▶│    Redis       │  │  │
│  │              │         │  │  :8265   │    │ (GCS backend)  │  │  │
│  │  MLflow CLI  │         │  └────┬─────┘    └───────────────┘  │  │
│  │              │         │       │                              │  │
│  └──────────────┘         │       ▼ GPU tasks                   │  │
│                           │  ┌─────────┐    ┌───────────────┐  │  │
│  ┌──────────────┐         │  │ MLflow   │◀──▶│  PostgreSQL   │  │  │
│  │  AWS EC2     │  ray    │  │  :5000   │    │  (metadata)   │  │  │
│  │  (future)    │  start  │  └────┬─────┘    └───────────────┘  │  │
│  │  ─ ─ ─ ─ ─ ─┼────────▶│       │ artifacts                  │  │
│  │  Worker node │         │       ▼                              │  │
│  └──────────────┘         │  ┌─────────┐    ┌───────────────┐  │  │
│                           │  │  MinIO   │    │  Prometheus   │  │  │
│                           │  │  :9000   │    │    :9090      │  │  │
│                           │  └─────────┘    └───────┬───────┘  │  │
│                           │                         ▼           │  │
│                           │                 ┌───────────────┐  │  │
│                           │                 │   Grafana      │  │  │
│                           │                 │    :3000       │  │  │
│                           │                 └───────────────┘  │  │
│                           └─────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

| Machine    | Requirements                                           |
|-----------|--------------------------------------------------------|
| DGX Spark | Docker, Docker Compose V2, NVIDIA Container Toolkit, Tailscale |
| MacBook   | Python 3.11+, Tailscale                                |

Both machines must be on the same Tailscale network.

## Quickstart

### 1. Set up the DGX Spark

```bash
cd terraform/platform/local-dgx-training
bash scripts/setup-dgx.sh
```

### 2. Set up the Mac

```bash
cd terraform/platform/local-dgx-training
bash scripts/setup-mac.sh
```

### 3. Verify connectivity

```bash
make health
```

### 4. Submit your first job

```bash
make submit ARGS="--script jobs/examples/train_example.py --working-dir . --env pytorch --gpus 1 --name my-first-job --follow"
```

## Service URLs

| Service         | URL                              | Purpose                    |
|----------------|----------------------------------|----------------------------|
| Ray Dashboard  | `http://<DGX_IP>:8265`          | Job management, cluster view |
| MLflow UI      | `http://<DGX_IP>:5000`          | Experiment tracking         |
| Grafana        | `http://<DGX_IP>:3000`          | GPU/system monitoring       |
| MinIO Console  | `http://<DGX_IP>:9001`          | Artifact storage browser    |
| Prometheus     | `http://<DGX_IP>:9090`          | Raw metrics                 |

## Common Workflows

### Submit a training job

```bash
python jobs/submit.py \
    --script train.py \
    --working-dir ./experiments/my_experiment \
    --env transformers \
    --gpus 1 \
    --name "finetune-v1" \
    --mlflow-experiment "my-experiments" \
    --follow
```

### Resume a failed job

```bash
python jobs/examples/train_example.py --run-id <MLFLOW_RUN_ID>
```

### Monitor cluster status

```bash
make monitor
```

### Add an AWS EC2 node

```bash
bash scripts/add-aws-node.sh
# Then run the printed command on the EC2 instance
```

See [docs/adding-aws-nodes.md](docs/adding-aws-nodes.md) for the full guide.

## Make Targets

```
make up          # Start all services
make down        # Stop all services
make logs        # Tail service logs
make health      # Check all services
make submit      # Submit a job (pass ARGS="...")
make monitor     # Show job and cluster status
make setup-dgx   # One-time DGX setup
make setup-mac   # One-time Mac setup
```

## Documentation

- [Architecture](docs/architecture.md) — design decisions and data flow
- [Submitting Jobs](docs/submitting-jobs.md) — job submission reference
- [Adding AWS Nodes](docs/adding-aws-nodes.md) — scaling to AWS
- [Troubleshooting](docs/troubleshooting.md) — common issues and fixes
